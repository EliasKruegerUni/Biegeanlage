"""Anbindung an die SPS (TwinCAT 3) über ADS (pyads). Zugriff nur auf GVL_HMI.*

Lesen:  GVL_HMI.stStatus und GVL_HMI.stAlarm als Bytefolge, zerlegt mit layout.py
Schreiben: einzelne Felder von GVL_HMI.stCmd bzw. GVL_HMI.stParam, nCmdId zuletzt
           und in einem eigenen Zugriff (Handshake laut docs/hmi-schnittstelle.md)
Plan:   GVL_HMI.stPlanXfer blockweise über Index-Gruppe/-Offset, Kopf (mit Prüfsumme) zuletzt

Dieselbe Anbindung gilt für die echte Anlage und für TwinCAT im Simulationsbetrieb (Compiler-Define SIM);
ob simuliert wird, meldet die SPS in stStatus.bSimulation.
"""
from __future__ import annotations

import logging
import struct

from .base import PlcLink
from .layout import (F_CMD_ACK, OFS_PLAN_POINTS, OFS_PLAN_SECTIONS, SIZE_ALARMS, SIZE_CMD_ACK, SIZE_PLAN,
                     SIZE_STATUS, pack_plan, unpack_alarms, unpack_status)
from .hmi_types import ST_HmiAlarms, ST_HmiCmdAck, ST_HmiParam, ST_HmiStatus

log = logging.getLogger(__name__)

try:
    import pyads
    from pyads.pyads_ex import adsGetSymbolInfo
except ImportError:  # pragma: no cover - nur ohne installierte Abhängigkeit
    pyads = None


