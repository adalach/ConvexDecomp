# Corner-ray perimeter

`convexdecomp.corner_perimeter` splits a building footprint into perimeter zones along the facades and one or more
core parts, before a convex decomposition cuts what is still not convex. A band 4.5 m deep runs along every facade,
and every corner of the footprint casts a ray into the building along the bisector of its corner. The rays and the
band's inner line cut the footprint into zones, and ten rules clean up what they leave: rays that meet, close
opposite rays, rays that reach nothing, narrow parts of the core, clusters of points, narrow zones, tiny zones,
sharp corners and specks; after each round of the last rules, close zone corners are tidied. The two corners at the
end of a wing too narrow for two zones side by side cast no ray, unless that leaves a thin zone; neither do the
corners of a small bay between its two base corners, and a short wall between two corners casts one ray, not two.
No step moves a point of the footprint's outline, so the zones always tile the footprint exactly. Zones that are not
convex are left for the convex decomposition.

The package needs only numpy, shapely and `convexdecomp.core.scores`; it does not draw.

## Use

```python
from shapely.geometry import box
from convexdecomp.corner_perimeter import CornerRayConfig, corner_ray_perimeter

result = corner_ray_perimeter(box(0, 0, 20, 12))                 # the chosen settings
result = corner_ray_perimeter(poly, CornerRayConfig(band_m=4.0))  # other settings
result = corner_ray_perimeter(poly, upto="narrow_core")           # the state after rule 4

for zone, kind in result.zones:      # kind: zone, thin (narrower than min_zone_width_m) or core
    ...
result.counts()                      # zones, core parts, thin, not convex, sharp, specks, and per rule
```

The footprint is a valid shapely polygon in metres (courtyards allowed), for example a building in its own local
frame. From the command line:

```bash
python -m convexdecomp.corner_perimeter settings                     # the settings table as JSON
python -m convexdecomp.corner_perimeter zones "POLYGON ((...))" band_m=4
```

## The procedure

