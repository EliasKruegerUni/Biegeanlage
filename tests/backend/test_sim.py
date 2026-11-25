"""SPS-Simulation gegen die Regeln aus docs/hmi-schnittstelle.md."""
import pytest

from biegbert.plan import build, geo_from_json
from biegbert.plc.hmi_types import E_AlarmCode as AC, E_CmdResult as R, E_HmiCmd as C, E_KinState as K, \
    E_OperatingMode as M
from biegbert.plc.sim import SimPlc


class Rig:
    """Treibt die Simulation zyklisch und spielt Backend (Heartbeat, Handshake)."""

    def __init__(self):
        self.p = SimPlc()
        self.hb = 0
        self.cid = 0
        self.t = 0

    def run(self, n, heartbeat=True):
        for _ in range(n):
            self.t += 1
            if heartbeat and self.t % 10 == 0:
                self.hb += 1
                self.p.write_heartbeat(self.hb)
            self.p.cycle(0.01)

    def cmd(self, c, axis=0, clamp=0, pos=0.0, vel=0.0, value=0):
        self.p.write_cmd_fields(int(c), axis, clamp, pos, vel, value)
        self.cid += 1
        self.p.write_cmd_id(self.cid)
        self.run(1)
        a = self.p.read_status().stCmdAck
        assert a.nAckId == self.cid
        return R(a.eResult), a.nDetail

    def single_axis(self):
        self.run(20)
        assert self.cmd(C.ModeSingleAxis1)[0] == R.Accepted
        assert self.cmd(C.ModeSingleAxis2)[0] == R.Accepted
        for ax in range(1, 6):
            assert self.cmd(C.AxisEnable, ax)[0] == R.Accepted


@pytest.fixture
def rig():
    return Rig()


def test_command_checked_once(rig):
    rig.run(20)
    assert rig.cmd(C.ModeSingleAxis1)[0] == R.Accepted
    rig.run(5)                      # gleiche nCmdId: keine erneute Prüfung
    assert rig.p.read_status().stCmdAck.nAckId == rig.cid


def test_two_step_and_sequence(rig):
    rig.run(20)
    assert rig.cmd(C.ModeSingleAxis2)[0] == R.RejectedSequence
    assert rig.cmd(C.ModeSingleAxis1)[0] == R.Accepted
    assert rig.cmd(C.ModeSingleAxis2)[0] == R.Accepted
    assert rig.p.read_status().eMode == M.SingleAxis


def test_move_requires_single_axis_mode(rig):
    rig.run(20)
    rig.cmd(C.AxisEnable, 1)        # Phase-4-Ausnahme
    assert rig.cmd(C.AxisMoveAbs, 1, pos=10, vel=10)[0] == R.RejectedMode


def test_move_and_limits(rig):
    rig.single_axis()
    assert rig.cmd(C.AxisMoveAbs, 3, pos=60, vel=1)[0] == R.RejectedParam      # außerhalb ±50
    assert rig.cmd(C.AxisMoveAbs, 3, pos=5, vel=99)[0] == R.RejectedParam      # zu schnell
    assert rig.cmd(C.AxisMoveAbs, 3, pos=5, vel=3.5)[0] == R.Accepted
    rig.run(300)
    assert rig.p.read_status().aAxis[2].fActPos == pytest.approx(5.0, abs=1e-3)
    res, det = rig.cmd(C.AxisMoveAbs, 1, pos=900, vel=30)
    assert res == R.RejectedInterlock and det == AC.DistanceZ1Z2


def test_clamp_zone_interlock(rig):
    rig.single_axis()
    assert rig.cmd(C.ClampClose, clamp=1)[0] == R.Accepted
    rig.run(30)
    res, det = rig.cmd(C.AxisMoveAbs, 1, pos=600, vel=30)
    assert res == R.RejectedInterlock and det == AC.ClampZoneViolation


def test_heartbeat_loss_stops_and_alarms(rig):
    rig.single_axis()
    rig.p.write_jog(3, 1, 100)
    rig.run(20)
    assert abs(rig.p.read_status().aAxis[2].fActVelo) > 0
    rig.run(80, heartbeat=False)    # > 500 ms ohne Heartbeat
    st = rig.p.read_status()
    assert not st.bHmiAlive and st.aAxis[2].fActVelo == 0
    assert any(e.eCode == AC.HmiHeartbeatLost and e.bActive for e in rig.p.read_alarms().aEntry)


def test_seqstart_names_missing_axes(rig):
    rig.run(20)
    rig.cmd(C.ModeResume1); rig.cmd(C.ModeResume2)              # Automatik ohne Achsfreigabe
    g = geo_from_json({"format": "biegbert-geo", "version": 1, "elements": [{"type": "straight", "length_mm": 20}]})
    m, _ = build(g, 9, 0.0)
    rig.p.transfer_plan(m)
    assert rig.cmd(C.PlanLoad, value=9)[0] == R.Accepted
    assert rig.cmd(C.MaterialConfirm, value=9)[0] == R.Accepted
    res, det = rig.cmd(C.SeqStart)
    assert res == R.RejectedInterlock and det == 0x10000 | 0b111110
    from biegbert.alarms import detail_text
    assert detail_text(res, det) == "Achse nicht freigegeben oder gestört: Z1, Z2, X, A, W"


def test_plan_runs_to_end(rig):
    rig.single_axis()
    assert rig.cmd(C.ModeReturnToBase)[0] == R.Accepted
    g = geo_from_json({"format": "biegbert-geo", "version": 1, "process": {"transition_mm": 30},
                       "elements": [{"type": "straight", "length_mm": 40},
                                    {"type": "bend", "angle_deg": 45, "radius_mm": 50},
                                    {"type": "straight", "length_mm": 40}, {"type": "rotation", "angle_deg": 90},
                                    {"type": "bend", "angle_deg": 30, "radius_mm": 60},
                                    {"type": "straight", "length_mm": 30}]})
    m, rep = build(g, 3, 0.0)
    assert m
    rig.p.transfer_plan(m)
    assert rig.cmd(C.SeqStart)[0] == R.RejectedInterlock          # kein Plan geladen
    assert rig.cmd(C.PlanLoad, value=3)[0] == R.Accepted
    assert rig.cmd(C.SeqStart)[0] == R.RejectedInterlock          # Material nicht bestätigt
    assert rig.cmd(C.MaterialConfirm, value=3)[0] == R.Accepted
    assert rig.cmd(C.SeqStart)[0] == R.Accepted
    for _ in range(400):
        rig.run(10)
        if rig.p.read_status().nStepG2 == 24:
            break
    st = rig.p.read_status()
    assert st.nStepG2 == 24 and st.stKin.eState == K.Held
    assert st.aAxis[0].fActPos == pytest.approx(m["bends"][-1]["z1_fin_mm"], abs=0.01)
    assert st.aAxis[4].fActPos == pytest.approx(90.0, abs=0.01)   # Rotation W ausgeführt
    assert rig.cmd(C.SeqConfirmQ1)[0] == R.Accepted
    rig.run(300)
    st = rig.p.read_status()
    assert st.nStepG2 == 0 and st.stKin.eState == K.Loaded
    assert all(c.bOpen for c in st.aClamp)
