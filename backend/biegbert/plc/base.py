"""Abstrakte Verbindung zur SPS.

Die Bridge kennt nur diese Klasse. Implementierungen:
  ads.AdsPlc – echte SPS über ADS (pyads), Zugriff nur auf GVL_HMI.*

Alle Methoden sind blockierend; die Bridge ruft sie im Thread-Pool auf.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .hmi_types import ST_HmiAlarms, ST_HmiCmdAck, ST_HmiParam, ST_HmiStatus


class PlcLink(ABC):
    kind: str = "?"

    def connect(self) -> None: ...
    def close(self) -> None: ...

    @abstractmethod
    def read_status(self) -> ST_HmiStatus: ...

    @abstractmethod
    def read_alarms(self) -> ST_HmiAlarms: ...

    def read_ack(self) -> ST_HmiCmdAck:
        """Nur die Kommandoantwort lesen (schneller als der ganze Status)."""
        return self.read_status().stCmdAck

    @abstractmethod
    def write_cmd_fields(self, e_cmd: int, e_axis: int, n_clamp: int, f_position: float,
                         f_velocity: float, n_value: int) -> None:
        """Schritt 2 des Handshakes: Kommando und Parameter schreiben."""

    @abstractmethod
    def write_cmd_id(self, n_cmd_id: int) -> None:
        """Schritt 3: nCmdId in einem eigenen Zugriff schreiben."""

    @abstractmethod
    def write_heartbeat(self, n: int) -> None: ...

    @abstractmethod
    def write_jog(self, e_axis: int, n_direction: int, f_factor_pct: float) -> None: ...

    @abstractmethod
    def write_param(self, p: ST_HmiParam) -> None: ...

    @abstractmethod
    def transfer_plan(self, plan: dict) -> None:
        """Maschinenplan nach GVL_HMI.stPlanXfer schreiben (layout.pack_plan)."""
