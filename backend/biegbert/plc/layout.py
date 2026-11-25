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
