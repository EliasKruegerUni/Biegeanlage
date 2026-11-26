"""SPS-Anbindung: Typen, Byte-Layout und ADS (TwinCAT 3, real oder im Simulationsbetrieb)."""
from .base import PlcLink


def create_link(kind: str = "ads", ams_net_id: str = "127.0.0.1.1.1", port: int = 851) -> PlcLink:
    if kind == "ads":
        from .ads import AdsPlc
        return AdsPlc(ams_net_id, port)
    raise ValueError(f"Unbekannte SPS-Anbindung: {kind}")
