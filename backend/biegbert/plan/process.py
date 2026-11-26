"""Prozessmodell: geometrischer Biegeplan → Maschinenplan (Format aus docs/kinematik.md).

Grundlage ist die geometrische Näherung nach [Eberle]:
    R(u, v) = (u² + v²) / (2u)        u(R, v) = R − √(R² − v²)    (R ≥ v)
    φ_tan   = arcsin(2uv / (u² + v²))  = arcsin(v / R)

Vereinfachungen (VORLÄUFIG, mit Versuchen ersetzen):
  - Material in der Matrize ≙ Vorschubweg Z1 (Bogenlänge der Biegezone vernachlässigt)
  - An- und Rückbiegen als lineare Rampe von u über transition_mm, mittig auf der
    Elementgrenze, an Rotationen und Planenden abgeschnitten
  - keine Rückfederungs- oder Elastizitätskorrektur
  - A = a_factor · φ_tan(u, v)

Der Maschinenplan erweitert das Format aus kinematik.md um „feed_mm_s“ (kleinster Vorschub),
„feed_profile“: [{"z1_mm", "feed_mm_s"}] und „rotations“: [{"z1_mm", "w_deg"}] (relative Drehung von W).
Ein Plan ist EIN Abschnitt (eine Kurvenscheibe von z1_start bis z1_fin, Geraden mit u = 0); mehrere
Abschnitte sind nur bei wechselndem Z2 nötig (noch nicht erzeugt).
"""
from __future__ import annotations

import math

from ..config import AXES, GEOMETRY
from .model import GeoPlan

DS = 0.5           # Abtastschritt in mm
TOL_U = 0.005      # Toleranz beim Ausdünnen (mm)
TOL_PHI = 0.02     # (°)
MAX_POINTS = 4000  # = GVL_Config.cMaxPoints
MAX_ROT = 16       # = GVL_Config.cMaxRot
MAX_FEED = 8       # = GVL_Config.cMaxFeed


def u_from_kappa(kappa: float, v: float) -> float:
    if abs(kappa) < 1e-12:
        return 0.0
    r = 1.0 / abs(kappa)
    if r < v:
        return math.nan
    return math.copysign(r - math.sqrt(r * r - v * v), kappa)


def phi_from_u(u: float, v: float) -> float:
    if abs(u) < 1e-12:
        return 0.0
    return math.copysign(math.degrees(math.asin(min(1.0, 2 * abs(u) * v / (u * u + v * v)))), u)


def kappa_from_u(u: float, v: float) -> float:
    return 0.0 if abs(u) < 1e-12 else math.copysign(2 * abs(u) / (u * u + v * v), u)