| Step | What happens | Stage (`upto`) |
| --- | --- | --- |
| Band | The footprint is offset inwards by `band_m` with mitred corners; its inner boundary is the band line, what lies inside it the core. | `band` |
| Corner rays | Every corner casts a ray along its bisector. A ray is cut off at the band line and stops on the first ray it reaches (all rays grow at the same speed; two arriving within `tie_m` both stop). | `rays` |
| Wing ends | The two convex corners of an end wall shorter than `wing_end_max_m` whose side walls are parallel within `wing_end_parallel_deg` cast no ray: the wing's zones reach from side to side instead of being split into strips along its axis. With `wing_end_check`, a wing end whose wing touches a thin zone of the result is tried again with its rays, and kept with them when the footprint's thin, non-convex and sharp zones and specks go down. | `rays` |
| Short walls | With `short_wall_one_ray`, a wall shorter than `short_wall_m` between two corners that both cast rays casts one ray, since the two would bound a needle or a speck: a step (one convex, one reflex corner) only the reflex corner's, a chamfer (two convex corners between walls at least `short_wall_m` long, with `short_wall_chamfers`) one ray from its middle (`short_wall_midpoint`). With `short_wall_to_band` the ray runs to the band line's corner that the dropped ray, or the chamfer's merged ray, would have reached; with `short_wall_tooth` the second reflex corner of a tooth (a convex corner between two short steps) continues the tooth's wall. Wing ends, bays and walls between two reflex corners keep their rays. | `rays` |
| Bays | With `bays`, a bay casts rays only from its two base corners, so the bay and the triangle behind its opening become one zone instead of a fan of thin zones. A bay is a run of at least `bay_min_corners` convex corners (a wiggle reflex by less than `bay_wiggle_deg` counts as convex) between two reflex base corners, whose opening (base corner to base corner) lies inside the footprint and is at most `bay_width_band` band depths long, whose corners all lie outside the opening and at most `bay_depth_band` band depths from it, and where the walls before and after it each run within `bay_edge_deg` of the opening's line (the continuous facade it protrudes from). A bay whose two corners are a wing end stays a wing end; a wing end inside a longer bay is the bay's. With `bays_in_band`, the core is offset from the footprint with its bays cut off at their openings. With `bay_check`, while the footprint keeps a thin, non-convex or sharp zone or a speck, each bay is tried again with its rays, and finally all bays together; a try is kept when those go down. | `rays` |
| Rule 1 | Rays that meet merge: the ray that is hit is cut there, and one ray goes on from the meeting point between the two directions to the first line it meets. | `merge_rays` |
| Rule 2 | Two rays running in opposite directions that each pass within `opposite_gap_m` of the other's corner become one line from corner to corner. Decided on the rays as cast. | `opposite` |
| Rule 3 | A ray from a convex corner that reaches the far facade without meeting anything is discarded; a reflex corner's ray is kept and cast again after rule 1. | `discard` |
| Rule 4 | Narrow core becomes lines: legs narrower than `leg_width_m`, notches leaving the core thinner than `notch_passage_m`, and parts, tips and passages thinner than `narrow_core_m`. The shorter side moves onto the longer one, or both half way when their lengths are within `similar_sides_ratio`. | `narrow_core` |
| Zones | The lines are joined (every point lying on a line is inserted into it) and cut the footprint into zones. | |
| Rule 5 | Zone corners closer than `close_points_m` become one. | `close_points` |
| Rule 6 | A four-cornered zone narrower than `narrow_zone_m`, with a perimeter zone across each long side, gives way to its shorter diagonal. | `diagonals` |
| Rule 5 again | The same with `corner_group_m`, in groups at most `corner_group_span_m` across. | `corner_groups` |
| Rule 7 | Zones thinner than `min_zone_width_m` that touch become one; a non-convex union takes the rest of its convex hull from its neighbours if they stay convex and (with `hull_keeps_width`) at least `min_zone_width_m` wide. Where a neighbour would get thinner, the hull is still taken if the rest of rules 7 and 8 then merge every zone it thins; otherwise the zones merge without the hull. | `tiny_touching` |
| Rule 8 | A lone thin zone merges into a neighbour when the union is convex and wide enough, after relinking one inner edge to a zone corner within `relink_m` if needed. | `tiny_lone` |
| Rule 9 | A zone corner sharper than `sharp_corner_deg` is cut off; the zone along most of its shorter edge takes the tip, or the zone across the longer edge when the shorter one is on the outline. A triangle goes whole to that zone instead when the cut would leave a new sharp corner (its rest a similar triangle, cut again and again) or when the merged zone is convex. Then rules 7 and 8 once more. | `sharp` |
| Rule 10 | With `join_specks`, a perimeter zone under `speck_m2` joins the neighbour it shares the longest edge with, convex or not. With `repeat_until_stable`, rules 9, 7, 8 and 10 (and the tidy step) run again until nothing changes. | `specks` |
| Tidy | At the end of each round: inner zone corners closer than `tidy_close_m` to an outline point move onto it (`snap_to_outline`); inner corners closer than `tidy_close_m` become one, in groups at most `corner_group_span_m` across, at their mean or on one of them (`merge_close_again`); an inner point where a zone's corner is within `straight_deg` of 180° moves onto the line between that corner's neighbours, with the points lying on the two edges (`straighten_corners`, kept only if it leaves fewer zones not convex or fewer near-straight corners); inner points straight within `collinear_deg` in every zone are dropped (`drop_collinear`). Every move applies to all zones sharing the point and is kept only if the zones stay valid and tile the footprint, no outline point moves, no convex zone becomes non-convex, no zone gets thinner than `min_zone_width_m` (a core part `narrow_core_m`) unless it already was, and no corner under `sharp_corner_deg` and no speck appears. With `split_pinched`, a zone that a move makes touch itself becomes two zones if both are convex. | `tidy` |

## Settings

Every value lives in one frozen object, `CornerRayConfig`; its defaults are the settings chosen on 2026-10-20, with the
narrowest core raised from 1 m to 1.2 m and the fixes of 2026-10-23, and the short walls, bays and tidy step of
2026-10-24 switched on.
Each field's metadata holds its section, title, help, unit, the values a run accepts (`ge`, `le`) and a slider
range that holds the default (`lo`, `hi`, `step`); `settings_table()` gives them as plain data, in the sections
below. Values outside `ge`/`le` raise `ValueError`.

