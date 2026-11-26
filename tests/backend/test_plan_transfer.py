"""Planübergabe an die SPS: Byte-Layout, Prüfsumme und dieselben Prüfungen wie FB_PlanStore.Load."""
import struct

import pytest

from biegbert.config import AXES, GEOMETRY
from biegbert.plan import build, geo_from_json
from biegbert.plc.layout import (F_PLAN_HEADER, F_PLAN_POINT, F_PLAN_SECTION, OFS_PLAN_POINTS, OFS_PLAN_SECTIONS,
                                 SIZE_PLAN, SIZE_PLAN_POINT, SIZE_PLAN_SECTION, pack_plan, plan_checksum)

TOL = 0.05


def plc_load(head: bytes, secs: bytes, pts: bytes, plan_id: int) -> int:
    """Nachbildung von FB_PlanStore.Load (06_HMI). Rückgabe 0 oder Detailcode."""
    h = struct.unpack(F_PLAN_HEADER, head)
    pid, fmt, nsec, npts, chk = h[:5]
    if pid != plan_id or pid == 0:
        return 1
    if fmt != 1:
        return 2
    if npts > 4000:
        return 15
    if not 1 <= nsec <= 16 or npts < 2:
        return 2
    if plan_checksum(secs[:nsec * SIZE_PLAN_SECTION] + pts[:npts * SIZE_PLAN_POINT]) != chk:
        return 3
    P = [struct.unpack_from(F_PLAN_POINT, pts, i * SIZE_PLAN_POINT) for i in range(npts)]
    prev_fin = -1e9
    for k in range(nsec):
        v = struct.unpack_from(F_PLAN_SECTION, secs, k * SIZE_PLAN_SECTION)
        z1s, z1f, z2, first, cnt, nfeed = v[:6]
        feed = [(v[6 + 2 * i], v[7 + 2 * i]) for i in range(nfeed)]
        nrot = v[22]
        if not z1s < z1f:
            return 10
        if cnt < 2 or first < 1 or first + cnt - 1 > npts:
            return 2
        if z1s < prev_fin - TOL:
            return 11
        if not AXES["Z2"].min_pos <= z2 <= AXES["Z2"].max_pos:
            return 13
        if z1f > GEOMETRY.z1_max(z2) + TOL or z1f > AXES["Z1"].max_pos:
            return 14
        if not 1 <= nfeed <= 8 or nrot > 16:
            return 2
        for i, (z, f) in enumerate(feed):
            if not 0 < f <= AXES["Z1"].v_max or (i and z <= feed[i - 1][0]):
                return 2
        pp = P[first - 1:first - 1 + cnt]
        if abs(pp[0][0] - z1s) > TOL or abs(pp[-1][0] - z1f) > TOL:
            return 11
        fi = 0
        for j, (s, u, phi) in enumerate(pp):
            if abs(u) > AXES["X"].max_pos or abs(phi) > AXES["A"].max_pos:
                return 12
            if j:
                ds = s - pp[j - 1][0]
                if ds <= 0:
                    return 11
                while fi < nfeed - 1 and feed[fi + 1][0] <= pp[j - 1][0] + 1e-6:
                    fi += 1
                if abs(u - pp[j - 1][1]) / ds * feed[fi][1] > AXES["X"].v_max * 1.05:
                    return 16
        prev_fin = z1f
    return 0


def geo(elements, **process):
    return geo_from_json({"format": "biegbert-geo", "version": 1, "process": process, "elements": elements})


PLANS = [
    [{"type": "straight", "length_mm": 50}, {"type": "bend", "angle_deg": 90, "radius_mm": 48},
     {"type": "bend", "angle_deg": -90, "radius_mm": 70}, {"type": "bend", "angle_deg": 90, "radius_mm": 87.5},
     {"type": "bend", "angle_deg": -90, "radius_mm": 120}],
    [{"type": "straight", "length_mm": 40}, {"type": "bend", "angle_deg": 45, "radius_mm": 50},
     {"type": "straight", "length_mm": 40}, {"type": "rotation", "angle_deg": 90},
     {"type": "bend", "angle_deg": 30, "radius_mm": 60}, {"type": "straight", "length_mm": 30}],
    [{"type": "straight", "length_mm": 30}, {"type": "bend_var", "length_mm": 80, "radius_start_mm": 0,
                                             "radius_end_mm": 40, "direction": -1},
     {"type": "bend_var", "length_mm": 80, "radius_start_mm": 40, "radius_end_mm": 0, "direction": -1},
     {"type": "straight", "length_mm": 30}],
]


def test_layout_sizes():
    assert struct.calcsize(F_PLAN_HEADER) == 48
    assert SIZE_PLAN_SECTION == 469
    assert OFS_PLAN_SECTIONS == 48 and OFS_PLAN_POINTS == 48 + 16 * 469
    assert SIZE_PLAN == 103552


def test_checksum_reference():
    # Referenzwerte für den Abgleich mit F_PlanChecksum in TwinCAT
    assert plan_checksum(b"") == 0
    assert plan_checksum(b"\x01") == 1
    assert plan_checksum(b"\x01\x02") == (1 << 5) ^ 2
    assert plan_checksum(bytes(range(256))) == plan_checksum(bytes(range(256)))


@pytest.mark.parametrize("els", PLANS)
@pytest.mark.parametrize("transition", [10, 30])
def test_generated_plans_pass_plc_checks(els, transition):
    m, rep = build(geo(els, transition_mm=transition), 4711, 0.0)
    assert m, rep["messages"]
    head, secs, pts = pack_plan(m)
    assert plc_load(head, secs, pts, 4711) == 0


def test_plc_rejects_corrupted_plan():
    m, _ = build(geo(PLANS[0]), 7, 0.0)
    head, secs, pts = pack_plan(m)
    bad = bytearray(pts)
    bad[10] ^= 0xFF
    assert plc_load(head, secs, bytes(bad), 7) == 3
    assert plc_load(head, secs, pts, 8) == 1


def test_one_section_without_stop_between_bends():
    m, rep = build(geo(PLANS[0], transition_mm=10), 1, 0.0)
    assert len(m["bends"]) == 1
    b = m["bends"][0]
    assert b["z1_start_mm"] == 0.0 and b["z1_fin_mm"] == pytest.approx(rep["summary"]["z1_end_mm"], abs=0.01)
    assert rep["summary"]["bends"] == 4
    assert b["feed_profile"][0]["z1_mm"] == 0.0 and len(b["feed_profile"]) <= 8
