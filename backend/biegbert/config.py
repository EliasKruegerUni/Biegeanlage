"""Maschinen- und Laufzeitkonfiguration.

Werte mit dem Kommentar VORLÄUFIG stammen aus Doku/CAD-Abschätzungen und
werden an der Anlage ersetzt. Grenzwerte gehören in der SPS nach GVL_Config;
das Backend nutzt sie nur für Vorabprüfung und Simulation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AxisConfig:
    name: str
    unit: str
    min_pos: float            # Software-Endlage; W: -inf..inf (Modulo)
    max_pos: float
    v_max: float              # mm/s bzw. °/s (Automatik)
    v_setup: float            # Einrichtgeschwindigkeit bei 100 % (Einzelachse, Tippen)
    accel: float              # mm/s² bzw. °/s²
    modulo: bool = False


# Reihenfolge = E_Axis (1..5)
AXES: dict[str, AxisConfig] = {
    # VORLÄUFIG: Z1-Nullpunkt = CAD-Stand, Hub laut Doku 1282 mm, real begrenzt durch Kollision
    "Z1": AxisConfig("Z1", "mm", -30.0, 1282.0, 50.0, 30.0, 200.0),
    # VORLÄUFIG: Z2 = 0 entspricht v = 24 mm
    "Z2": AxisConfig("Z2", "mm", -50.0, 48.0, 28.6, 10.0, 100.0),
    # X: v_max ≈ 3,5 mm/s bei Nenndrehzahl (Dok. 01)
    "X": AxisConfig("X", "mm", -50.0, 50.0, 3.5, 3.5, 20.0),
    "A": AxisConfig("A", "°", -60.0, 60.0, 180.0, 20.0, 360.0),
    "W": AxisConfig("W", "°", float("-inf"), float("inf"), 180.0, 30.0, 360.0, modulo=True),
}
AXIS_ORDER = ["Z1", "Z2", "X", "A", "W"]


@dataclass(frozen=True)
class Geometry:
    """Prozessgeometrie (CAD-Auswertung vom 24.11.2026). VORLÄUFIG."""
    v_at_z2_zero_mm: float = 24.0          # Werkzeugabstand bei Z2 = 0
    z1_collision_base_mm: float = 743.25   # Z1-Grenze Futter ↔ Führung bei Z2 = 0 (inkl. 2 mm Abstand)
    chuck_front_cad_mm: float = -1670.61   # Futtervorderkante bei Z1 = 0 (CAD z)
    chuck_length_mm: float = 56.5
    clamp_half_length_mm: float = 73.0
    # Mitte der Niederhalter-Paare in CAD z; Paar k = Niederhalter 2k-1, 2k.
    # VORLÄUFIG: Paar 1 = nächstes zur Führung (Nummerierung an der Anlage prüfen)
    clamp_pair_center_z: tuple[float, float, float] = (-1089.36, -1319.11, -1547.11)
    clamp_open_lead_mm: float = 30.0       # Vorlauf: so weit vor der Zone öffnen
    clamp_switch_time_ms: float = 120.0    # Schaltzeit DFM-50 (geschätzt 50–200 ms)

    def v_mm(self, z2_mm: float) -> float:
        return self.v_at_z2_zero_mm - z2_mm

    def z1_max(self, z2_mm: float) -> float:
        return self.z1_collision_base_mm + z2_mm

    def clamp_zone_z1(self, pair: int) -> tuple[float, float]:
        """Z1-Bereich, in dem das Futter die Zone des Niederhalterpaares (1..3) berührt."""
        zc = self.clamp_pair_center_z[pair - 1]
        lo = (zc - self.clamp_half_length_mm) - self.chuck_front_cad_mm
        hi = (zc + self.clamp_half_length_mm) - (self.chuck_front_cad_mm - self.chuck_length_mm)
        return lo, hi


@dataclass(frozen=True)
class Timing:
    """Startwerte aus docs/hmi-schnittstelle.md, in Phase 4 messen."""
    browser_ping_ms: int = 100
    browser_timeout_ms: int = 300
    plc_heartbeat_ms: int = 100
    plc_heartbeat_timeout_ms: int = 500
    ack_timeout_ms: int = 1000
    two_step_ms: int = 10_000
    status_poll_ms: int = 50       # 20 Hz an das HMI
    vis_rate_ms: int = 33          # ~30 Hz an die Visualisierung


@dataclass
class Settings:
    plc: str = "sim"                       # "sim" oder "ads"
    ams_net_id: str = "127.0.0.1.1.1"
    ams_port: int = 851
    host: str = "127.0.0.1"
    port: int = 8765
    hmi_dir: str = ""                      # leer = ../hmi relativ zum Backend
    vis_dir: str = ""                      # leer = ../frontend
    geometry: Geometry = field(default_factory=Geometry)
    timing: Timing = field(default_factory=Timing)


GEOMETRY = Geometry()
TIMING = Timing()
