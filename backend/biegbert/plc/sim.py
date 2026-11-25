"""SPS-Simulation: verhält sich nach außen wie GVL_HMI der echten SPS.

Nachgebildet nach docs/hmi-schnittstelle.md und docs/kinematik.md:
  - Handshake nCmdId/nAckId, jedes Kommando wird genau einmal geprüft
  - Heartbeat-Überwachung (tHeartbeatTimeout 500 ms), Alarm HmiHeartbeatLost
  - Betriebsarten nach G1 mit zweistufigen Kommandos (tTwoStepConfirmation 10 s)
  - Achsen mit Trapezprofil, Software-Endlagen, Tippen über stJog
  - Niederhalter mit Schaltzeit und Klemmdruck
  - Kinematik (Z1 Master, X/A Slaves) mit Plan aus dem Maschinenplanformat,
    Rotationen (G3), Halt/Fortsetzen, Ende mit Bestätigung (B4)
  - Alarme mit Quittierung und nChangeCounter
  - Fehlerinjektion (SimInjectFault) und Eingänge setzen (SimSetInput)

Die Simulation läuft in einem eigenen Thread mit 10 ms Zyklus, wie der PLC-Task.
Sie ist ein Testpartner für HMI und Backend, kein Ersatz für die SPS-Logik.
Verhalten, das in der SPS erst in Phase 8/9 entsteht (PlanLoad, SeqStart …),
ist hier vorweggenommen und im HMI als „nur Simulation“ gekennzeichnet.
"""
from __future__ import annotations

import copy
import math
import threading
import time
from bisect import bisect_right

from ..config import AXES, AXIS_ORDER, GEOMETRY, TIMING
from .base import PlcLink
from .hmi_types import (
    E_AlarmCode as AC, E_AlarmSeverity as SEV, E_Axis, E_AxisState, E_ClampState, E_CmdResult as R,
    E_HmiCmd as C, E_KinState as K, E_MotionMode, E_OperatingMode as M,
    ST_AlarmEntry, ST_HmiAlarms, ST_HmiCmd, ST_HmiParam, ST_HmiStatus, ST_Material,
)

CYCLE_S = 0.010
POS_TOL = 0.05
# nDetail-Codes für Ablehnungen, die keine Grundstellungsbits sind (in der SPS festzulegen)
DETAIL_AXES_NOT_READY = 0x10000      # + Bit je E_Axis: Achse nicht freigegeben oder gestört
DETAIL_Z1_BEHIND_START = 0x20000     # Z1 steht hinter dem Start der ersten Biegung


def _filetime_now() -> int:
    return int((time.time() + 11644473600) * 10_000_000)


class _Axis:
    def __init__(self, name: str):
        self.cfg = AXES[name]
        self.name = name
        self.enabled = False
        self.error = False
        self.error_id = 0
        self.pos = 0.0
        self.vel = 0.0
        self.mode = "idle"          # idle | move | jog | halt | coupled
        self.target = 0.0
        self.v_lim = 0.0
        self.set_pos = 0.0
        self.set_vel = 0.0
        self.torque = 0.0
        self.lag = 0.0

    def disp_pos(self) -> float:
        return self.pos % 360.0 if self.cfg.modulo else self.pos

    def stop(self):
        if self.mode in ("move", "jog"):
            self.mode = "halt"

    def step(self, dt: float):
        a = self.cfg.accel
        if not self.enabled or self.error:
            self.vel = 0.0
            self.mode = "idle" if self.mode != "coupled" else self.mode
            self.set_vel = 0.0
            return
        if self.mode == "coupled":
            return  # Position kommt von der Kinematik
        if self.mode == "move":
            d = self.target - self.pos
            if abs(d) < 1e-4 and abs(self.vel) < 1e-3:
                self.pos, self.vel, self.mode = self.target, 0.0, "idle"
            else:
                v_des = math.copysign(min(self.v_lim, math.sqrt(2 * a * abs(d))), d)
                self.vel += max(-a * dt, min(a * dt, v_des - self.vel))
                step = self.vel * dt
                if abs(step) >= abs(d) and math.copysign(1, step) == math.copysign(1, d):
                    self.pos, self.vel, self.mode = self.target, 0.0, "idle"
                else:
                    self.pos += step
            self.set_pos = self.target
        elif self.mode == "jog":
            self.vel += max(-a * dt, min(a * dt, self.v_lim - self.vel))
            self.pos += self.vel * dt
            self.set_pos = self.pos
        elif self.mode == "halt":
            if abs(self.vel) <= a * dt:
                self.vel, self.mode = 0.0, "idle"
            else:
                self.vel -= math.copysign(a * dt, self.vel)
                self.pos += self.vel * dt
            self.set_pos = self.pos
        else:
            self.vel = 0.0
        self.set_vel = self.vel


class _Clamp:
    def __init__(self):
        self.valve = False
        self.state = E_ClampState.Open
        self.t_switch = 0.0
        self.last_switch_ms = 0.0

    @property
    def is_open(self) -> bool:
        return self.state == E_ClampState.Open