def _rdp(pts: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Douglas-Peucker auf (s, u) und (s, φ) gemeinsam."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        s0, u0, p0 = pts[i0]
        s1, u1, p1 = pts[i1]
        worst, wi = 0.0, -1
        for i in range(i0 + 1, i1):
            s, u, p = pts[i]
            t = (s - s0) / (s1 - s0) if s1 != s0 else 0.0
            e = max(abs(u - (u0 + t * (u1 - u0))) / TOL_U, abs(p - (p0 + t * (p1 - p0))) / TOL_PHI)
            if e > worst:
                worst, wi = e, i
        if worst > 1.0:
            keep[wi] = True
            stack += [(i0, wi), (wi, i1)]
    return [p for p, k in zip(pts, keep) if k]


def build(geo: GeoPlan, plan_id: int, z1_start: float = 0.0) -> tuple[dict | None, dict]:
    """Gibt (Maschinenplan oder None bei Fehlern, Bericht) zurück."""
    msgs: list[dict] = []

    def msg(level, text, el=None):
        msgs.append({"level": level, "text": text, "element": el})

    pr = geo.process
    if pr.z1_start_mm is not None:
        z1_start = pr.z1_start_mm
    v = GEOMETRY.v_mm(pr.z2_mm)
    if v <= 0:
        msg("error", f"Werkzeugabstand v = {v:.1f} mm ≤ 0 bei Z2 = {pr.z2_mm} mm")
        return None, _report(False, msgs)
    if not AXES["Z2"].min_pos <= pr.z2_mm <= AXES["Z2"].max_pos:
        msg("error", f"Z2 = {pr.z2_mm} mm außerhalb {AXES['Z2'].min_pos}…{AXES['Z2'].max_pos} mm")

    # 1) Elemente auf den Vorschubweg legen
    pieces, rotations, el_info = [], [], []
    s = z1_start
    for i, e in enumerate(geo.elements):
        if e.type == "rotation":
            rotations.append({"s": s, "w": e.angle_deg, "el": i})
            el_info.append({"index": i, "type": e.type, "s0": s, "s1": s, "angle_deg": e.angle_deg})
            continue
        L = e.material_length
        pieces.append({"s0": s, "s1": s + L, "el": e, "i": i})
        info = {"index": i, "type": e.type, "s0": round(s, 3), "s1": round(s + L, 3), "length_mm": round(L, 3)}
        if e.type in ("bend", "bend_var"):
            ks = [abs(e.kappa_at(L * t / 20)) for t in range(21)]
            info["angle_deg"] = round(math.degrees(sum((ks[j] + ks[j + 1]) / 2 * L / 20 for j in range(20))), 3) \
                * (1 if e.angle_deg >= 0 else -1)
            rs = [1 / k for k in ks if k > 0]
            info["r_min"], info["r_max"] = round(min(rs), 3), round(max(rs), 3)
            if min(rs) < v:
                msg("error", f"Radius {min(rs):.1f} mm kleiner als Werkzeugabstand v = {v:.1f} mm "
                             f"(kleinster herstellbarer Radius bei Z2 = {pr.z2_mm} mm)", i)
            else:
                phi_max = math.degrees(math.asin(v / min(rs))) * abs(pr.a_factor)
                if phi_max > AXES["A"].max_pos:
                    r_lim = v / math.sin(math.radians(AXES["A"].max_pos / abs(pr.a_factor)))
                    msg("error", f"A = {phi_max:.1f}° überschreitet ±{AXES['A'].max_pos:.0f}° "
                                 f"(Radius mindestens {r_lim:.1f} mm)", i)
        el_info.append(info)
        s += L
    s_end = s
    if not pieces:
        msg("error", "Der Plan enthält keine Vorschubelemente (nur Rotationen)")
        return None, _report(False, msgs, el_info)
    if any(m["level"] == "error" for m in msgs):
        return None, _report(False, msgs, el_info)

    # 2) u(s) roh abtasten, Rampen an Elementgrenzen einsetzen
    cuts = [z1_start] + [r["s"] for r in rotations] + [s_end]

    def u_raw(x: float) -> float:
        for p in pieces:
            if p["s0"] <= x < p["s1"] or (x == p["s1"] == s_end and p is pieces[-1]):
                return u_from_kappa(p["el"].kappa_at(x - p["s0"]), v)
        return 0.0

    n = max(2, int(math.ceil((s_end - z1_start) / DS)) + 1)
    grid = [z1_start + (s_end - z1_start) * k / (n - 1) for k in range(n)]
    # Rotationspositionen exakt als Stützstellen (dort steht der Master, u muss 0 sein)
    extra = [r["s"] for r in rotations if z1_start < r["s"] < s_end]
    if extra:
        grid = sorted(set(grid) | set(extra))
    u = [u_raw(x) for x in grid]
    half = pr.transition_mm / 2
    shortened = set()
    for j in range(len(pieces) - 1):
        p, q = pieces[j], pieces[j + 1]
        b = p["s1"]
        ua = u_raw(b - 1e-6) if p["s1"] > p["s0"] else 0.0
        ub = u_raw(b + 1e-6)
        if abs(ua - ub) < 1e-9:
            continue
        rot_here = any(abs(r["s"] - b) < 1e-9 for r in rotations)
        lo_lim = max([c for c in cuts if c < b - 1e-9] + [z1_start])
        hi_lim = min([c for c in cuts if c > b + 1e-9] + [s_end])
        h1 = min(half, (p["s1"] - p["s0"]) / 2, b - lo_lim)
        h2 = min(half, (q["s1"] - q["s0"]) / 2, hi_lim - b)
        if rot_here:
            # Rotation an der Grenze: vorher auf u = 0 zurückbiegen, nachher neu anbiegen
            ramps = [(b - h1, b, u_raw(b - h1 - 1e-6) if h1 > 0 else 0.0, 0.0),
                     (b, b + h2, 0.0, u_raw(b + h2))]
            if (abs(ua) > 1e-9 and h1 < pr.transition_mm - 1e-6) or (abs(ub) > 1e-9 and h2 < pr.transition_mm - 1e-6):
                shortened.add(q["i"])
        else:
            ramps = [(b - h1, b + h2, u_raw(b - h1 - 1e-6) if h1 > 0 else ua, u_raw(b + h2))]
            if h1 + h2 < pr.transition_mm - 1e-6:
                shortened.add(q["i"] if abs(ub) > abs(ua) else p["i"])
        for a0, a1, u0, u1 in ramps:
            if a1 <= a0:
                continue
            for k, x in enumerate(grid):
                if a0 <= x <= a1:
                    u[k] = u0 + (u1 - u0) * (x - a0) / (a1 - a0)
    # Plananfang und -ende: an- und rückbiegen innerhalb des Plans
    for edge, sign in ((z1_start, 1), (s_end, -1)):
        k0 = 0 if sign == 1 else len(grid) - 1
        if abs(u[k0]) > 1e-9:
            span = min(pr.transition_mm, s_end - z1_start)
            for k, x in enumerate(grid):
                d = (x - edge) * sign
                if 0 <= d <= span:
                    u[k] *= d / span if span > 0 else 0.0
            msg("warning", "Plan beginnt oder endet mit einem Bogen: An- bzw. Rückbiegen verkürzt den Bogen – "
                           "Gerade davor/danach einfügen")
    for i in sorted(shortened):
        msg("warning", f"Übergang an Element {i + 1} kürzer als {pr.transition_mm:.0f} mm (Element oder Rotation zu nah)", i)
    for k in range(len(u)):
        if abs(u[k]) > AXES["X"].max_pos:
            msg("error", f"Auslenkung u = {u[k]:.1f} mm überschreitet X ±{AXES['X'].max_pos:.0f} mm")
            break
    phi = [pr.a_factor * phi_from_u(x, v) for x in u]

    # 3) Eine Kurvenscheibe für den ganzen Plan (Z2 konstant): Geraden sind Abschnitte mit u = 0, damit der
    #    Vorschub zwischen Bögen nicht hält. Zusammenhängende Bereiche mit u ≠ 0 („Zonen“) bekommen einen
    #    Vorschub, bei dem X höchstens 95 % von v_max braucht; dazwischen gilt der Plan-Vorschub.
    active = [abs(x) > 1e-6 for x in u]
    zones, k = [], 0
    while k < len(grid):
        if active[k]:
            k0 = k
            while k < len(grid) and active[k]:
                k += 1
            zones.append((max(0, k0 - 1), min(len(grid) - 1, k)))
        else:
            k += 1
    # Stützpunkte: an Zonengrenzen und Rotationen festhalten, dazwischen ausdünnen
    keep = {0, len(grid) - 1}
    for k0, k1 in zones:
        keep |= {k0, k1}
    for r in rotations:
        keep.add(min(range(len(grid)), key=lambda i: abs(grid[i] - r["s"])))
    keep = sorted(keep)
    pts = []
    for i0, i1 in zip(keep, keep[1:]):
        part = _rdp([(grid[k], u[k], phi[k]) for k in range(i0, i1 + 1)])
        pts.extend(part if not pts else part[1:])
    feed_profile = [(z1_start, pr.feed_mm_s)]
    for zi, (k0, k1) in enumerate(zones):
        zp = [(grid[k], u[k]) for k in range(k0, k1 + 1)]
        slope = max((abs(zp[j + 1][1] - zp[j][1]) / (zp[j + 1][0] - zp[j][0])
                     for j in range(len(zp) - 1) if zp[j + 1][0] > zp[j][0]), default=0.0)
        feed = pr.feed_mm_s
        if slope * feed > AXES["X"].v_max:
            feed = 0.95 * AXES["X"].v_max / slope
            msg("warning", f"Biegung {zi + 1}: Vorschub auf {feed:.1f} mm/s reduziert, "
                           f"sonst überschreitet X {AXES['X'].v_max} mm/s (Übergang verlängern hilft)")
        feed_profile.append((grid[k0], feed))
        if k1 < len(grid) - 1:
            feed_profile.append((grid[k1], pr.feed_mm_s))
    feed_profile = _merge_feed(feed_profile, MAX_FEED)
    if len(pts) > MAX_POINTS:
        msg("error", f"{len(pts)} Stützpunkte, die SPS nimmt höchstens {MAX_POINTS} je Plan")
    if len(rotations) > MAX_ROT:
        msg("error", f"{len(rotations)} Rotationen, die SPS nimmt höchstens {MAX_ROT} je Plan")
    if rotations and rotations[-1]["s"] >= s_end - 1e-9:
        msg("warning", "Rotation am Planende ohne nachfolgenden Bogen – geometrisch ohne Wirkung")
    bends = [_bend(z1_start, s_end, pr.z2_mm, min(f for _, f in feed_profile), pts,
                   [{"z1_mm": round(r["s"], 3), "w_deg": r["w"]} for r in sorted(rotations, key=lambda r: r["s"])])]
    bends[0]["feed_profile"] = [{"z1_mm": round(z, 3), "feed_mm_s": round(f, 3)} for z, f in feed_profile]
    z1_lim = GEOMETRY.z1_max(pr.z2_mm)
    if s_end > z1_lim:
        msg("error", f"Plan endet bei Z1 = {s_end:.1f} mm, Grenze (Futter ↔ Führung) bei {z1_lim:.1f} mm")
    if s_end > AXES["Z1"].max_pos:
        msg("error", f"Plan endet bei Z1 = {s_end:.1f} mm, Software-Endlage {AXES['Z1'].max_pos:.0f} mm")

    # 4) Niederhalter: schließen, wenn ihre Öffnungsposition hinter dem Start liegt
    for b in bends:
        cl, op = [], []
        for nh in range(1, 7):
            lo, _ = GEOMETRY.clamp_zone_z1((nh + 1) // 2)
            zo = lo - GEOMETRY.clamp_open_lead_mm
            if zo > b["z1_start_mm"]:
                cl.append(nh)
                op.append(round(zo, 3))
        b["clamps"], b["clamp_open_mm"] = cl, op

    ok = not any(m["level"] == "error" for m in msgs)
    machine = {
        "format": 1, "plan_id": plan_id, "name": geo.name,
        "material": {"diameter_mm": geo.material.diameter_mm, "wall_mm": geo.material.wall_mm,
                     "yield_mpa": geo.material.yield_mpa, "force_limit_n": geo.material.force_limit_n},
        "bends": bends,
    }
    rep = _report(ok, msgs, el_info)
    rep["profile"] = {"s": [round(x, 3) for x in grid[::max(1, len(grid) // 1500)]],
                      "u": [round(x, 4) for x in u[::max(1, len(grid) // 1500)]],
                      "phi": [round(x, 4) for x in phi[::max(1, len(grid) // 1500)]]}
    rep["centerline"] = centerline(grid, u, v, rotations, z1_start)
    rep["summary"] = {"length_mm": round(s_end - z1_start, 2), "z1_start_mm": round(z1_start, 2),
                      "z1_end_mm": round(s_end, 2), "z1_limit_mm": round(z1_lim, 2), "v_mm": round(v, 3),
                      "bends": sum(1 for e in geo.elements if e.type in ("bend", "bend_var")),
                      "machine_sections": len(bends), "points": sum(len(b["points"]) for b in bends)}
    return (machine if ok else None), rep


def _merge_feed(profile: list[tuple[float, float]], n_max: int) -> list[tuple[float, float]]:
    """Gleiche Nachbarn zusammenfassen; bei mehr als n_max Einträgen benachbarte Bereiche mit dem kleineren
    Vorschub vereinen (immer das Paar mit dem kleinsten Unterschied)."""
    out: list[tuple[float, float]] = []
    for z, f in profile:
        if out and abs(out[-1][1] - f) < 1e-9:
            continue
        if out and abs(out[-1][0] - z) < 1e-9:
            out[-1] = (z, min(out[-1][1], f))
            continue
        out.append((z, f))
    while len(out) > n_max:
        j = min(range(len(out) - 1), key=lambda i: abs(out[i][1] - out[i + 1][1]))
        out[j] = (out[j][0], min(out[j][1], out[j + 1][1]))
        del out[j + 1]
    return out


def _bend(zs, ze, z2, feed, pts, rots) -> dict:
    return {"z1_start_mm": round(zs, 3), "z1_fin_mm": round(ze, 3), "z2_mm": z2, "feed_mm_s": round(feed, 3),
            "points": [{"s_mm": round(s, 3), "u_mm": round(u, 4), "phi_deg": round(p, 4)} for s, u, p in pts],
            "rotations": rots}


def _report(ok, msgs, elements=None) -> dict:
    return {"ok": ok, "messages": msgs, "elements": elements or []}


def centerline(grid, u, v, rotations, z1_start, step=2.0) -> list[list[float]]:
    """Ideale Mittellinie des Biegeteils (Materialrahmen), wie in der Visualisierung integriert.
    Startpunkt 0, Startrichtung +Z, Biegung in Richtung +X bei W = 0."""
    p = [0.0, 0.0, 0.0]
    T, X, Y = [0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    out = [p[:]]
    w = 0.0
    rots = sorted(rotations, key=lambda r: r["s"])
    ri = 0
    acc = 0.0
    for k in range(len(grid) - 1):
        while ri < len(rots) and rots[ri]["s"] <= grid[k] + 1e-9:
            w += rots[ri]["w"]
            ri += 1
        ds = grid[k + 1] - grid[k]
        kap = kappa_from_u((u[k] + u[k + 1]) / 2, v)
        a = math.radians(-w) if kap >= 0 else math.radians(180 - w)
        d = [X[i] * math.cos(a) + Y[i] * math.sin(a) for i in range(3)]
        th = abs(kap) * ds
        if th < 1e-12:
            p = [p[i] + T[i] * ds for i in range(3)]
        else:
            r = 1 / abs(kap)
            p = [p[i] + T[i] * math.sin(th) * r + d[i] * (1 - math.cos(th)) * r for i in range(3)]
            b = [T[1] * d[2] - T[2] * d[1], T[2] * d[0] - T[0] * d[2], T[0] * d[1] - T[1] * d[0]]
            T, X, Y = (_rot(vv, b, th) for vv in (T, X, Y))
        acc += ds
        if acc >= step or th > 0.02 or k == len(grid) - 2:
            out.append([round(c, 3) for c in p])
            acc = 0.0
    return out


def _rot(vv, k, th):
    c, s = math.cos(th), math.sin(th)
    kv = k[0] * vv[0] + k[1] * vv[1] + k[2] * vv[2]
    cr = [k[1] * vv[2] - k[2] * vv[1], k[2] * vv[0] - k[0] * vv[2], k[0] * vv[1] - k[1] * vv[0]]
    return [vv[i] * c + cr[i] * s + k[i] * kv * (1 - c) for i in range(3)]
