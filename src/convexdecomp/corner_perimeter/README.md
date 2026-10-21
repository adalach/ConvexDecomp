# Corner-ray perimeter

`convexdecomp.corner_perimeter` splits a building footprint into perimeter zones along the facades and one or more
core parts, before a convex decomposition cuts what is still not convex. A band 4.5 m deep runs along every facade,
and every corner of the footprint casts a ray into the building along the bisector of its corner. The rays and the
band's inner line cut the footprint into zones, and nine rules clean up what they leave: rays that meet, close
opposite rays, rays that reach nothing, narrow parts of the core, clusters of points, narrow zones, tiny zones and
sharp corners. The two corners at the end of a wing too narrow for two zones side by side cast no ray. No step moves
a point of the footprint's outline, so the zones always tile the footprint exactly.

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
| Wing ends | The two convex corners of an end wall shorter than `wing_end_max_m` whose side walls are parallel within `wing_end_parallel_deg` cast no ray: the wing's zones reach from side to side instead of being split into strips along its axis. | `rays` |
| Rule 1 | Rays that meet merge: the ray that is hit is cut there, and one ray goes on from the meeting point between the two directions to the first line it meets. | `merge_rays` |
| Rule 2 | Two rays running in opposite directions that each pass within `opposite_gap_m` of the other's corner become one line from corner to corner. Decided on the rays as cast. | `opposite` |
| Rule 3 | A ray from a convex corner that reaches the far facade without meeting anything is discarded; a reflex corner's ray is kept and cast again after rule 1. | `discard` |
| Rule 4 | Narrow core becomes lines: legs narrower than `leg_width_m`, notches leaving the core thinner than `notch_passage_m`, and parts, tips and passages thinner than `narrow_core_m`. The shorter side moves onto the longer one, or both half way when their lengths are within `similar_sides_ratio`. | `narrow_core` |
| Zones | The lines are joined (every point lying on a line is inserted into it) and cut the footprint into zones. | |
| Rule 5 | Zone corners closer than `close_points_m` become one. | `close_points` |
| Rule 6 | A four-cornered zone narrower than `narrow_zone_m`, with a perimeter zone across each long side, gives way to its shorter diagonal. | `diagonals` |
| Rule 5 again | The same with `corner_group_m`, in groups at most `corner_group_span_m` across. | `corner_groups` |
| Rule 7 | Zones thinner than `min_zone_width_m` that touch become one; a non-convex union takes the rest of its convex hull from its neighbours if they stay convex. | `tiny_touching` |
| Rule 8 | A lone thin zone merges into a neighbour when the union is convex and wide enough, after relinking one inner edge to a zone corner within `relink_m` if needed. | `tiny_lone` |
| Rule 9 | A zone corner sharper than `sharp_corner_deg` is cut off; the zone across its shorter edge takes the tip. Then rules 7 and 8 once more. | `sharp` |

## Settings

Every value lives in one frozen object, `CornerRayConfig`; its defaults are the settings chosen on 2026-10-20.
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
| `narrow_core_m` | 1 m | 0 to 3 m, step 0.05 | Core thinner than this becomes a line: whole parts (with the switch below), tapered tips and passages between wider parts. |
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

## What a result holds

`PerimeterResult` keeps the footprint, the settings and everything the steps made: `zones` (`(polygon, kind)`
pairs that tile the footprint), `lines` (the corner rays, merged rays and corner-to-corner lines, each with its kind,
what it stops on, and whether it comes from a reflex corner), `band_line` (after rule 4), `core0`/`band0` (as
offset), `core`, `band`, `corners` (the corners that cast rays) and `wing_ends` (those that did not), and the records
of each rule: `pairs`, `discarded`, `legs`, `notches`, `narrow_parts`, `points_merged`, `diagonals`,
`corner_groups`, `tiny_merged`, `lone_merged` (with whether a relink came first) and `sharp_cuts`. `counts()`
gives the numbers.

## Previews for a settings page

`preview(poly, config)` runs the procedure for one building and never raises:

- `layers`: shapely geometries in the footprint's frame, `before` the perimeter (`footprint`) and `after` it
  (`perimeter_zones`, `thin_zones`, `core`, `not_convex` as outlines, `band_line`, `rays`, `merged_rays`,
  `corner_lines`, `wing_ends`), in drawing order; `LAYER_TITLES` names them.
- `numbers`: `NUMBERS` in order, `(key, label, unit)`: corners and wing-end corners, perimeter zones, core parts and
  the core's share of the floor area, thin and non-convex zones, corners under 20°, specks, the narrowest zone, and
  how often each rule acted.
- `fate`: `{"status": "kept"}`, or `{"status": "dropped", "reason": "perimeter_failed", "detail": {"error": ...}}`
  for a footprint the procedure cannot zone (none of the 990 test footprints).
- `seconds` and `result` (the `PerimeterResult`, or `None` when dropped).

`zone_records(result)` gives the zones as rows (`zone`, `kind`, `area_m2`, `min_width_m`, `convex`, `geometry`).

## How it was checked

On the 990 footprints of ConvexDecomp's BEM4AI sample (one process, about 24 s for all on a quiet machine, median
0.01 s and at most 0.45 s per footprint, none failed):

| | Rays from every corner | Wing ends without rays (chosen) |
| --- | --- | --- |
| Perimeter zones | 7,666 | 7,539 |
| Zones thinner than 2 m (footprints with any) | 420 (179) | 263 (157) |
| Zones not convex (footprints with any) | 600 (334) | 584 (326) |
| Corners under 20° | 27 | 21 |
| Specks under 0.5 m² | 48 | 41 |
| Footprints with none of these | 606 | 620 |
| Footprints without a core | 351 | 351 |

The thin zones that stay are small: median 3.7 m² and 1.35 m wide, together 0.4 % of the perimeter zones' area.
The package gives exactly the results of the experiment it was made from (every zone, line and count of the 990
footprints, and every stage of twelve footprints).

`tests/test_corner_perimeter.py` checks four real footprints (`tests/data/corner_perimeter_footprints.json`: their
counts, the tiling, that no outline point moves), the wing ends on a narrow leg and on a round building, simple
shapes, every stage, determinism, the settings table and the preview.

The method, with a figure per rule, is described in the Obsidian note "Convex Decomposition Perimeter";
`tools/corner_perimeter/` draws its figures and runs the sample.
