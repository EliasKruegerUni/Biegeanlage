"""Prozessmodell: Referenzfall [Eberle] und Planerzeugung."""
import math

import pytest

from biegbert.plan import build, geo_from_json, parse_lra, to_lra
from biegbert.plan.process import kappa_from_u, phi_from_u, u_from_kappa


def geo(elements, **process):
    return geo_from_json({"format": "biegbert-geo", "version": 1, "process": process, "elements": elements})


def test_eberle_reference():
    # v = 24 mm, u = 7 mm → R ≈ 44,64 mm, φ_tan = 32,52°
    v = 24.0
    R = 1 / kappa_from_u(7.0, v)
    assert R == pytest.approx(44.6429, abs=1e-3)
    assert phi_from_u(7.0, v) == pytest.approx(32.52, abs=0.01)
    assert u_from_kappa(1 / R, v) == pytest.approx(7.0, abs=1e-9)


def test_radius_below_v_is_error():
    m, rep = build(geo([{"type": "straight", "length_mm": 50}, {"type": "bend", "angle_deg": 90, "radius_mm": 20},
                        {"type": "straight", "length_mm": 50}]), 1)
    assert m is None and any("kleiner als Werkzeugabstand" in x["text"] for x in rep["messages"])


def test_plan_structure_and_limits():
    g = geo([{"type": "straight", "length_mm": 60}, {"type": "bend", "angle_deg": 90, "radius_mm": 44.64},
             {"type": "straight", "length_mm": 40}, {"type": "rotation", "angle_deg": 90},
             {"type": "bend", "angle_deg": -45, "radius_mm": 60}, {"type": "straight", "length_mm": 50}],
            transition_mm=30)
    m, rep = build(g, 7, 0.0)
    assert m and rep["ok"]
    assert m["plan_id"] == 7 and m["format"] == 1
    last = -1e9
    for b in m["bends"]:
        s = [p["s_mm"] for p in b["points"]]
        assert all(a < c for a, c in zip(s, s[1:]))          # streng monoton je Biegung
        assert s[0] >= last - 1e-9                            # Biegungen überlappen nicht
        last = s[-1]
        # X-Geschwindigkeit beim gewählten Vorschub ≤ 3,5 mm/s
        slope = max(abs(q["u_mm"] - p["u_mm"]) / (q["s_mm"] - p["s_mm"]) for p, q in zip(b["points"], b["points"][1:]))
        assert slope * b["feed_mm_s"] <= 3.5 + 1e-6
    u_max = max(abs(p["u_mm"]) for p in m["bends"][0]["points"])
    assert u_max == pytest.approx(7.0, abs=0.01)
    # Rotation sitzt im zweiten Bogen, dort wo u = 0 ist
    rots = [r for b in m["bends"] for r in b["rotations"]]
    assert len(rots) == 1 and rots[0]["w_deg"] == 90
    b2 = m["bends"][1]
    assert b2["points"][0]["u_mm"] == 0 and rots[0]["z1_mm"] >= b2["z1_start_mm"]
    # Biegung in −X-Richtung
    assert min(p["u_mm"] for p in b2["points"]) < -1


def test_centerline_angle():
    """Ein 90°-Bogen ergibt eine um ~90° abgeknickte Mittellinie (Übergänge verschmieren den Radius, nicht den Winkel)."""
    g = geo([{"type": "straight", "length_mm": 80}, {"type": "bend", "angle_deg": 90, "radius_mm": 50},
             {"type": "straight", "length_mm": 80}], transition_mm=20)
    _, rep = build(g, 1)
    c = rep["centerline"]
    d0 = [c[1][i] - c[0][i] for i in range(3)]
    d1 = [c[-1][i] - c[-2][i] for i in range(3)]
    cos = sum(a * b for a, b in zip(d0, d1)) / math.dist(c[0], c[1]) / math.dist(c[-2], c[-1])
    assert math.degrees(math.acos(max(-1, min(1, cos)))) == pytest.approx(90, abs=3)


def test_lra_roundtrip():
    g = parse_lra("# L R A CLR\n50 0 90 45\n30 90 45 60\n40\n")
    types = [e.type for e in g.elements]
    assert types == ["straight", "bend", "straight", "rotation", "bend", "straight"]
    assert parse_lra(to_lra(g)).to_json()["elements"] == g.to_json()["elements"]