**Band and rays**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `band_m` | 4.5 m | 2 to 8 m, step 0.1 | How deep the perimeter band along the facades is: the footprint offset inwards by this distance, with mitred corners. What lies inside the band line is the core. |
| `tie_m` (advanced) | 0.1 m | 0 to 0.5 m, step 0.01 | Rays grow from their corners at the same speed; two rays reaching a crossing within this distance of each other both stop there. |
| `min_turn_deg` (advanced) | 1 deg | 0 to 5 deg, step 0.1 | A vertex where the outline turns by less than this is not a corner and casts no ray. |

**Wing ends**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `wing_end_max_m` | 4 m | 0 to 6 m, step 0.1 | The two convex corners of an end wall shorter than this cast no ray (0 switches the wing ends off). Two zones side by side need twice the zone width. |
| `wing_end_parallel_deg` | 30 deg | 0 to 60 deg, step 1 | Only an end wall whose two side walls are parallel within this angle counts as a wing's end. On a curved facade drawn as many short walls no short wall loses its rays; 180 lets every short wall count. |
| `wing_end_long_sides` (advanced) | off | switch | Also require both side walls to be at least as long as the end wall (a wing, not a step). |
| `wing_end_check` | on | switch | A wing end whose wing touches a zone thinner than the zone width is tried again with its rays; the result with fewer thin, non-convex and sharp zones and specks is kept. |

**Short walls**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `short_wall_one_ray` | on | switch | A wall shorter than the short wall length between two corners that both cast rays casts one ray: a step only its reflex corner's ray, so a line still leaves every reflex corner; a chamfer one ray from the middle of the wall. A wing's end wall and a wall between two reflex corners keep both rays. |
| `short_wall_m` | 1.5 m | 0 to 3 m, step 0.05 | A wall between two corners shorter than this casts one ray. A chamfer counts only when the walls before and after it are at least this long, so on a curve drawn as short walls no wall counts. |
| `short_wall_chamfers` | on | switch | Also a short wall between two convex corners casts one ray; off: only steps. |
| `short_wall_midpoint` (advanced) | on | switch | A chamfer casts its ray from the middle of the wall; off: from the sharper of its two corners. |
| `short_wall_to_band` | on | switch | The one ray runs to the band line's corner that the dropped ray (a step) or the chamfer's two rays, merged, would have reached, so that corner is not left as a reflex corner of a zone. Off, or where the band line is not reached: along the reflex corner's bisector, or the mean of the chamfer's two bisectors. |
| `short_wall_tooth` (advanced) | on | switch | A convex corner between two short steps (a tooth): the reflex corner of the shorter step casts the one ray, the other continues the tooth's wall. |

**Bays**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `bays` | on | switch | A run of convex corners between two reflex base corners, no wider and deeper than below, where the walls on both sides run on along the line between the base corners: the corners of the run cast no ray. |
| `bay_width_band` | 2.5 × band | 0 to 4, step 0.1 | The distance between the two base corners, at most this many band depths. At two band depths the rays of two right-angled base corners meet on the band line. |
| `bay_depth_band` | 1.2 × band | 0 to 2, step 0.05 | How far the bay reaches out from the line between its base corners, at most this many band depths. A whole round building is far deeper than its band and keeps its rays. |
| `bay_edge_deg` | 45 deg | 0 to 90 deg, step 1 | The walls before and after the bay must each run within this angle of the line between its base corners (180 drops the test). |
| `bay_min_corners` | 2 corners | 1 to 6, step 1 | A bay has at least this many corners between its base corners. With 2, a rectangular bump counts too (one with parallel sides narrower than the wing-end width stays a wing end); with 3, only polygonal and rounded bays. |
| `bay_wiggle_deg` (advanced) | 5 deg | 0 to 15 deg, step 0.5 | A corner of the bay may be reflex by less than this (a wiggle where a curve was drawn as short walls). |
| `bays_in_band` | on | switch | The core is offset from the footprint with its bays cut off at their openings, so a bay deeper than the band holds no sliver of core. |
| `bay_check` | on | switch | While the footprint keeps a thin, non-convex or sharp zone or a speck, each bay is tried again with the rays of all its corners; the result with fewer of them is kept. |