class SimPlc(PlcLink):
    kind = "sim"

    def __init__(self):
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._run = False
        self.cmd = ST_HmiCmd()
        self.param = ST_HmiParam(stMaterial=ST_Material(15.0, 2.0, 300.0, 16000.0))
        self.status = ST_HmiStatus(bSimulation=True, bSafetyOk=True, eMode=M.Pause)
        self.status.stParamActive = copy.deepcopy(self.param)
        self.alarms = ST_HmiAlarms()
        self._alarm_list: list[ST_AlarmEntry] = []
        self.axes = {n: _Axis(n) for n in AXIS_ORDER}
        self.clamps = [_Clamp() for _ in range(6)]
        self._last_cmd_id = 0
        self._last_hb = 0              # Startwert von nHeartbeat; erst eine Änderung zählt als Lebenszeichen
        self._t_last_hb = -1e9
        self._hmi_alive = False
        self._pending_two_step: tuple[int, float] | None = None
        self._estop = False
        self._pressure_ok = True
        self._time = 0.0
        # Kinematik / Ablauf
        self._plans: dict[int, dict] = {}
        self._plan: dict | None = None
        self._material_ok = False
        self._kin = K.Idle
        self._kin_err = 0
        self._seq: dict | None = None
        self._step_g2 = 0
        self._step_g3 = 0
        self._bend_no = 0
        self._raise(AC.SimulationActive, SEV.Info)

    # ── PlcLink ─────────────────────────────────────────────────────────
    def connect(self):
        if self._thread:
            return
        self._run = True
        self._thread = threading.Thread(target=self._loop, name="SimPlc", daemon=True)
        self._thread.start()

    def close(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None

    def read_status(self) -> ST_HmiStatus:
        with self._lock:
            return copy.deepcopy(self.status)

    def read_alarms(self) -> ST_HmiAlarms:
        with self._lock:
            return copy.deepcopy(self.alarms)

    def write_cmd_fields(self, e_cmd, e_axis, n_clamp, f_position, f_velocity, n_value):
        with self._lock:
            c = self.cmd
            c.eCmd, c.eAxis, c.nClamp = int(e_cmd), int(e_axis), int(n_clamp)
            c.fPosition, c.fVelocity, c.nValue = float(f_position), float(f_velocity), int(n_value)

    def write_cmd_id(self, n_cmd_id):
        with self._lock:
            self.cmd.nCmdId = int(n_cmd_id)

    def write_heartbeat(self, n):
        with self._lock:
            self.cmd.nHeartbeat = int(n)

    def write_jog(self, e_axis, n_direction, f_factor_pct):
        with self._lock:
            j = self.cmd.stJog
            j.eAxis, j.nDirection, j.fVelocityFactor_pct = int(e_axis), int(n_direction), float(f_factor_pct)

    def write_param(self, p: ST_HmiParam):
        with self._lock:
            self.param = copy.deepcopy(p)

    def transfer_plan(self, plan: dict) -> None:
        """Übergabebereich der Phase 9, hier vorweggenommen."""
        with self._lock:
            self._plans[int(plan["plan_id"])] = copy.deepcopy(plan)

    # ── Zyklus ──────────────────────────────────────────────────────────
    def _loop(self):
        t_next = time.perf_counter()
        while self._run:
            with self._lock:
                self.cycle(CYCLE_S)
            t_next += CYCLE_S
            delay = t_next - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                t_next = time.perf_counter()

    def cycle(self, dt: float):
        """Ein SPS-Zyklus (öffentlich für Tests)."""
        self._time += dt
        st = self.status
        st.nLifeCounter = (st.nLifeCounter + 1) & 0xFFFFFFFF
        self._heartbeat()
        if self.cmd.nCmdId != self._last_cmd_id:
            self._last_cmd_id = self.cmd.nCmdId
            res, detail = self._command()
            st.stCmdAck.eResult, st.stCmdAck.nDetail = int(res), int(detail)
            st.stCmdAck.nAckId = self.cmd.nCmdId          # zuletzt
        self._jog()
        self._sequence(dt)
        for ax in self.axes.values():
            ax.step(dt)
        self._limits()
        self._clamps(dt)
        self._torques()
        self._fill_status()

    # ── Heartbeat ───────────────────────────────────────────────────────
    def _heartbeat(self):
        hb = self.cmd.nHeartbeat
        if hb != self._last_hb:
            self._last_hb, self._t_last_hb = hb, self._time
        alive = (self._time - self._t_last_hb) * 1000 < TIMING.plc_heartbeat_timeout_ms
        if self._hmi_alive and not alive:
            self._raise(AC.HmiHeartbeatLost, SEV.Stop)
            for ax in self.axes.values():
                ax.stop()
            self._to_pause()
        elif alive and not self._hmi_alive:
            self._clear(AC.HmiHeartbeatLost)
        self._hmi_alive = alive

    # ── Kommandoprüfung (genau einmal je nCmdId) ────────────────────────
    def _command(self) -> tuple[R, int]:
        c, mode = self.cmd, self.status.eMode
        try:
            cmd = C(c.eCmd)
        except ValueError:
            return R.RejectedUnknown, 0
        single = mode == M.SingleAxis
        ax = self.axes.get(E_Axis(c.eAxis).name) if c.eAxis in (1, 2, 3, 4, 5) else None

        if cmd == C.AlarmAck:
            self._ack(c.nValue)
            return R.Accepted, 0
        if cmd == C.ParamApply:
            if self._kin in (K.Coupling, K.Running, K.Holding, K.Held):
                return R.RejectedMode, 0
            p = self.param
            if not (0 <= p.fFeedOverride_pct <= 100 and 0 < p.fSingleAxisVelo_pct <= 100
                    and p.stMaterial.fDiameter_mm > 0 and p.stMaterial.fForceLimit_N > 0):
                return R.RejectedParam, 0
            act = copy.deepcopy(p)
            act.stMaterial.fForceLimit_N = min(act.stMaterial.fForceLimit_N, 25000.0)
            self.status.stParamActive = act
            return R.Accepted, 0

        if cmd in (C.AxisEnable, C.AxisDisable, C.AxisReset, C.AxisHalt, C.AxisMoveAbs):
            if ax is None:
                return R.RejectedParam, 0
            # Phase-4-Ausnahme: Enable und Halt im Simulationsbetrieb ohne Betriebsartenprüfung
            if cmd in (C.AxisEnable, C.AxisHalt):
                ok_mode = True
            elif cmd == C.AxisReset:
                ok_mode = mode in (M.SingleAxis, M.Pause)
            else:
                ok_mode = single
            if not ok_mode:
                return R.RejectedMode, 0
            if cmd == C.AxisEnable:
                if self._estop or not self.status.bSafetyOk:
                    return R.RejectedInterlock, int(AC.EStopActive)
                ax.enabled = True
                return R.Accepted, 0
            if cmd == C.AxisDisable:
                if ax.mode == "coupled":
                    return R.RejectedBusy, 0
                ax.enabled, ax.mode = False, "idle"
                return R.Accepted, 0
            if cmd == C.AxisReset:
                ax.error, ax.error_id = False, 0
                self._clear(AC.AxisError, int(E_Axis[ax.name]))
                self._clear(AC.AxisLagError, int(E_Axis[ax.name]))
                return R.Accepted, 0
            if cmd == C.AxisHalt:
                ax.stop()
                return R.Accepted, 0
            # AxisMoveAbs
            if not ax.enabled or ax.error:
                return R.RejectedInterlock, 1
            if ax.mode == "coupled":
                return R.RejectedBusy, 0
            v_max = ax.cfg.v_setup * self.status.stParamActive.fSingleAxisVelo_pct / 100.0
            if not (0 < c.fVelocity <= v_max + 1e-9):
                return R.RejectedParam, 2
            target = c.fPosition
            if ax.cfg.modulo:
                delta = ((target - ax.disp_pos() + 180.0) % 360.0) - 180.0
                target = ax.pos + delta
            elif not (ax.cfg.min_pos <= target <= ax.cfg.max_pos):
                return R.RejectedParam, 1
            if ax.name == "Z1":
                if target > GEOMETRY.z1_max(self.axes["Z2"].pos) + 1e-9:
                    return R.RejectedInterlock, int(AC.DistanceZ1Z2)
                blk = self._clamp_blocking(ax.pos, target)
                if blk:
                    return R.RejectedInterlock, int(AC.ClampZoneViolation)
            if ax.name == "Z2" and self.axes["Z1"].pos > GEOMETRY.z1_max(target) + 1e-9:
                return R.RejectedInterlock, int(AC.DistanceZ1Z2)
            ax.target, ax.v_lim, ax.mode = target, c.fVelocity, "move"
            return R.Accepted, 0

        if cmd in (C.ClampOpen, C.ClampClose):
            if not single:
                return R.RejectedMode, 0
            if not 1 <= c.nClamp <= 6:
                return R.RejectedParam, 0
            if cmd == C.ClampClose:
                pair = (c.nClamp + 1) // 2
                if self._chuck_in_zone(pair, self.axes["Z1"].pos):
                    return R.RejectedInterlock, int(AC.ClampZoneViolation)
            self._set_valve(c.nClamp - 1, cmd == C.ClampClose)
            return R.Accepted, 0

        if cmd == C.ModePause:
            if mode not in (M.Automatic, M.ReturnToBase):
                return R.RejectedMode, 0
            self._to_pause()
            return R.Accepted, 0
        if cmd in (C.ModeResume1, C.ModeSingleAxis1):
            if mode != M.Pause:
                return R.RejectedMode, 0
            self._pending_two_step = (int(cmd), self._time)
            return R.Accepted, 0
        if cmd in (C.ModeResume2, C.ModeSingleAxis2):
            if mode != M.Pause:
                return R.RejectedMode, 0
            first = C.ModeResume1 if cmd == C.ModeResume2 else C.ModeSingleAxis1
            p = self._pending_two_step
            self._pending_two_step = None
            if not p or p[0] != first or (self._time - p[1]) * 1000 > TIMING.two_step_ms:
                return R.RejectedSequence, 0
            if cmd == C.ModeResume2:
                if not self._hmi_alive or self._estop or self._fault_active():
                    return R.RejectedInterlock, 0
                self.status.eMode = M.Automatic
                if self._kin == K.Held and self._seq:
                    self._kin = K.Running
            else:
                if self._kin in (K.Coupling, K.Running, K.Holding, K.Held):
                    self._abort_kin()
                self.status.eMode = M.SingleAxis
            return R.Accepted, 0
        if cmd == C.ModeReturnToBase:
            if not single:
                return R.RejectedMode, 0
            missing = self._base_missing() & 0b111111
            if missing:
                return R.RejectedInterlock, missing
            self.status.eMode = M.Automatic
            return R.Accepted, 0

        if cmd == C.SimSetInput:
            if c.nValue == 1:
                self._pressure_ok = c.fPosition != 0
            elif c.nValue == 2:
                self._set_estop(c.fPosition != 0)
            else:
                return R.RejectedParam, 0
            return R.Accepted, 0
        if cmd == C.SimInjectFault:
            return self._inject(c.nValue)

        if cmd == C.PlanLoad:
            if self._kin not in (K.Idle, K.Loaded):
                return R.RejectedBusy, 0
            if mode not in (M.Automatic, M.Pause, M.SingleAxis):
                return R.RejectedMode, 0
            plan = self._plans.get(c.nValue)
            if not plan:
                return R.RejectedParam, 1
            err = self._check_plan(plan)
            if err:
                return R.RejectedParam, err
            self._plan, self._kin, self._material_ok = plan, K.Loaded, False
            return R.Accepted, 0
        if cmd == C.MaterialConfirm:
            if not self._plan or c.nValue != int(self._plan["plan_id"]):
                return R.RejectedParam, 0
            self._material_ok = True
            return R.Accepted, 0
        if cmd == C.SeqStart:
            if mode != M.Automatic:
                return R.RejectedMode, 0
            if self._kin != K.Loaded or not self._plan:
                return R.RejectedInterlock, 1 << 8
            if not self._material_ok:
                return R.RejectedInterlock, 1 << 9
            if self._estop:
                return R.RejectedInterlock, int(AC.EStopActive)
            if not self._hmi_alive:
                return R.RejectedInterlock, int(AC.HmiHeartbeatLost)
            not_ready = [n for n, a in self.axes.items() if not a.enabled or a.error]
            if not_ready:   # Detail: 0x10000 + Bit je E_Axis
                return R.RejectedInterlock, DETAIL_AXES_NOT_READY | sum(1 << int(E_Axis[n]) for n in not_ready)
            if self._fault_active():
                return R.RejectedInterlock, 1 << 6
            if self.axes["Z1"].pos > self._plan["bends"][0]["z1_start_mm"] + POS_TOL:
                return R.RejectedInterlock, DETAIL_Z1_BEHIND_START
            self._seq = {"bend": 0, "rot": 0, "phase": "start"}
            self._step_g2 = 20
            return R.Accepted, 0
        if cmd in (C.SeqConfirmQ1, C.SeqConfirmQ2):
            if self._step_g2 != 24 or cmd != C.SeqConfirmQ1:
                return R.RejectedSequence, 0
            self._seq["phase"] = "release"
            self._step_g2 = 25
            return R.Accepted, 0
        if cmd in (C.SeqPartRemoved, C.CalibrateAxis):
            return R.RejectedUnknown, 0
        return R.RejectedUnknown, 0

    # ── Tippen ──────────────────────────────────────────────────────────
    def _jog(self):
        j = self.cmd.stJog
        ok = (self.status.eMode == M.SingleAxis and self._hmi_alive and j.nDirection in (-1, 1)
              and j.eAxis in (1, 2, 3, 4, 5))
        for n, ax in self.axes.items():
            active = ok and E_Axis[n] == j.eAxis
            if active and ax.enabled and not ax.error and ax.mode in ("idle", "jog", "halt"):
                f = max(0.0, min(100.0, j.fVelocityFactor_pct)) / 100.0
                f *= self.status.stParamActive.fSingleAxisVelo_pct / 100.0
                ax.mode, ax.v_lim = "jog", j.nDirection * ax.cfg.v_setup * f
            elif ax.mode == "jog":
                ax.stop()

    # ── Grenzen und Verriegelungen ──────────────────────────────────────
    def _limits(self):
        for n, ax in self.axes.items():
            if ax.cfg.modulo or ax.mode == "coupled":
                continue
            if ax.pos > ax.cfg.max_pos or ax.pos < ax.cfg.min_pos:
                ax.pos = min(ax.cfg.max_pos, max(ax.cfg.min_pos, ax.pos))
                ax.vel, ax.mode = 0.0, "idle"
                self._raise(AC.AxisSoftLimit, SEV.Warning, int(E_Axis[n]))
            elif self._has(AC.AxisSoftLimit, int(E_Axis[n])):
                self._clear(AC.AxisSoftLimit, int(E_Axis[n]))
        z1, z2 = self.axes["Z1"], self.axes["Z2"]
        zmax = GEOMETRY.z1_max(z2.pos)
        if z1.pos > zmax:
            z1.pos, z1.vel = zmax, 0.0
            if z1.mode != "coupled":
                z1.mode = "idle"
            else:
                self._kin_fault(AC.DistanceZ1Z2)
            self._raise(AC.DistanceZ1Z2, SEV.Stop, 1)
        elif self._has(AC.DistanceZ1Z2, 1) and z1.pos < zmax - 1:
            self._clear(AC.DistanceZ1Z2, 1)
        # Futter läuft auf geschlossenen Niederhalter
        for pair in (1, 2, 3):
            if self._chuck_in_zone(pair, z1.pos):
                for k in (2 * pair - 2, 2 * pair - 1):
                    if not self.clamps[k].is_open:
                        if abs(z1.vel) > 0 or z1.mode == "coupled":
                            if z1.mode == "coupled":
                                self._kin_fault(AC.ClampZoneViolation)
                            z1.vel, z1.mode = 0.0, "idle" if z1.mode != "coupled" else "coupled"
                        self._raise(AC.ClampZoneViolation, SEV.Fault, k + 1)

    def _chuck_in_zone(self, pair: int, z1: float) -> bool:
        lo, hi = GEOMETRY.clamp_zone_z1(pair)
        return lo < z1 < hi

    def _clamp_blocking(self, z_from: float, z_to: float) -> int:
        a, b = sorted((z_from, z_to))
        for pair in (1, 2, 3):
            lo, hi = GEOMETRY.clamp_zone_z1(pair)
            if b > lo and a < hi and not all(self.clamps[k].is_open for k in (2 * pair - 2, 2 * pair - 1)):
                return pair
        return 0

    # ── Niederhalter ────────────────────────────────────────────────────
    def _set_valve(self, i: int, on: bool):
        c = self.clamps[i]
        if c.valve == on:
            return
        c.valve = on
        c.t_switch = 0.0
        c.state = E_ClampState.Closing if on else E_ClampState.Opening

    def _clamps(self, dt: float):
        t_sw = GEOMETRY.clamp_switch_time_ms
        for i, c in enumerate(self.clamps):
            if self._estop and c.valve:   # Entlüftung bei Not-Halt
                c.valve, c.state, c.t_switch = False, E_ClampState.Opening, 0.0
            if c.state in (E_ClampState.Closing, E_ClampState.Opening):
                c.t_switch += dt * 1000
                if c.t_switch >= t_sw:
                    c.last_switch_ms = c.t_switch
                    if c.state == E_ClampState.Opening:
                        c.state = E_ClampState.Open
                    elif self._pressure_ok:
                        c.state = E_ClampState.Clamped
                    elif c.t_switch > 3 * t_sw:
                        c.state = E_ClampState.Fault
                        self._raise(AC.ClampTimeout, SEV.Fault, i + 1)
            if c.state == E_ClampState.Clamped and not self._pressure_ok:
                c.state = E_ClampState.Fault
                self._raise(AC.ClampPressureLow, SEV.Fault, i + 1)
        if self._pressure_ok and self._has(AC.ClampPressureLow):
            for i in range(6):
                self._clear(AC.ClampPressureLow, i + 1)
        for i, c in enumerate(self.clamps):
            if self._has(AC.ClampZoneViolation, i + 1) and (c.is_open or not any(
                    self._chuck_in_zone(p, self.axes["Z1"].pos) for p in (1, 2, 3))):
                self._clear(AC.ClampZoneViolation, i + 1)
            if c.state == E_ClampState.Fault and not c.valve:
                c.state = E_ClampState.Opening

    def _torques(self):
        """Grobe Momentschätzung für die Anzeige (Simulationswert)."""
        z1, x = self.axes["Z1"], self.axes["X"]
        moving = abs(z1.vel) > 1e-3
        force = (1500.0 + 1200.0 * abs(x.pos)) if moving else 0.0     # N, grob
        z1.torque = force * 0.007 / (2 * math.pi * 0.42 * 4) * (1 if z1.vel >= 0 else -1)
        x.torque = 0.08 * x.pos * (1 if moving else 0.2)
        for n in ("Z2", "A", "W"):
            ax = self.axes[n]
            ax.torque = 0.05 * math.copysign(1, ax.vel) if abs(ax.vel) > 1e-3 else 0.0

    # ── Kinematik und Ablauf G2/G3 ──────────────────────────────────────
    def _check_plan(self, plan: dict) -> int:
        """Prüfung beim Laden (vereinfacht). Rückgabe 0 = ok, sonst Detailcode."""
        try:
            last_s = -1e9
            for b in plan["bends"]:
                if not b["z1_start_mm"] < b["z1_fin_mm"]:
                    return 10
                if b["points"] and b["points"][0]["s_mm"] < last_s:
                    return 11          # Biegungen überlappen
                prev = -1e9
                for p in b["points"]:
                    if p["s_mm"] <= prev:
                        return 11      # s innerhalb der Biegung nicht streng monoton
                    prev = p["s_mm"]
                    if abs(p["u_mm"]) > AXES["X"].max_pos or abs(p["phi_deg"]) > AXES["A"].max_pos:
                        return 12
                last_s = prev
                if not AXES["Z2"].min_pos <= b.get("z2_mm", 0.0) <= AXES["Z2"].max_pos:
                    return 13
                if b["z1_fin_mm"] > GEOMETRY.z1_max(b.get("z2_mm", 0.0)):
                    return 14
        except (KeyError, TypeError):
            return 2
        return 0

    @staticmethod
    def _interp(points, key, s):
        ss = [p["s_mm"] for p in points]
        if s <= ss[0]:
            return points[0][key]
        if s >= ss[-1]:
            return points[-1][key]
        i = bisect_right(ss, s) - 1
        p0, p1 = points[i], points[i + 1]
        t = (s - p0["s_mm"]) / (p1["s_mm"] - p0["s_mm"])
        return p0[key] + t * (p1[key] - p0[key])

    def _couple(self, on: bool):
        for n in ("Z1", "X", "A"):
            ax = self.axes[n]
            if on:
                ax.mode, ax.vel = "coupled", 0.0
            elif ax.mode == "coupled":
                ax.mode, ax.vel = "idle", 0.0

    def _abort_kin(self):
        self._couple(False)
        self._seq = None
        self._step_g2 = self._step_g3 = 0
        self._kin = K.Loaded if self._plan else K.Idle

    def _kin_fault(self, code):
        if self._kin in (K.Coupling, K.Running, K.Holding, K.Held):
            self._couple(False)
            self._kin, self._kin_err = K.Fault, int(code)
            self._to_pause()

    def _to_pause(self):
        if self.status.eMode in (M.Automatic, M.ReturnToBase):
            self.status.eMode = M.Pause
        if self._kin in (K.Running, K.Coupling):
            self._kin = K.Holding

    def _feed(self, bend) -> float:
        v = float(bend.get("feed_mm_s", 30.0))
        return min(v, AXES["Z1"].v_max) * self.status.stParamActive.fFeedOverride_pct / 100.0

    def _sequence(self, dt: float):
        if not self._seq or not self._plan:
            if self._kin == K.Holding:
                self._kin = K.Held
            return
        sq, plan = self._seq, self._plan
        bends = plan["bends"]
        z1, x, a, z2, w = (self.axes[n] for n in ("Z1", "X", "A", "Z2", "W"))
        if sq["phase"] in ("start", "rotate", "approach") and self.status.eMode != M.Automatic:
            return   # nicht gekoppelte Schritte ruhen in der Pause
        if self._kin == K.Fault:
            return
        b = bends[sq["bend"]] if sq["bend"] < len(bends) else None

        if sq["phase"] == "start":                       # S20: Startposition anfahren (B1)
            self._bend_no = sq["bend"] + 1
            tgt = {"Z2": b.get("z2_mm", 0.0), "X": 0.0, "A": 0.0, "Z1": b["z1_start_mm"]}
            busy = False
            for n, t in tgt.items():
                ax = self.axes[n]
                if abs(ax.pos - t) > POS_TOL:
                    if ax.mode != "move" or abs(ax.target - t) > 1e-9:
                        ax.target, ax.v_lim, ax.mode = t, ax.cfg.v_setup, "move"
                    busy = True
                elif ax.mode == "idle":
                    ax.pos = t
            if not busy:
                sq["phase"], self._step_g2 = "clamp", 21
                for k in b.get("clamps", []):
                    if not self._chuck_in_zone((k + 1) // 2, z1.pos):
                        self._set_valve(k - 1, True)
            return
        if sq["phase"] == "clamp":                       # S21: Niederhalter schließen, Klemmung prüfen (B2)
            need = [k for k in b.get("clamps", []) if not self._chuck_in_zone((k + 1) // 2, z1.pos)]
            if all(self.clamps[k - 1].state == E_ClampState.Clamped for k in need):
                sq["phase"], self._step_g2, self._kin = "run", 23, K.Coupling
                self._couple(True)
            return
        if sq["phase"] == "run":                         # S22/S23: gekoppelt biegen (B3)
            if self._kin == K.Coupling:
                self._kin = K.Running
            v_set = self._feed(b) if self._kin == K.Running else 0.0
            if self._kin == K.Holding or self._kin == K.Held:
                v_set = 0.0
            acc = AXES["Z1"].accel
            z1.vel += max(-acc * dt, min(acc * dt, v_set - z1.vel))
            if self._kin == K.Holding and abs(z1.vel) < 1e-6:
                self._kin = K.Held
            rots = b.get("rotations", [])
            nxt_rot = rots[sq["rot"]] if sq["rot"] < len(rots) else None
            stop_at = min(b["z1_fin_mm"], nxt_rot["z1_mm"] if nxt_rot else 1e9)
            new = z1.pos + z1.vel * dt
            if new >= stop_at:
                new, z1.vel = stop_at, 0.0
            vx_prev, va_prev = x.pos, a.pos
            z1.pos = z1.set_pos = new
            x.pos = x.set_pos = self._interp(b["points"], "u_mm", new)
            a.pos = a.set_pos = self._interp(b["points"], "phi_deg", new)
            if dt > 0:
                x.vel, a.vel = (x.pos - vx_prev) / dt, (a.pos - va_prev) / dt
                if abs(x.vel) > AXES["X"].v_max * 1.05:
                    self._raise(AC.AxisLagError, SEV.Warning, int(E_Axis.X), 1)
            z1.set_vel = z1.vel
            for i, zo in enumerate(b.get("clamp_open_mm", [])):         # positionsabhängig öffnen
                k = b["clamps"][i] if i < len(b.get("clamps", [])) else i + 1
                if new >= zo and self.clamps[k - 1].valve:
                    self._set_valve(k - 1, False)
            if nxt_rot and new >= nxt_rot["z1_mm"] - 1e-9:
                sq["phase"], self._kin, self._step_g3 = "rotate", K.Held, 30
                sq["w_target"] = w.pos + float(nxt_rot["w_deg"])
                return
            if new >= b["z1_fin_mm"] - 1e-9:
                x.vel = a.vel = 0.0
                if sq["bend"] + 1 < len(bends):
                    self._couple(False)
                    sq["bend"] += 1
                    sq["rot"] = 0
                    sq["phase"], self._step_g2, self._kin = "start", 20, K.Loaded
                else:
                    sq["phase"], self._step_g2, self._kin = "end", 24, K.Held
            return
        if sq["phase"] == "rotate":                      # G3: Rotation, alle anderen Achsen halten
            if w.mode == "idle" and abs(w.pos - sq["w_target"]) > 1e-6:
                w.target, w.v_lim, w.mode = sq["w_target"], AXES["W"].v_setup, "move"
                self._step_g3 = 31
            elif w.mode == "idle":
                sq["rot"] += 1
                sq["phase"], self._kin, self._step_g3 = "run", K.Running, 0
            return
        if sq["phase"] == "end":                         # S24: Geometrie unter Last halten, Bestätigung Q1 (B4)
            return
        if sq["phase"] == "release":                     # S25: Niederhalter lösen, Biegekopf zurück
            self._couple(False)
            for i in range(6):
                self._set_valve(i, False)
            done = True
            for n in ("X", "A"):
                ax = self.axes[n]
                if abs(ax.pos) > POS_TOL:
                    if ax.mode != "move":
                        ax.target, ax.v_lim, ax.mode = 0.0, ax.cfg.v_setup, "move"
                    done = False
            if done and all(c.is_open for c in self.clamps):
                self._seq = None
                self._step_g2, self._kin = 0, K.Loaded
                self._material_ok = False

    # ── Fehler und Not-Halt ─────────────────────────────────────────────
    def _inject(self, fid: int):
        if fid in (1, 2, 3):
            n = {1: "Z1", 2: "X", 3: "A"}[fid]
            ax = self.axes[n]
            ax.error, ax.error_id = True, 0x4260
            self._raise(AC.AxisError, SEV.Fault, int(E_Axis[n]), 0x4260)
            if ax.mode == "coupled":
                self._kin_fault(AC.KinMasterFault if n == "Z1" else AC.KinSlaveFault)
                self._raise(AC.KinMasterFault if n == "Z1" else AC.KinSlaveFault, SEV.Fault)
            return R.Accepted, 0
        if fid == 4:
            self._pressure_ok = False
            return R.Accepted, 0
        if fid == 5:
            self._set_estop(True)
            return R.Accepted, 0
        if fid == 6:
            self._raise(AC.AxisLagError, SEV.Warning, int(E_Axis.Z1), 123)
            return R.Accepted, 0
        return R.RejectedParam, 0

    def _set_estop(self, on: bool):
        self._estop = on
        self.status.bEStopActive = on
        self.status.bSafetyOk = not on
        if on:
            for ax in self.axes.values():
                ax.enabled, ax.vel = False, 0.0
                if ax.mode == "coupled":
                    ax.mode = "idle"
            if self._kin in (K.Coupling, K.Running, K.Holding, K.Held):
                self._kin, self._kin_err = K.Fault, int(AC.EStopActive)
            self._to_pause()
            self._raise(AC.EStopActive, SEV.Fault)
            self._raise(AC.SafetyNotReady, SEV.Fault)
        else:
            self._clear(AC.EStopActive)
            self._clear(AC.SafetyNotReady)

    def _fault_active(self) -> bool:
        return any(e.bActive and e.eSeverity >= SEV.Fault for e in self._alarm_list) or \
            any(ax.error for ax in self.axes.values())

    # ── Alarme ──────────────────────────────────────────────────────────
    def _find(self, code, source=0):
        for e in self._alarm_list:
            if e.eCode == int(code) and e.nSource == int(source):
                return e
        return None

    def _has(self, code, source=None) -> bool:
        return any(e.eCode == int(code) and e.bActive and (source is None or e.nSource == source)
                   for e in self._alarm_list)

    def _raise(self, code, sev, source=0, detail=0):
        e = self._find(code, source)
        if e and e.bActive:
            return
        if e is None:
            if len(self._alarm_list) >= 32:
                self.alarms.bOverflow = True
                return
            e = ST_AlarmEntry(int(code), int(sev), int(source), int(detail))
            self._alarm_list.append(e)
        e.eSeverity, e.nDetail, e.nTimestamp, e.bActive, e.bAcked = int(sev), int(detail), _filetime_now(), True, False
        self.alarms.nChangeCounter += 1
        if sev >= SEV.Stop:
            self._to_pause()
            if self._kin in (K.Running, K.Coupling):
                self._kin = K.Holding

    def _clear(self, code, source=None):
        changed = False
        for e in self._alarm_list:
            if e.eCode == int(code) and (source is None or e.nSource == source) and e.bActive:
                e.bActive, changed = False, True
        if changed:
            self.alarms.nChangeCounter += 1
            self._purge()

    def _ack(self, slot: int):
        for i, e in enumerate(self._alarm_list):
            if slot in (0, i + 1):
                e.bAcked = True
        if self._kin == K.Fault and not self._fault_active():
            self._kin = K.Loaded if self._plan else K.Idle
            self._seq = None
            self._step_g2 = self._step_g3 = 0
        self.alarms.nChangeCounter += 1
        self._purge()

    def _purge(self):
        self._alarm_list = [e for e in self._alarm_list if e.bActive or not e.bAcked]

    # ── Status ──────────────────────────────────────────────────────────
    def _base_missing(self) -> int:
        bits = 0
        for i, n in enumerate(AXIS_ORDER):
            ax = self.axes[n]
            p = ((ax.disp_pos() + 180) % 360) - 180 if ax.cfg.modulo else ax.pos
            if abs(p) > POS_TOL:
                bits |= 1 << i
        if not all(c.is_open for c in self.clamps):
            bits |= 1 << 5
        if self._fault_active():
            bits |= 1 << 6
        if not self.status.bSafetyOk:
            bits |= 1 << 7
        if not self._plan:
            bits |= 1 << 8
        if not self._material_ok:
            bits |= 1 << 9
        return bits

    def _fill_status(self):
        st = self.status
        st.bHmiAlive = self._hmi_alive
        st.nStepG2, st.nStepG3, st.nBend = self._step_g2, self._step_g3, self._bend_no
        st.dwBaseStateMissing = self._base_missing()
        for i, n in enumerate(AXIS_ORDER):
            ax, s = self.axes[n], st.aAxis[i]
            s.bEnabled, s.bError, s.nErrorId = ax.enabled, ax.error, ax.error_id
            s.bReady = ax.enabled and not ax.error
            if ax.error:
                s.eState = E_AxisState.Error
            elif not ax.enabled:
                s.eState = E_AxisState.Disabled
            elif ax.mode == "coupled":
                s.eState = E_AxisState.Coupled
            elif ax.mode in ("move", "jog", "halt") or abs(ax.vel) > 1e-6:
                s.eState = E_AxisState.Moving
            else:
                s.eState = E_AxisState.Standstill
            s.fActPos = ax.disp_pos()
            s.fSetPos = (ax.set_pos % 360.0) if ax.cfg.modulo else ax.set_pos
            s.fActVelo, s.fSetVelo = ax.vel, ax.set_vel
            s.fLagError, s.fTorque_Nm = ax.lag, ax.torque
        for i, c in enumerate(self.clamps):
            s = st.aClamp[i]
            s.eState, s.bValve, s.bOpen, s.fLastSwitchTime_ms = int(c.state), c.valve, c.is_open, c.last_switch_ms
        st.stIo.bPressureOk = self._pressure_ok
        k = st.stKin
        k.eState = int(self._kin)
        k.eMode = int(E_MotionMode.Coupled if self._kin in (K.Coupling, K.Running) else
                      E_MotionMode.Hold if self._kin in (K.Holding, K.Held) else E_MotionMode.Single)
        k.nPlanId = int(self._plan["plan_id"]) if self._plan else 0
        k.bError, k.nErrorId = self._kin == K.Fault, self._kin_err if self._kin == K.Fault else 0
        k.fMasterPos_mm = self.axes["Z1"].pos
        al = self.alarms
        al.nCount = len(self._alarm_list)
        al.eHighestSeverity = max((e.eSeverity for e in self._alarm_list), default=0)
        al.aEntry = [copy.copy(e) for e in self._alarm_list] + [ST_AlarmEntry() for _ in range(32 - len(self._alarm_list))]
