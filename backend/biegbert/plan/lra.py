"""LRA-Import/-Export (Length – Rotation – Angle, auch YBC).

Je Zeile: L  R  A  [CLR]   (Trennzeichen Leerzeichen, Tab, ; oder ,)
  L    gerade Länge vor der Biegung in mm
  R    Drehung des Rohrs vor der Biegung in °
  A    Biegewinkel in °
  CLR  Biegeradius (Mittellinie) in mm; fehlt er, gilt der Standardradius
Zeilen mit # oder ohne Zahlen werden übersprungen. Die letzte Zeile darf nur L enthalten.
"""
from __future__ import annotations

import re

from .model import Element, GeoPlan, PlanError


def parse_lra(text: str, default_radius_mm: float = 45.0, name: str = "LRA-Import") -> GeoPlan:
    plan = GeoPlan(name=name)
    rows = 0
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p for p in re.split(r"[\s;,]+", line) if p]
        try:
            nums = [float(p.replace(",", ".")) for p in parts]
        except ValueError:
            if rows == 0:
                continue   # Kopfzeile
            raise PlanError(f"LRA Zeile {ln}: keine Zahlen") from None
        if not 1 <= len(nums) <= 4:
            raise PlanError(f"LRA Zeile {ln}: 1 bis 4 Werte erwartet")
        rows += 1
        L = nums[0]
        R = nums[1] if len(nums) > 1 else 0.0
        A = nums[2] if len(nums) > 2 else 0.0
        clr = nums[3] if len(nums) > 3 else default_radius_mm
        if L > 0:
            plan.elements.append(Element("straight", length_mm=L))
        if R:
            plan.elements.append(Element("rotation", angle_deg=R))
        if A:
            if clr <= 0:
                raise PlanError(f"LRA Zeile {ln}: Radius muss > 0 sein")
            plan.elements.append(Element("bend", angle_deg=A, radius_mm=clr))
    if not plan.elements:
        raise PlanError("LRA: keine Daten gefunden")
    return plan


def to_lra(plan: GeoPlan) -> str:
    """Nur konstante Radien; veränderliche Bögen werden abgelehnt."""
    lines = ["# L_mm R_deg A_deg CLR_mm"]
    L = R = 0.0
    for e in plan.elements:
        if e.type == "straight":
            L += e.length_mm
        elif e.type == "rotation":
            R += e.angle_deg
        elif e.type == "bend":
            lines.append(f"{L:.3f} {R:.3f} {e.angle_deg:.3f} {e.radius_mm:.3f}")
            L = R = 0.0
        else:
            raise PlanError("LRA kennt keine veränderlichen Radien")
    if L > 0:
        lines.append(f"{L:.3f}")
    return "\n".join(lines) + "\n"
