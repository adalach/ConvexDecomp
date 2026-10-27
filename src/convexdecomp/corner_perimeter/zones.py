"""The zones: the lines joined and the footprint cut into zones (:func:`form_zones`), then rules 5 to 10 on them.

- Rule 5, close points become one (:func:`merge_close_points`), runs twice: before rule 6 and after it with a
  longer reach.
- Rule 6, narrow zones give way to a diagonal (:func:`narrow_zones_to_diagonals`).
- Rule 7, touching thin zones merge (:func:`merge_touching_thin_zones`).
- Rule 8, lone thin zones merge into a neighbour (:func:`merge_lone_thin_zones`).
- Rule 9, sharp corners are cut off (:func:`cut_sharp_corners`).
- Rule 10, specks join a neighbour (:func:`join_specks`).

No step moves a point of the footprint's outline, so the zones always tile the footprint exactly.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize

from convexdecomp.core.scores import minimum_width

from .geometry import (EPS, Line, clean_ring, close_groups, convex_corners, corner_points, insert_point, is_convex,
                       kind_of, one_polygon, outline_corners, polygons, shared_length, shares_edge,
                       sharp_corner_count, unit, zone_across)
from .settings import CornerRayConfig

#: rules 9 and 10 find the zone across an edge within this distance: edges that agree only to rounding, or where one
#: carries a point the other does not, still count as shared
ACROSS_TOL = 1e-6


class PerimeterError(RuntimeError):
    """The zones would not tile the footprint."""


def form_zones(poly: Polygon, core, spines: list[LineString], lines: list[Line], cfg: CornerRayConfig):
    """The zones: the lines are noded on each other (:func:`node_lines`) and cut the footprint into faces
    (:func:`zones_from`), each a perimeter zone (``zone``, or ``thin`` when narrower than ``min_zone_width_m``) or a
    core part (``core``). Returns the zones, the core, the band line (core outline and spines) and the lines."""
    core, spines, lines, facade = node_lines(poly, core, spines, lines)
    band_line = shapely.union_all([core.boundary, *spines]) if not core.is_empty or spines else LineString()
    zones = zones_from(poly, core, [*facade, band_line, *(ln.geom for ln in lines)], cfg.min_zone_width_m)
    return zones, core, band_line, lines


def node_lines(poly: Polygon, core, spines: list[LineString], lines: list[Line]):
    """Every point that lies on a line (outline, core outline, spine or line) is inserted into it,
    so each line end joins the line it stops on; points within 1e-7 m of one another become the
    first of them (the footprint corners come first and keep their exact coordinates). A core
    part that flattens to a line stays as a line of the band. Returns the core, spines, lines and
    the facade rings."""
    rings = [poly.exterior, *poly.interiors] + [r for part in polygons(core) for r in [part.exterior, *part.interiors]]
    geoms = [np.asarray(r.coords) for r in rings] + [np.asarray(sp.coords) for sp in spines] \
        + [np.asarray(ln.geom.coords) for ln in lines]
    pts = list(outline_corners(poly))  # one point per place
    for xy in np.vstack(geoms):
        if np.min(np.linalg.norm(np.asarray(pts) - xy, axis=1)) > 1e-9:
            pts.append(xy)
    pts = np.asarray(pts)

    def node(c: np.ndarray) -> np.ndarray:
        """c with every point lying on it inserted, and every point snapped to its place."""
        out = [c[0]]
        for a, b in zip(c[:-1], c[1:]):
            ab = b - a
            L2 = ab @ ab
            if L2 > 0:
                t = np.clip((pts - a) @ ab / L2, 0, 1)
                on = np.linalg.norm(a + t[:, None] * ab - pts, axis=1) < 1e-7
                on &= (t > 1e-9) & (t < 1 - 1e-9)
                out += [pts[k] for k in np.nonzero(on)[0][np.argsort(t[on])]]
            out.append(b)
        snapped = []
        for p in out:
            k = np.argmin(np.linalg.norm(pts - p, axis=1))
            q = pts[k] if np.linalg.norm(pts[k] - p) < 1e-7 else p
            if not snapped or np.linalg.norm(q - snapped[-1]) > 1e-9:
                snapped.append(q)
        return np.array(snapped)

    parts, collapsed = [], []
    for part in polygons(core):
        ext = node(np.asarray(part.exterior.coords))
        holes = [node(np.asarray(h.coords)) for h in part.interiors]
        distinct = [p for k, p in enumerate(ext) if all(np.linalg.norm(p - q) > 1e-9 for q in ext[:k])]
        if len(ext) >= 4 and Polygon(ext).area > 1e-6:
            geom = shapely.make_valid(Polygon(ext, [h for h in holes if len(h) >= 4]))
        elif len(distinct) >= 2:  # the part collapsed: what is left of it is a line
            geom = LineString(distinct)
        else:
            continue
        parts += polygons(geom)
        for g in getattr(geom, "geoms", [geom]):
            for ln in getattr(g, "geoms", [g]):
                if ln.geom_type == "LineString" and ln.length > 1e-6:
                    collapsed.append(ln)
    new_core = shapely.union_all(parts) if parts else Polygon()
    new_spines = [LineString(c) for c in (node(np.asarray(sp.coords)) for sp in spines) if len(c) >= 2]
    new_spines += collapsed
    new_lines = []
    for ln in lines:
        c = node(np.asarray(ln.geom.coords))
        if len(c) >= 2:
            ln.geom = LineString(c)
            new_lines.append(ln)
    facade = [LineString(node(np.asarray(r.coords))) for r in [poly.exterior, *poly.interiors]]
    return new_core, new_spines, new_lines, facade


def zones_from(poly: Polygon, core, linework: list, min_width_m: float) -> list[tuple[Polygon, str]]:
    """The faces the outline, band line, spines and lines (all noded on each other) cut the
    footprint into."""
    out = []
    for f in polygonize(shapely.unary_union(linework)):
        if not poly.contains(f.representative_point()):
            continue  # a courtyard
        out.append((f, "core" if not core.is_empty and shapely.intersection(f, core).area > 0.5 * f.area
                    else kind_of(f, "zone", min_width_m)))
    if abs(sum(f.area for f, _ in out) - poly.area) >= 1e-3 * poly.area:
        raise PerimeterError("the zones do not tile the footprint")
    return out


def merge_close_points(poly: Polygon, zones: list[tuple[Polygon, str]], reach_m: float, span_m: float,
                       min_width_m: float):
    """Rule 5 (``close_points_m``, any span), and rule 5 again (``corner_group_m``, ``corner_group_span_m``): zone
    corners gathered in one place become one vertex in their middle. Groups grow from the closest pairs (closer than
    reach_m), up to span_m across (:func:`~.geometry.close_groups`). Points on the outline (footprint corners, line
    ends on the facade) are left alone: never moved, never merged. A group is skipped if moving it would make a zone
    invalid or break the tiling. Returns the zones and the merged vertices."""
    pts, fixed = corner_points(poly, zones)
    groups = close_groups(pts, fixed, reach_m, span_m)

    def node(c: np.ndarray) -> np.ndarray:
        """The ring c with every point that lies on one of its edges inserted."""
        out = []
        for a, b in zip(c, np.roll(c, -1, axis=0)):
            out.append(a)
            ab = b - a
            t = np.clip((pts - a) @ ab / (ab @ ab), 0, 1)
            on = (np.linalg.norm(a + t[:, None] * ab - pts, axis=1) < 1e-7) & (t > 1e-9) & (t < 1 - 1e-9)
            out += [pts[k] for k in np.nonzero(on)[0][np.argsort(t[on])]]
        return np.asarray(out)

    rings = [[node(np.asarray(z.exterior.coords)[:-1]), *(node(np.asarray(h.coords)[:-1]) for h in z.interiors)]
             for z, _ in zones]
    kinds = [k for _, k in zones]
    total = poly.area
    merged = []
    for members in groups:
        m = np.asarray(members)
        t = pts[m].mean(axis=0)
        trial = []
        for rs in rings:
            new_rs = []
            for c in rs:
                hit = np.min(np.linalg.norm(c[:, None] - pts[m][None], axis=2), axis=1) < 1e-7
                c = np.where(hit[:, None], t, c)
                keep = np.linalg.norm(c - np.roll(c, 1, axis=0), axis=1) > 1e-9
                new_rs.append(c[keep])
            trial.append(new_rs)
        polys = [Polygon(rs[0], [h for h in rs[1:] if len(h) >= 3]) if len(rs[0]) >= 3 else Polygon() for rs in trial]
        if all(p.is_empty or p.area < 1e-9 or p.is_valid for p in polys) and \
                abs(sum(p.area for p in polys if p.is_valid) - total) < 1e-6 * total:
            rings = trial
            merged.append(t)
    out = []
    for rs, k in zip(rings, kinds):
        if len(rs[0]) < 3:
            continue
        p = Polygon(rs[0], [h for h in rs[1:] if len(h) >= 3])
        if p.area < 1e-9:
            continue
        out.append((p, kind_of(p, k, min_width_m)))
    return out, merged


def narrow_zones_to_diagonals(zones: list[tuple[Polygon, str]], cfg: CornerRayConfig):
    """Rule 6, narrowest zone first: a four-cornered perimeter zone narrower than ``narrow_zone_m`` with a
    perimeter zone across each long side is cut along its shorter diagonal, and each half joins
    the zone on its side. Returns the zones and the diagonals."""
    zones = list(zones)
    diagonals: list[LineString] = []
    tried: set[bytes] = set()
    while True:
        narrow = sorted((i for i, (z, k) in enumerate(zones)
                         if k != "core" and minimum_width(z) < cfg.narrow_zone_m and z.wkb not in tried),
                        key=lambda i: minimum_width(zones[i][0]))
        if not narrow:
            return zones, diagonals
        i = narrow[0]
        z = zones[i][0]
        tried.add(z.wkb)
        c = clean_ring(np.asarray(orient(z, 1.0).exterior.coords)[:-1])
        if len(c) != 4 or z.interiors:
            continue
        edges = [LineString([c[t], c[(t + 1) % 4]]) for t in range(4)]
        t = 0 if edges[0].length + edges[2].length >= edges[1].length + edges[3].length else 1
        across = []
        for e in (edges[t], edges[t + 2]):  # the zone sharing most of each long side
            share, j = zone_across(zones, i, e)
            across.append(j if share > 0.5 * e.length and zones[j][1] != "core" else None)
        if None in across or across[0] == across[1]:
            continue
        diag = min((LineString([c[0], c[2]]), LineString([c[1], c[3]])), key=lambda d: d.length)
        halves = [h for h in polygonize(shapely.unary_union([z.exterior, diag])) if h.area > 1e-9]
        if len(halves) != 2:
            continue
        new = {}
        for e, j in zip((edges[t], edges[t + 2]), across):
            half = max(halves, key=lambda h: shapely.intersection(h.boundary, e).length)
            merged = shapely.union_all([zones[j][0], half])
            if merged.geom_type != "Polygon" or (is_convex(zones[j][0]) and not is_convex(merged)):
                break  # keep the zone rather than make a convex neighbour non-convex
            new[j] = merged
        if len(new) != 2:
            continue
        for j, p in new.items():
            zones[j] = (p, kind_of(p, "zone", cfg.min_zone_width_m))
        del zones[i]
        diagonals.append(diag)


def merge_touching_thin_zones(poly: Polygon, zones: list[tuple[Polygon, str]], cfg: CornerRayConfig):
    """Rule 7: zones thinner than ``min_zone_width_m`` that share an edge become one zone. A merged zone that is not
    convex takes the rest of its convex hull from the zones around it, if the hull stays inside the footprint and
    every zone giving area stays one polygon and, if it was convex, convex; otherwise the group stays as it is.

    With ``hull_keeps_width``, a zone giving area must also stay at least the zone width wide (a core part the
    narrowest core; a zone already thinner, as wide as it was). Where one would get thinner, the hull is still taken
    if the rest of rules 7 and 8 then merge every zone it thins (looked ahead on a copy, without the width check);
    otherwise the group merges without its hull and is not convex, for the convex decomposition to cut. Returns the
    zones and the merged zones."""
    zones = list(zones)

    def touching_thin_groups() -> list[list[int]]:
        thin = [i for i, (_, k) in enumerate(zones) if k == "thin"]
        group = {i: [i] for i in thin}
        for a in thin:
            for b in thin:
                if a < b and group[a] is not group[b] and shares_edge(zones[a][0], zones[b][0]):
                    both = group[a] + group[b]
                    for k in both:
                        group[k] = both
        return [g for g in {id(g): g for g in group.values()}.values() if len(g) > 1]

    merged: list[Polygon] = []
    tried: set[bytes] = set()
    while True:
        groups = [g for g in touching_thin_groups()
                  if shapely.union_all([zones[i][0] for i in g]).wkb not in tried]
        if not groups:
            return zones, merged
        members = groups[0]
        union = one_polygon(shapely.union_all([zones[i][0] for i in members]))
        tried.add(shapely.union_all([zones[i][0] for i in members]).wkb)
        if union is None:
            continue
        new = union
        changes = {}
        if not is_convex(union):
            hull = union.convex_hull
            if hull.difference(poly).area > 1e-6:
                continue  # the hull reaches outside the footprint
            extra = hull.difference(union)
            ok, thinned = True, []
            for j, (z, k) in enumerate(zones):
                if j in members or shapely.intersection(z, extra).area <= 1e-6:
                    continue
                rest = one_polygon(z.difference(extra))
                if rest is None or not rest.is_valid or (is_convex(z) and not is_convex(rest)):
                    ok = False
                    break
                limit = cfg.narrow_core_m if k == "core" else cfg.min_zone_width_m
                if cfg.hull_keeps_width and minimum_width(rest) < min(limit, minimum_width(z)) - 1e-9:
                    thinned.append(rest)  # the hull would leave this zone too thin
                changes[j] = rest
            if not ok:
                continue
            if thinned:  # take the hull only if the rest of rules 7 and 8 then merge every zone it thins
                trial = [(changes.get(j, z), kind_of(changes[j], k, cfg.min_zone_width_m) if j in changes else k)
                         for j, (z, k) in enumerate(zones) if j not in members]
                trial.append((hull, kind_of(hull, "zone", cfg.min_zone_width_m)))
                plain = replace(cfg, hull_keeps_width=False)
                after, _ = merge_lone_thin_zones(poly, merge_touching_thin_zones(poly, trial, plain)[0], plain)
                still_thin = [z for z, k in after if k == "thin"]
                if any(any(z.covers(r.representative_point()) for z in still_thin) for r in thinned):
                    changes = {}  # merge without the hull
                else:
                    new = hull
            else:
                new = hull
        for j, rest in changes.items():
            zones[j] = (rest, kind_of(rest, zones[j][1], cfg.min_zone_width_m))
        zones = [zk for i, zk in enumerate(zones) if i not in members]
        zones.append((new, kind_of(new, "zone", cfg.min_zone_width_m)))
        merged.append(new)


def merge_lone_thin_zones(poly: Polygon, zones: list[tuple[Polygon, str]], cfg: CornerRayConfig):
    """Rule 8: a zone thinner than 2 m with no thin neighbour merges into a neighbouring perimeter
    zone when the union is convex and at least 2 m wide (the widest such union). Where no neighbour
    gives that, one inner edge of the zone is first relinked: an end of the edge moves to a zone
    corner within ``relink_m``, the triangle between the old and the new edge changes zone, and the
    zone then merges as above. A relink is allowed only if the new edge stays inside the footprint,
    the triangle has no side on the outline, and the zone giving or taking the triangle stays one
    polygon, at least 2 m wide, and convex if it was. Returns the zones and, for every merge, the
    merged zone and whether a relink came first."""
    zones = list(zones)
    width_ok = cfg.min_zone_width_m

    def best_merge(zs, i):
        """(width, j, union) of the widest convex union of zone i with a perimeter neighbour."""
        best = None
        for j, (n, k) in enumerate(zs):
            if j == i or k == "core" or not shares_edge(zs[i][0], n):
                continue
            u = one_polygon(shapely.union_all([zs[i][0], n]))
            if u is not None and is_convex(u) and minimum_width(u) >= width_ok:
                if best is None or minimum_width(u) > best[0]:
                    best = (minimum_width(u), j, u)
        return best

    def relinks(zs, i):
        """Every allowed relink of an inner edge of zone i: the zones after it."""
        t = zs[i][0]
        corners = np.vstack([np.asarray(z.exterior.coords)[:-1] for z, _ in zs])
        c = np.asarray(t.exterior.coords)[:-1]
        for k in range(len(c)):
            u, v = c[k], c[(k + 1) % len(c)]
            edge = LineString([u, v])
            if poly.boundary.distance(edge.interpolate(0.5, normalized=True)) < EPS:
                continue  # an edge on the outline is never relinked
            for fixed, moved in ((u, v), (v, u)):
                for w in corners[np.linalg.norm(corners - moved, axis=1) <= cfg.relink_m]:
                    if min(np.linalg.norm(w - moved), np.linalg.norm(w - fixed)) < 1e-6:
                        continue
                    tri = Polygon([fixed, moved, w])
                    if tri.area < 0.05 or not poly.buffer(EPS).covers(LineString([fixed, w])) \
                            or shapely.intersection(tri, poly.boundary).length > EPS:
                        continue
                    if shapely.intersection(tri, t).area > tri.area * (1 - 1e-6):  # the zone gives it
                        m = next((j for j, (z, _) in enumerate(zs) if j != i and shares_edge(z, edge)), None)
                        if m is None:
                            continue
                        t2, m2 = one_polygon(t.difference(tri)), one_polygon(shapely.union_all([zs[m][0], tri]))
                    else:  # the zone takes it from the zone it lies in
                        m = next((j for j, (z, _) in enumerate(zs)
                                  if j != i and shapely.intersection(z, tri).area > tri.area * (1 - 1e-6)), None)
                        if m is None:
                            continue
                        t2, m2 = one_polygon(shapely.union_all([t, tri])), one_polygon(zs[m][0].difference(tri))
                    if t2 is None or m2 is None or not m2.is_valid or minimum_width(m2) < width_ok \
                            or (is_convex(zs[m][0]) and not is_convex(m2)) or zs[m][1] == "core":
                        continue
                    out = list(zs)
                    out[i], out[m] = (t2, kind_of(t2, "zone", width_ok)), (m2, kind_of(m2, zs[m][1], width_ok))
                    yield out

    merged: list[tuple[Polygon, bool]] = []
    tried: set[bytes] = set()
    while True:
        lone = [i for i, (z, k) in enumerate(zones) if k == "thin" and z.wkb not in tried
                and not any(kk == "thin" and shares_edge(z, zz) for j, (zz, kk) in enumerate(zones) if j != i)]
        if not lone:
            return zones, merged
        i = lone[0]
        tried.add(zones[i][0].wkb)
        choice = None
        direct = best_merge(zones, i)
        if direct is not None:
            choice = (zones, i, direct, False)
        else:
            for zs in relinks(zones, i):
                m = best_merge(zs, i)
                if m is not None and (choice is None or m[0] > choice[2][0]):
                    choice = (zs, i, m, True)
        if choice is None:
            continue
        zs, i, (_, j, u), relinked = choice
        zones = [zk for k, zk in enumerate(zs) if k not in (i, j)] + [(u, kind_of(u, "zone", width_ok))]
        merged.append((u, relinked))


def taker_across(zones: list[tuple[Polygon, str]], i: int, edge: LineString) -> int | None:
    """Rule 9: the zone that takes a tip across edge: of the zones lying along it (within ``ACROSS_TOL``), the one
    along most of it; None when zones lie along less than half of it (the rest is outline)."""
    shares = [(shared_length(zones[j][0].boundary, edge, ACROSS_TOL), j) for j in range(len(zones)) if j != i]
    if sum(s for s, _ in shares) < 0.5 * edge.length:
        return None
    return max(shares)[1]


def _cut_off_tip(z: Polygon, a: np.ndarray, b: np.ndarray, far: np.ndarray):
    """Rule 9's cut of zone z at its corner a, on z's own ring. a and its neighbours b and far come from the cleaned
    ring, which leaves out points the outline runs almost straight through; those points stay in z's ring and in the
    tip. P is where the perpendicular from b meets z's ring on the way from a to far, so it lies exactly on that edge.
    Returns the rest's ring (z with the tip replaced by P), the tip (from b along the ring through a to P) and P; None
    when P would lie beyond far."""
    ring = np.asarray(orient(z, 1.0).exterior.coords)[:-1]
    n = len(ring)
    ia, ib, ifar = (int(np.flatnonzero(np.all(ring == q, axis=1))[0]) for q in (a, b, far))
    step = 1 if (ib - ia) % n < (ifar - ia) % n else -1      # the way round from a to b
    w = unit(far - a)
    s = (b - a) @ w                                           # how far P lies from a along a-far
    if s > (far - a) @ w + 1e-12:
        return None
    to_b = [(ia + step * m) % n for m in range(1, (ib - ia) * step % n)]
    to_p, prev, k = [], a, (ia - step) % n                    # the points from a towards far before P
    while (ring[k] - a) @ w < s - 1e-12:
        to_p.append(k)
        prev, k = ring[k], (k - step) % n
    q = ring[k]
    d0, d1 = (prev - a) @ w, (q - a) @ w
    p = prev + (s - d0) / (d1 - d0) * (q - prev) if d1 - d0 > 1e-15 else q
    on_point = bool(np.linalg.norm(p - q) < 1e-9)             # P is the ring's next point itself
    if on_point:
        p = q
    gone = {ia, *to_b, *to_p, *([k] if on_point else [])}
    first = next(m for m in range(n) if m not in gone and (m - step) % n in gone)
    kept = [(first + step * m) % n for m in range(n - len(gone))]
    rest = [p, *(ring[m] for m in kept)]                      # P, then round from the first point kept
    if step == -1:
        rest = rest[::-1]
    tip = [b, *(ring[m] for m in reversed(to_b)), a, *(ring[m] for m in to_p), p]
    return rest, Polygon(tip), p


def cut_sharp_corners(zones: list[tuple[Polygon, str]], cfg: CornerRayConfig):
    """Rule 9: a zone corner A sharper than ``sharp_corner_deg`` is cut off along BP, from the far end B of the
    shorter edge AB straight across to the longer edge AC (P, the foot of the perpendicular).
    The zone across AB takes the tip ABP, or, when AB lies on the outline, the zone across AP (:func:`taker_across`,
    within ``ACROSS_TOL``); the cut zone keeps the rest (A moves to P), and P is inserted into the zones whose edges
    it lies on, so the zones stay noded. A triangle goes whole to the zone taking the tip when the cut would leave a
    new corner sharper than the limit at B (its rest a similar triangle, cut again and again down to a point) or when
    the merged zone is convex. The cut is made on the zone's own ring (:func:`_cut_off_tip`), so P lies on the edge
    the zone across AC shares. Skipped when no zone lies across either edge, or the result would be invalid or not
    keep every area exact. Returns the zones, the cuts and the zones handed over whole."""
    zones = list(zones)
    cuts: list[LineString] = []
    whole: list[Polygon] = []
    tried: set[tuple] = set()
    while True:  # the sharpest corner first
        sharp = []
        for i, (z, _) in enumerate(zones):
            c, angles = convex_corners(z)
            for angle, t in angles:
                key = (round(c[t][0], 6), round(c[t][1], 6), round(z.area, 6))
                if angle < cfg.sharp_corner_deg and key not in tried:
                    sharp.append((angle, i, t, c, key))
        if not sharp:
            return zones, cuts, whole
        _, i, t, c, key = min(sharp, key=lambda s_: s_[0])
        tried.add(key)
        a, x, y = c[t], c[t - 1], c[(t + 1) % len(c)]
        b, far = (x, y) if np.linalg.norm(x - a) <= np.linalg.norm(y - a) else (y, x)
        cut = _cut_off_tip(zones[i][0], a, b, far)
        if cut is None:
            continue
        rest_ring, tip, p = cut
        j = taker_across(zones, i, LineString([a, b]))
        if j is None:  # the shorter edge is on the outline: the zone across the longer edge takes the tip
            j = taker_across(zones, i, LineString([a, p]))
            if j is None:
                continue  # nobody to take the tip
        rest = Polygon(rest_ring, [h.coords for h in zones[i][0].interiors])
        rc, rest_angles = convex_corners(rest)
        at_b = [angle for angle, s in rest_angles if np.linalg.norm(rc[s] - b) < 1e-9]
        whole_taker = one_polygon(shapely.union_all([zones[j][0], zones[i][0]])) \
            if len(c) == 3 and not zones[i][0].interiors else None
        runaway = bool(at_b) and at_b[0] < cfg.sharp_corner_deg  # the cut would make a new sharp corner at B
        if whole_taker is not None and whole_taker.is_valid and (runaway or is_convex(whole_taker)):
            taker = whole_taker
            zones[j] = (taker, kind_of(taker, zones[j][1], cfg.min_zone_width_m))
            whole.append(zones[i][0])
            del zones[i]
            continue
        taker = shapely.union_all([zones[j][0], tip])
        if not rest.is_valid or rest.area <= 0 or taker.geom_type != "Polygon" or not taker.is_valid:
            continue
        tol = 1e-9 * zones[i][0].area
        if abs(rest.area + tip.area - zones[i][0].area) > tol or abs(taker.area - zones[j][0].area - tip.area) > tol:
            continue  # the cut would leave a gap or an overlap
        if cfg.sharp_keep_convex and is_convex(zones[j][0]) and not is_convex(taker):
            continue  # the zone taking the tip would lose its convexity
        zones[i] = (rest, kind_of(rest, zones[i][1], cfg.min_zone_width_m))
        zones[j] = (taker, kind_of(taker, zones[j][1], cfg.min_zone_width_m))
        for k, (z, kind) in enumerate(zones):  # P lies on the edge AC of the zone across it: insert it there
            if k not in (i, j):
                zones[k] = (insert_point(z, p), kind)
        cuts.append(LineString([b, p]))


def join_specks(zones: list[tuple[Polygon, str]], cfg: CornerRayConfig):
    """Rule 10, smallest first: a perimeter zone under ``speck_m2`` joins the neighbour it shares the longest edge
    with (a perimeter zone if it has one, else the core), even if the union is not convex: the convex decomposition
    cuts that later. A zone without area (under 1e-6 m²) is dropped. Returns the zones and the joined zones."""
    zones = list(zones)
    joined: list[Polygon] = []
    skipped: set[bytes] = set()
    while True:
        specks = sorted((i for i, (z, k) in enumerate(zones) if k != "core" and z.area < cfg.speck_m2
                         and z.wkb not in skipped), key=lambda i: zones[i][0].area)
        if not specks:
            return zones, joined
        i = specks[0]
        z = zones[i][0]
        if z.area < 1e-6:
            del zones[i]
            continue
        near = sorted(((zones[j][1] == "core", -shared_length(z.boundary, n.boundary, ACROSS_TOL), j)
                       for j, (n, _) in enumerate(zones) if j != i))
        for _, minus_share, j in near:
            if -minus_share <= 1e-6:
                continue
            u = one_polygon(shapely.union_all([zones[j][0], z]))
            if u is None or not u.is_valid:
                continue
            zones[j] = (u, kind_of(u, zones[j][1], cfg.min_zone_width_m))
            del zones[i]
            joined.append(u)
            break
        else:
            skipped.add(z.wkb)


#: an arm of a zone this narrow and at least ``ARM_LENGTH_M`` long is counted by :func:`narrow_arms`
ARM_WIDTH_M = 0.3
ARM_LENGTH_M = 1.0
#: and at least this large (smaller parts are what opening a zone leaves along its edges by rounding)
ARM_MIN_AREA_M2 = 0.05


def narrow_arms(zones: list[tuple[Polygon, str]], width_m: float = ARM_WIDTH_M,
                length_m: float = ARM_LENGTH_M) -> list[Polygon]:
    """The zones' arms narrower than width_m and at least length_m long: the parts that opening a zone by half the
    width (shrinking it and growing it back, with mitred corners) takes away, whose mean width (twice the area over
    the perimeter) is under width_m, whose half perimeter is at least length_m and whose area is at least
    ``ARM_MIN_AREA_M2``. A check: no rule acts on them."""
    arms: list[Polygon] = []
    for z, _ in zones:
        try:
            opened = z.buffer(-width_m / 2, join_style="mitre", mitre_limit=10.0).buffer(
                width_m / 2, join_style="mitre", mitre_limit=10.0)
            rest = z.difference(opened)
        except shapely.errors.GEOSException:
            continue
        arms += [q for q in polygons(rest) if q.area >= ARM_MIN_AREA_M2 and 2.0 * q.area / q.length < width_m
                 and q.length / 2.0 >= length_m]
    return arms


def sharp_corners(zones: list[tuple[Polygon, str]], deg: float = 20.0) -> int:
    """How many zone corners are sharper than deg."""
    return sum(sharp_corner_count(z, deg) for z, _ in zones)


def zone_counts(zones: list[tuple[Polygon, str]]) -> dict:
    """Perimeter zones and core parts, and how many perimeter zones are thin, not convex or specks (under 0.5 m²);
    corners sharper than 20°."""
    pz = [z for z, k in zones if k != "core"]
    return dict(zones=len(pz), core_parts=len(zones) - len(pz), thin=sum(k == "thin" for _, k in zones),
                not_convex=sum(not is_convex(z) for z in pz), sharp_20=sharp_corners(zones, 20.0),
                specks=sum(z.area < 0.5 for z in pz))