**Rays that meet**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `opposite_gap_m` | 1 m | 0 to 3 m, step 0.05 | Rule 2: two rays running in opposite directions that each pass within this distance of the other's corner become one line from corner to corner. |
| `opposite_angle_deg` | 20 deg | 0 to 45 deg, step 1 | Rule 2: how far from exactly opposite the two rays' directions may be. |
| `keep_reflex_rays` | on | switch | Rule 3 keeps a reflex corner's ray and casts it again after rule 1: a zone can only be convex if a line leaves every reflex corner. |
| `recast_discarded` (advanced) | off | switch | Rule 3 off: every ray that reaches nothing is cast again after rule 1, from convex corners too. |

**Narrow core**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `leg_width_m` | 2 m | 0 to 4 m, step 0.05 | A leg of the core (out along one side, across a short tip, back along a nearly parallel side) narrower than this becomes a line. |
| `leg_angle_deg` | 15 deg | 0 to 30 deg, step 0.5 | How far from parallel the two sides of a leg, or a notch's floor and its side, may be. |
| `fill_notches` | on | switch | A notch in the core's side, where it leaves the core thinner than the notch passage, moves onto the line of that side. |
| `notch_passage_m` | 1 m | 0 to 3 m, step 0.05 | A notch is filled where the core across its floor is thinner than this. |
| `narrow_core_m` | 1.2 m | 0 to 3 m, step 0.05 | Core thinner than this becomes a line: whole parts (with the switch below), tapered tips and passages between wider parts. |
| `collapse_narrow_parts` | on | switch | A whole core part thinner than the narrowest core everywhere becomes a line between its two ends. |
| `similar_sides_ratio` | 0.9 | 0 to 1, step 0.05 | The shorter side moves onto the longer one; when the shorter is at least this share of the longer, both move half way. |

**Close points and narrow zones**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `close_points_m` | 0.5 m | 0 to 1.5 m, step 0.05 | Rule 5: zone corners closer than this become one point at their mean. Points on the outline are never moved. |
| `corner_group_m` | 1.2 m | 0 to 3 m, step 0.05 | Rule 5 again, after rule 6: zone corners closer than this become one. |
| `corner_group_span_m` | 2 m | 0 to 5 m, step 0.1 | Rule 5 again: a group of corners merged into one may be at most this wide. |
| `narrow_zone_m` | 2 m | 0 to 4 m, step 0.05 | Rule 6: a four-cornered perimeter zone narrower than this, with a perimeter zone across each long side, gives way to its shorter diagonal. |

**Tiny zones and sharp corners**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `min_zone_width_m` | 2 m | 0 to 4 m, step 0.05 | A perimeter zone narrower than this is thin: rules 7 and 8 merge thin zones, and a merge must leave a zone at least this wide. |
| `relink_m` | 4 m | 0 to 8 m, step 0.25 | Rule 8: an inner edge of a lone thin zone may move one end to a zone corner this close, so the zone can merge into a neighbour. |
| `sharp_corner_deg` | 20 deg | 0 to 45 deg, step 1 | Rule 9: a zone corner sharper than this is cut off; the zone across its shorter edge takes the tip. |
| `sharp_keep_convex` (advanced) | off | switch | Rule 9: skip a cut that would make the convex zone taking the tip non-convex. |
| `repeat_tiny_after_sharp` (advanced) | on | switch | Rules 7 and 8 run once more after rule 9, for thin zones its cuts leave. |
| `hull_keeps_width` | on | switch | Rule 7: a zone giving area to the convex hull of merged thin zones must stay at least the zone width wide (a core part the narrowest core), unless the rest of rules 7 and 8 then merge it. Where it would stay thinner, the thin zones merge without the hull, into a zone that is not convex. |
| `join_specks` | on | switch | Rule 10: a perimeter zone smaller than the speck area joins the neighbour it shares the longest edge with, even if the union is not convex; a zone without area is dropped. |
| `speck_m2` | 0.5 m² | 0 to 2 m², step 0.05 | Rule 10: a perimeter zone smaller than this is a speck. |
| `repeat_until_stable` (advanced) | on | switch | Rules 9, 7, 8 and 10 run again, in turn, until the zones no longer change (at most 8 rounds). |

