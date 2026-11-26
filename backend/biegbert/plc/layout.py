"""Byte-Layout der HMI-Strukturen (pack_mode := '1', also ohne Füllbytes).

Damit liest das ADS-Backend ganze Strukturen als Bytefolge und zerlegt sie
selbst. Ändert sich eine Struktur in der SPS, muss sie hier gleich geändert
werden; tests/backend/test_layout.py prüft die Größen.

TwinCAT-Typen → struct-Formate (Little Endian):
BOOL ?  SINT b  USINT B  INT h  UINT H  DINT i  UDINT/DWORD I  ULINT Q  LREAL d
"""
from __future__ import annotations

import struct

from .hmi_types import (
    ST_AlarmEntry, ST_HmiAlarms, ST_HmiAxisStatus, ST_HmiClampStatus, ST_HmiCmdAck,
    ST_HmiIoStatus, ST_HmiParam, ST_HmiStatus, ST_KinStatus, ST_Material,
)

F_CMD_ACK = "<IhI"
F_AXIS = "<h???Idddddd"
F_CLAMP = "<h??d"
F_IO = "<?" + "?" * 16 + "?" * 16
F_KIN = "<hhI?Id"
F_MATERIAL = "<dddd"
F_PARAM = "<I" + F_MATERIAL[1:] + "dd"
F_STATUS_HEAD = "<I????hhhII"
F_ALARM_ENTRY = "<IhiIQ??"
F_ALARMS_HEAD = "<IH?h"

SIZE_CMD_ACK = struct.calcsize(F_CMD_ACK)
SIZE_AXIS = struct.calcsize(F_AXIS)
SIZE_CLAMP = struct.calcsize(F_CLAMP)
SIZE_IO = struct.calcsize(F_IO)
SIZE_KIN = struct.calcsize(F_KIN)
SIZE_PARAM = struct.calcsize(F_PARAM)
SIZE_STATUS = (struct.calcsize(F_STATUS_HEAD) + SIZE_CMD_ACK + 5 * SIZE_AXIS + 6 * SIZE_CLAMP
               + SIZE_IO + SIZE_KIN + SIZE_PARAM)
SIZE_ALARM_ENTRY = struct.calcsize(F_ALARM_ENTRY)
SIZE_ALARMS = struct.calcsize(F_ALARMS_HEAD) + 32 * SIZE_ALARM_ENTRY


class _Reader:
    def __init__(self, data: bytes):
        self.b, self.o = data, 0

    def take(self, fmt: str):
        v = struct.unpack_from(fmt, self.b, self.o)
        self.o += struct.calcsize(fmt)
        return v


def unpack_status(data: bytes) -> ST_HmiStatus:
    if len(data) != SIZE_STATUS:
        raise ValueError(f"ST_HmiStatus: {len(data)} Bytes, erwartet {SIZE_STATUS}")
    r = _Reader(data)
    h = r.take(F_STATUS_HEAD)
    st = ST_HmiStatus(nLifeCounter=h[0], bHmiAlive=h[1], bSimulation=h[2], bEStopActive=h[3], bSafetyOk=h[4],
                      eMode=h[5], nStepG2=h[6], nStepG3=h[7], nBend=h[8], dwBaseStateMissing=h[9])
    st.stCmdAck = ST_HmiCmdAck(*r.take(F_CMD_ACK))
    st.aAxis = [ST_HmiAxisStatus(*r.take(F_AXIS)) for _ in range(5)]
    st.aClamp = [ST_HmiClampStatus(*r.take(F_CLAMP)) for _ in range(6)]
    io = r.take(F_IO)
    st.stIo = ST_HmiIoStatus(io[0], list(io[1:17]), list(io[17:33]))
    st.stKin = ST_KinStatus(*r.take(F_KIN))
    p = r.take(F_PARAM)
    st.stParamActive = ST_HmiParam(p[0], ST_Material(*p[1:5]), p[5], p[6])
    return st


def pack_status(st: ST_HmiStatus) -> bytes:
    """Gegenstück zu unpack_status (für Tests und Simulation)."""
    out = [struct.pack(F_STATUS_HEAD, st.nLifeCounter, st.bHmiAlive, st.bSimulation, st.bEStopActive, st.bSafetyOk,
                       st.eMode, st.nStepG2, st.nStepG3, st.nBend, st.dwBaseStateMissing),
           struct.pack(F_CMD_ACK, st.stCmdAck.nAckId, st.stCmdAck.eResult, st.stCmdAck.nDetail)]
    for a in st.aAxis:
        out.append(struct.pack(F_AXIS, a.eState, a.bEnabled, a.bReady, a.bError, a.nErrorId, a.fActPos,
                               a.fSetPos, a.fActVelo, a.fSetVelo, a.fLagError, a.fTorque_Nm))
    for c in st.aClamp:
        out.append(struct.pack(F_CLAMP, c.eState, c.bValve, c.bOpen, c.fLastSwitchTime_ms))
    out.append(struct.pack(F_IO, st.stIo.bPressureOk, *st.stIo.abPanelIn, *st.stIo.abPanelOut))
    k = st.stKin
    out.append(struct.pack(F_KIN, k.eState, k.eMode, k.nPlanId, k.bError, k.nErrorId, k.fMasterPos_mm))
    p = st.stParamActive
    m = p.stMaterial
    out.append(struct.pack(F_PARAM, p.nPlanId, m.fDiameter_mm, m.fWall_mm, m.fYield_MPa, m.fForceLimit_N,
                           p.fFeedOverride_pct, p.fSingleAxisVelo_pct))
    return b"".join(out)


