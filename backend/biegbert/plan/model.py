"""Geometrischer Biegeplan: Folge aus Gerade, Bogen und Rotation.

JSON-Format (biegbert-geo, Version 1):

{
  "format": "biegbert-geo", "version": 1, "name": "Beispiel",
  "material": {"diameter_mm": 15, "wall_mm": 2, "yield_mpa": 300, "force_limit_n": 16000},
  "process":  {"z2_mm": 0, "feed_mm_s": 30, "transition_mm": 30, "a_factor": 1.0},
  "elements": [
    {"type": "straight", "length_mm": 50},
    {"type": "bend", "angle_deg": 90, "radius_mm": 45},                       # konstanter Radius
    {"type": "bend_var", "length_mm": 60, "radius_start_mm": 0, "radius_end_mm": 45},  # Krümmung linear, 0 = gerade
    {"type": "rotation", "angle_deg": 90}
  ]
}

Ein negativer Biegewinkel biegt zur −X-Seite (gleichwertig zu W ± 180°).
"""
from __future__ import annotations

from dataclasses import dataclass, field

FORMAT_GEO = "biegbert-geo"


class PlanError(ValueError):
    pass


@dataclass
class Material:
    diameter_mm: float = 15.0
    wall_mm: float = 2.0
    yield_mpa: float = 300.0
    force_limit_n: float = 16000.0


@dataclass
class Process:
    z2_mm: float = 0.0
    feed_mm_s: float = 30.0
    transition_mm: float = 30.0     # Länge der Rampe beim An-/Rückbiegen (VORLÄUFIG)
    a_factor: float = 1.0           # A = a_factor · φ_tan (nicht starr gekoppelt, siehe [Meier])
    z1_start_mm: float | None = None


@dataclass
class Element:
    type: str                       # straight | bend | bend_var | rotation
    length_mm: float = 0.0
    angle_deg: float = 0.0
    radius_mm: float = 0.0
    radius_start_mm: float = 0.0    # 0 = unendlich (gerade)
    radius_end_mm: float = 0.0

    def kappa_at(self, s_local: float) -> float:
        """Krümmung (1/mm, mit Vorzeichen der Biegerichtung) an der Stelle s im Element."""
        if self.type == "bend":
            return (1.0 if self.angle_deg >= 0 else -1.0) / self.radius_mm
        if self.type == "bend_var":
            k0 = 1.0 / self.radius_start_mm if self.radius_start_mm else 0.0
            k1 = 1.0 / self.radius_end_mm if self.radius_end_mm else 0.0
            t = 0.0 if self.length_mm <= 0 else min(1.0, max(0.0, s_local / self.length_mm))
            sign = -1.0 if self.angle_deg < 0 else 1.0
            return sign * (k0 + (k1 - k0) * t)
        return 0.0

    @property
    def material_length(self) -> float:
        if self.type == "bend":
            return abs(self.angle_deg) * 3.141592653589793 / 180.0 * self.radius_mm
        if self.type in ("straight", "bend_var"):
            return self.length_mm
        return 0.0

    def to_json(self) -> dict:
        d = {"type": self.type}
        if self.type == "straight":
            d["length_mm"] = self.length_mm
        elif self.type == "bend":
            d.update(angle_deg=self.angle_deg, radius_mm=self.radius_mm)
        elif self.type == "bend_var":
            d.update(length_mm=self.length_mm, radius_start_mm=self.radius_start_mm,
                     radius_end_mm=self.radius_end_mm)
            if self.angle_deg < 0:
                d["direction"] = -1
        elif self.type == "rotation":
            d["angle_deg"] = self.angle_deg
        return d


@dataclass
class GeoPlan:
    name: str = "Biegeplan"
    material: Material = field(default_factory=Material)
    process: Process = field(default_factory=Process)
    elements: list[Element] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"format": FORMAT_GEO, "version": 1, "name": self.name,
                "material": vars(self.material).copy(),
                "process": {k: v for k, v in vars(self.process).items() if v is not None},
                "elements": [e.to_json() for e in self.elements]}


def _num(d: dict, key: str, default=None, *, positive=False, nonneg=False, where="") -> float:
    if key not in d:
        if default is None:
            raise PlanError(f"{where}: Feld „{key}“ fehlt")
        return default
    try:
        v = float(d[key])
    except (TypeError, ValueError):
        raise PlanError(f"{where}: „{key}“ ist keine Zahl") from None
    if positive and v <= 0:
        raise PlanError(f"{where}: „{key}“ muss größer als 0 sein")
    if nonneg and v < 0:
        raise PlanError(f"{where}: „{key}“ darf nicht negativ sein")
    return v


def element_from_json(d: dict, idx: int = 0) -> Element:
    where = f"Element {idx + 1}"
    t = d.get("type")
    if t == "straight":
        return Element("straight", length_mm=_num(d, "length_mm", positive=True, where=where))
    if t == "bend":
        a = _num(d, "angle_deg", where=where)
        if a == 0:
            raise PlanError(f"{where}: Biegewinkel 0°")
        return Element("bend", angle_deg=a, radius_mm=_num(d, "radius_mm", positive=True, where=where))
    if t == "bend_var":
        e = Element("bend_var", length_mm=_num(d, "length_mm", positive=True, where=where),
                    radius_start_mm=_num(d, "radius_start_mm", 0.0, nonneg=True, where=where),
                    radius_end_mm=_num(d, "radius_end_mm", 0.0, nonneg=True, where=where))
        if not e.radius_start_mm and not e.radius_end_mm:
            raise PlanError(f"{where}: veränderlicher Bogen braucht mindestens einen Radius ≠ 0")
        e.angle_deg = -1.0 if float(d.get("direction", 1)) < 0 else 1.0
        return e
    if t == "rotation":
        return Element("rotation", angle_deg=_num(d, "angle_deg", where=where))
    raise PlanError(f"{where}: unbekannter Typ „{t}“")


def geo_from_json(d: dict) -> GeoPlan:
    if d.get("format") != FORMAT_GEO:
        raise PlanError("Kein biegbert-geo-Plan (Feld „format“)")
    if int(d.get("version", 0)) != 1:
        raise PlanError(f"Formatversion {d.get('version')} wird nicht unterstützt")
    m, p = d.get("material", {}), d.get("process", {})
    plan = GeoPlan(
        name=str(d.get("name", "Biegeplan")),
        material=Material(_num(m, "diameter_mm", 15.0, positive=True, where="Material"),
                          _num(m, "wall_mm", 2.0, nonneg=True, where="Material"),
                          _num(m, "yield_mpa", 300.0, positive=True, where="Material"),
                          _num(m, "force_limit_n", 16000.0, positive=True, where="Material")),
        process=Process(_num(p, "z2_mm", 0.0, where="Prozess"),
                        _num(p, "feed_mm_s", 30.0, positive=True, where="Prozess"),
                        _num(p, "transition_mm", 30.0, nonneg=True, where="Prozess"),
                        _num(p, "a_factor", 1.0, where="Prozess"),
                        (float(p["z1_start_mm"]) if p.get("z1_start_mm") is not None else None)),
        elements=[element_from_json(e, i) for i, e in enumerate(d.get("elements", []))],
    )
    if not plan.elements:
        raise PlanError("Der Plan enthält keine Elemente")
    return plan