**Tidy zone corners**

| Setting | Default | Range on a page | What it does |
| --- | --- | --- | --- |
| `snap_to_outline` | on | switch | An inner zone corner closer than the tidy reach to an outline point (a footprint corner or a line end on the facade) moves onto it. The outline point itself never moves. |
| `merge_close_again` | on | switch | Rule 5 once more, on the zones the later rules left: inner zone corners closer than the tidy reach become one (at their mean, or on one of them), in groups at most the widest group across. |
| `tidy_close_m` | 1.5 m | 0 to 3 m, step 0.05 | How close an inner zone corner must be to an outline point to snap onto it, or to another inner corner to become one with it. |
| `split_pinched` | on | switch | Where a tidy move brings two corners of one zone together, the zone falls apart into its two parts, each a zone of its own, if both are convex; otherwise the move is skipped. |
| `straighten_corners` | on | switch | An inner point where a zone's corner is within the tolerance of 180 degrees moves onto the straight line between that corner's two neighbours, in every zone that has it. |
| `straight_deg` | 2 deg | 0 to 5 deg, step 0.1 | A zone corner this close to 180 degrees counts as nearly straight. |
| `drop_collinear` | on | switch | An inner point that is straight in every zone that has it is dropped: no zone has a corner there. |
| `collinear_deg` (advanced) | 0.1 deg | 0 to 1 deg, step 0.01 | A point this close to straight counts as collinear. |

## What a result holds

`PerimeterResult` keeps the footprint, the settings and everything the steps made: `zones` (`(polygon, kind)`
pairs that tile the footprint), `lines` (the corner rays, merged rays and corner-to-corner lines, each with its kind,
what it stops on, and whether it comes from a reflex corner), `band_line` (after rule 4), `core0`/`band0` (as
offset), `core`, `band`, `corners` (the corners that cast rays), `wing_ends` and `bay_corners` (those that did
not), `wing_ends_restored` and `bays_restored` (corners the checks gave their rays back), `short_walls` (the short
walls that cast one ray: kind, wall, length, the corners without a ray, the point that casts it), and the records
of each rule: `pairs`,
`discarded`, `legs`, `notches`, `narrow_parts`, `points_merged`, `diagonals`, `corner_groups`, `tiny_merged`,
`lone_merged` (with whether a relink came first), `sharp_cuts`, `sharp_whole` (triangles rule 9 handed over whole),
`specks_joined`, `rounds` (of rules 9, 7, 8 and 10) and `tidied` (the tidy step's `snapped`, `merged_again`,
`straightened` and `collinear_dropped` points). `counts()` gives the numbers.

## Previews for a settings page

`preview(poly, config)` runs the procedure for one building and never raises:

- `layers`: shapely geometries in the footprint's frame, `before` the perimeter (`footprint`) and `after` it
  (`perimeter_zones`, `thin_zones`, `core`, `not_convex` as outlines, `band_line`, `rays`, `merged_rays`,
  `corner_lines`, `wing_ends`, `bay_corners`), in drawing order; `LAYER_TITLES` names them.
- `numbers`: `NUMBERS` in order, `(key, label, unit)`: corners, wing-end and bay corners, short walls, perimeter zones, core
  parts and the core's share of the floor area, thin and non-convex zones, corners under 20°, specks, the narrowest
  zone, and how often each rule acted.
- `fate`: `{"status": "kept"}`, or `{"status": "dropped", "reason": "perimeter_failed", "detail": {"error": ...}}`
  for a footprint the procedure cannot zone (none of the 990 test footprints).
- `seconds` and `result` (the `PerimeterResult`, or `None` when dropped).

`zone_records(result)` gives the zones as rows (`zone`, `kind`, `area_m2`, `min_width_m`, `convex`, `geometry`).

## How it was checked

On the 990 footprints of ConvexDecomp's BEM4AI sample (one process, about 60 s for all on a shared machine, median
0.02 s and at most 3 s per footprint, none failed; the wing-end and bay checks run the procedure again for some
footprints):