def unpack_alarms(data: bytes) -> ST_HmiAlarms:
    if len(data) != SIZE_ALARMS:
        raise ValueError(f"ST_HmiAlarms: {len(data)} Bytes, erwartet {SIZE_ALARMS}")
    r = _Reader(data)
    h = r.take(F_ALARMS_HEAD)
    return ST_HmiAlarms(h[0], h[1], h[2], h[3], [ST_AlarmEntry(*r.take(F_ALARM_ENTRY)) for _ in range(32)])


def pack_alarms(al: ST_HmiAlarms) -> bytes:
    out = [struct.pack(F_ALARMS_HEAD, al.nChangeCounter, al.nCount, al.bOverflow, al.eHighestSeverity)]
    for e in al.aEntry:
        out.append(struct.pack(F_ALARM_ENTRY, e.eCode, e.eSeverity, e.nSource, e.nDetail, e.nTimestamp,
                               e.bActive, e.bAcked))
    return b"".join(out)


# ── Planübergabe GVL_HMI.stPlanXfer (ST_PlanTransfer, pack_mode 1) ──────────────
PLAN_FORMAT = 1
MAX_SECTIONS, MAX_FEED, MAX_ROT, MAX_POINTS = 16, 8, 16, 4000
F_PLAN_HEADER = "<IHHII" + "dddd"
F_PLAN_SECTION = "<dddIIH" + "dd" * MAX_FEED + "H" + "dd" * MAX_ROT + "B" + "d" * 6
F_PLAN_POINT = "<ddd"
SIZE_PLAN_HEADER = struct.calcsize(F_PLAN_HEADER)      # 48
SIZE_PLAN_SECTION = struct.calcsize(F_PLAN_SECTION)    # 469
SIZE_PLAN_POINT = struct.calcsize(F_PLAN_POINT)        # 24
OFS_PLAN_SECTIONS = SIZE_PLAN_HEADER
OFS_PLAN_POINTS = SIZE_PLAN_HEADER + MAX_SECTIONS * SIZE_PLAN_SECTION
SIZE_PLAN = OFS_PLAN_POINTS + MAX_POINTS * SIZE_PLAN_POINT   # 103552


def plan_checksum(data: bytes) -> int:
    """Gleicher Algorithmus wie F_PlanChecksum in der SPS: h := ROL(h, 5) XOR Byte, Startwert 0."""
    h = 0
    for b in data:
        h = (((h << 5) | (h >> 27)) & 0xFFFFFFFF) ^ b
    return h


def pack_plan(plan: dict) -> tuple[bytes, bytes, bytes]:
    """Maschinenplan (Format 1) → (Kopf, belegte Abschnitte, belegte Punkte) als Bytes für stPlanXfer.

    Die Abschnitte stehen ab OFS_PLAN_SECTIONS, die Punkte ab OFS_PLAN_POINTS. Der Kopf enthält die Prüfsumme
    über Abschnitte und Punkte und wird zuletzt geschrieben.
    """
    secs, pts = [], []
    bends = plan.get("bends", [])
    if not 1 <= len(bends) <= MAX_SECTIONS:
        raise ValueError(f"{len(bends)} Abschnitte, erlaubt 1 bis {MAX_SECTIONS}")
    for b in bends:
        first = len(pts) + 1
        for p in b["points"]:
            pts.append(struct.pack(F_PLAN_POINT, p["s_mm"], p["u_mm"], p["phi_deg"]))
        feed = b.get("feed_profile") or [{"z1_mm": b["z1_start_mm"], "feed_mm_s": b.get("feed_mm_s", 30.0)}]
        rots = b.get("rotations", [])
        if len(feed) > MAX_FEED or len(rots) > MAX_ROT:
            raise ValueError("zu viele Vorschubstufen oder Rotationen in einem Abschnitt")
        feed_vals = [x for f in feed for x in (f["z1_mm"], f["feed_mm_s"])] + [0.0] * 2 * (MAX_FEED - len(feed))
        rot_vals = [x for r in rots for x in (r["z1_mm"], r["w_deg"])] + [0.0] * 2 * (MAX_ROT - len(rots))
        mask, opens = 0, [0.0] * 6
        for i, nh in enumerate(b.get("clamps", [])):
            mask |= 1 << (nh - 1)
            ol = b.get("clamp_open_mm", [])
            opens[nh - 1] = ol[i] if i < len(ol) else 0.0
        secs.append(struct.pack(F_PLAN_SECTION, b["z1_start_mm"], b["z1_fin_mm"], b.get("z2_mm", 0.0),
                                first, len(b["points"]), len(feed), *feed_vals, len(rots), *rot_vals, mask, *opens))
    if len(pts) > MAX_POINTS:
        raise ValueError(f"{len(pts)} Punkte, erlaubt {MAX_POINTS}")
    sec_b, pts_b = b"".join(secs), b"".join(pts)
    m = plan.get("material", {})
    head = struct.pack(F_PLAN_HEADER, int(plan["plan_id"]), PLAN_FORMAT, len(secs), len(pts),
                       plan_checksum(sec_b + pts_b), float(m.get("diameter_mm", 15.0)), float(m.get("wall_mm", 2.0)),
                       float(m.get("yield_mpa", 300.0)), float(m.get("force_limit_n", 16000.0)))
    return head, sec_b, pts_b
