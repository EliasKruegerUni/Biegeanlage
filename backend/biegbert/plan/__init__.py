"""Biegeplan: Datenmodell, Import (JSON, LRA, Maschinenplan) und Prozessmodell."""
from __future__ import annotations

import json

from .lra import parse_lra, to_lra
from .model import FORMAT_GEO, GeoPlan, PlanError, element_from_json, geo_from_json
from .process import build

__all__ = ["GeoPlan", "PlanError", "build", "element_from_json", "geo_from_json", "parse_lra", "to_lra",
           "parse_upload"]


def parse_upload(filename: str, content: str, default_radius_mm: float = 45.0) -> tuple[str, object]:
    """Erkennt das Format einer hochgeladenen Datei.

    Rückgabe: ("geo", GeoPlan) oder ("machine", dict nach docs/kinematik.md).
    """
    name = filename.lower()
    if name.endswith((".step", ".stp")):
        raise PlanError("CAD-Import (STEP → Biegeplan) ist noch nicht umgesetzt. "
                        "Bitte vorerst einen Biegeplan als JSON oder LRA laden.")
    if name.endswith((".lra", ".csv", ".txt", ".ybc")):
        return "geo", parse_lra(content, default_radius_mm, name=filename)
    try:
        d = json.loads(content)
    except json.JSONDecodeError as e:
        raise PlanError(f"Keine gültige JSON-Datei: {e}") from None
    if d.get("format") == FORMAT_GEO:
        return "geo", geo_from_json(d)
    if d.get("format") == 1 and "bends" in d:
        return "machine", d
    raise PlanError("Unbekanntes Planformat (erwartet biegbert-geo, Maschinenplan Format 1 oder LRA)")