| | Rays from every corner | Wing ends without rays (chosen) |
| --- | --- | --- |
| Perimeter zones | 7,333 | 7,249 |
| Zones thinner than 2 m (footprints with any) | 175 (94) | 92 (73) |
| Zones not convex (footprints with any) | 369 (247) | 362 (241) |
| Corners under 20° | 0 | 0 |
| Specks under 0.5 m² | 0 | 0 |
| Footprints with none of these | 700 | 721 |
| Footprints without a core | 363 | 363 |

The thin zones that stay are small: median 5.2 m² and 1.53 m wide, together 0.2 % of the perimeter zones' area.

How the defaults got there, on the chosen column:

| | Zones | Thin (footprints) | Not convex | Corners under 20° | Specks | Clean |
| --- | --- | --- | --- | --- | --- | --- |
| Settings of 2026-10-20 (narrowest core 1 m) | 7,539 | 263 (157) | 584 | 21 | 41 | 620 |
| Narrowest core 1.2 m | 7,565 | 268 (162) | 574 | 17 | 44 | 624 |
| + rule 9: no cutting down to a point, neighbours within rounding, longer edge | 7,554 | 259 (157) | 576 | 3 | 40 | 625 |
| + `repeat_until_stable` | 7,554 | 258 (157) | 576 | 1 | 40 | 625 |
| + `hull_keeps_width` | 7,557 | 255 (154) | 584 | 0 | 40 | 624 |
| + `join_specks` (rule 10) | 7,515 | 212 (134) | 607 | 0 | 0 | 624 |
| + `wing_end_check` (the defaults of 2026-10-23) | 7,505 | 195 (121) | 607 | 0 | 0 | 635 |
| + rule 7 looks ahead, rule 9 hands a triangle over whole only where a cut runs away or the merge is convex | 7,502 | 195 (121) | 604 | 0 | 0 | 635 |
| + tidy step | 7,503 | 177 (111) | 422 | 0 | 0 | 697 |
| + bays | 7,283 | 116 (87) | 379 | 1 | 0 | 714 |
| + short walls (the defaults) | 7,249 | 92 (73) | 362 | 0 | 0 | 721 |

Each step of 2026-10-24 alone, on top of rule 7's look-ahead and rule 9's hand-over, against the defaults of
2026-10-23 (a footprint is better when it has fewer thin, non-convex or sharp zones or specks and none more, worse
when it has more of any): the tidy step 177 thin and 422 non-convex zones, 140 footprints better and none worse; the
bays 128 and 554, 61 better and 8 worse (each trading a thin zone for a non-convex one or back); the short walls 141
and 532, 73 better and 31 worse. All together: 192 footprints better and 13 worse. Of the 13, six trade thin for
non-convex zones or back; the other seven gain a non-convex zone or thin zones, most Leicester_0039 (three thin
zones at a junction, from the short walls). Rule 4 leaves no core thinner than 1.2 m, but rule 5 (corner groups) can
still thin a small core part afterwards (Pisa_0019 and Povoa_de_Varzim_0014, about 1 m² each). The package gave
exactly the results of the experiment it was made from (every zone, line and count of the 990 footprints, and every
stage of twelve footprints) at the settings of 2026-10-20.

`tests/test_corner_perimeter.py` checks four real footprints and five more for the fixes of 2026-10-23
(`tests/data/corner_perimeter_footprints.json`: their counts with the defaults and with the steps of 2026-10-24
switched off, the tiling, that no outline point moves, and what each fix changes), the wing ends on a narrow leg and
on a round building, bays on a block with a five-sided bay and a rectangular bump and on real footprints, simple
shapes, a triangle rule 9 hands over whole, a neighbour found within rounding, a speck joining its neighbour, every
stage, determinism, the settings table and the preview; `tests/test_corner_perimeter_tidy.py` the tidy step
(straightening, collinear points, snapping to the outline, the test footprints).

The method, with a figure per rule, is described in the Obsidian note "Convex Decomposition Perimeter";
`tools/corner_perimeter/` draws its figures and runs the sample.