class AdsPlc(PlcLink):
    kind = "ads"

    def __init__(self, ams_net_id: str, port: int = 851):
        if pyads is None:
            raise RuntimeError("pyads ist nicht installiert (pip install pyads)")
        self.ams_net_id, self.port = ams_net_id, port
        self.conn = None

    CHUNK = 16384

    def connect(self):
        """Öffnet die Verbindung. Läuft die SPS noch nicht, schlagen erst die Lesezugriffe fehl;
        die Bridge versucht es dann zyklisch weiter."""
        self.close()
        self.conn = pyads.Connection(self.ams_net_id, self.port)
        self.conn.open()
        for name, expected in (("GVL_HMI.stStatus", SIZE_STATUS), ("GVL_HMI.stAlarm", SIZE_ALARMS),
                               ("GVL_HMI.stPlanXfer", SIZE_PLAN)):
            try:
                size = self._sym_size(name)
                if size != expected:
                    log.error("%s: SPS meldet %d Bytes, Backend erwartet %d – layout.py anpassen", name, size, expected)
            except Exception as e:  # noqa: BLE001 - nur Diagnose
                log.warning("Größe von %s nicht prüfbar: %s", name, e)

    def _sym_size(self, name: str) -> int:
        """Größe einer SPS-Variablen in Bytes laut Symboltabelle (AdsSymbol selbst kennt sie nicht)."""
        return int(adsGetSymbolInfo(self.conn._port, self.conn._adr, name).size)

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None

    def _raw(self, name: str, size: int) -> bytes:
        return bytes(self.conn.read_by_name(name, pyads.PLCTYPE_BYTE * size))

    def read_status(self) -> ST_HmiStatus:
        return unpack_status(self._raw("GVL_HMI.stStatus", SIZE_STATUS))

    def read_alarms(self) -> ST_HmiAlarms:
        return unpack_alarms(self._raw("GVL_HMI.stAlarm", SIZE_ALARMS))

    def read_ack(self) -> ST_HmiCmdAck:
        return ST_HmiCmdAck(*struct.unpack(F_CMD_ACK, self._raw("GVL_HMI.stStatus.stCmdAck", SIZE_CMD_ACK)))

    def _w(self, name, value, plc_type):
        self.conn.write_by_name(f"GVL_HMI.{name}", value, plc_type)

    def write_cmd_fields(self, e_cmd, e_axis, n_clamp, f_position, f_velocity, n_value):
        self._w("stCmd.eCmd", int(e_cmd), pyads.PLCTYPE_UDINT)
        self._w("stCmd.eAxis", int(e_axis), pyads.PLCTYPE_INT)
        self._w("stCmd.nClamp", int(n_clamp), pyads.PLCTYPE_USINT)
        self._w("stCmd.fPosition", float(f_position), pyads.PLCTYPE_LREAL)
        self._w("stCmd.fVelocity", float(f_velocity), pyads.PLCTYPE_LREAL)
        self._w("stCmd.nValue", int(n_value), pyads.PLCTYPE_DINT)

    def write_cmd_id(self, n_cmd_id):
        self._w("stCmd.nCmdId", int(n_cmd_id), pyads.PLCTYPE_UDINT)

    def write_heartbeat(self, n):
        self._w("stCmd.nHeartbeat", int(n) & 0xFFFFFFFF, pyads.PLCTYPE_UDINT)

    def write_jog(self, e_axis, n_direction, f_factor_pct):
        self._w("stCmd.stJog.eAxis", int(e_axis), pyads.PLCTYPE_INT)
        self._w("stCmd.stJog.fVelocityFactor_pct", float(f_factor_pct), pyads.PLCTYPE_LREAL)
        self._w("stCmd.stJog.nDirection", int(n_direction), pyads.PLCTYPE_SINT)   # zuletzt

    def write_param(self, p: ST_HmiParam):
        m = p.stMaterial
        self._w("stParam.nPlanId", int(p.nPlanId), pyads.PLCTYPE_UDINT)
        self._w("stParam.stMaterial.fDiameter_mm", float(m.fDiameter_mm), pyads.PLCTYPE_LREAL)
        self._w("stParam.stMaterial.fWall_mm", float(m.fWall_mm), pyads.PLCTYPE_LREAL)
        self._w("stParam.stMaterial.fYield_MPa", float(m.fYield_MPa), pyads.PLCTYPE_LREAL)
        self._w("stParam.stMaterial.fForceLimit_N", float(m.fForceLimit_N), pyads.PLCTYPE_LREAL)
        self._w("stParam.fFeedOverride_pct", float(p.fFeedOverride_pct), pyads.PLCTYPE_LREAL)
        self._w("stParam.fSingleAxisVelo_pct", float(p.fSingleAxisVelo_pct), pyads.PLCTYPE_LREAL)

    def transfer_plan(self, plan: dict) -> None:
        """Maschinenplan nach GVL_HMI.stPlanXfer schreiben. Die SPS liest ihn erst bei PlanLoad und prüft
        dabei Plan-ID, Format und Prüfsumme."""
        head, secs, pts = pack_plan(plan)
        if self.conn is None:
            raise RuntimeError("keine ADS-Verbindung")
        sym = self.conn.get_symbol("GVL_HMI.stPlanXfer")
        size = self._sym_size("GVL_HMI.stPlanXfer")
        if size != SIZE_PLAN:
            raise RuntimeError(f"GVL_HMI.stPlanXfer: SPS meldet {size} Bytes, Backend erwartet {SIZE_PLAN}")
        ig, io = sym.index_group, sym.index_offset
        # Kopf ungültig machen, dann Daten, zuletzt den Kopf schreiben
        self.conn.write(ig, io, [0, 0, 0, 0], pyads.PLCTYPE_BYTE * 4)
        for base, data in ((OFS_PLAN_SECTIONS, secs), (OFS_PLAN_POINTS, pts)):
            for o in range(0, len(data), self.CHUNK):
                chunk = data[o:o + self.CHUNK]
                self.conn.write(ig, io + base + o, list(chunk), pyads.PLCTYPE_BYTE * len(chunk))
        self.conn.write(ig, io, list(head), pyads.PLCTYPE_BYTE * len(head))
