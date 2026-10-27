# Convex zones

`convexdecomp.convex_zones` turns a building's perimeter zones into convex zones. Every zone of the corner-ray
perimeter (`convexdecomp.corner_perimeter`) goes to the engine (`convexdecomp.osm.decompose`) as it is: a convex
zone comes back as one zone, the others are cut. A zone the engine cannot cut (a zone thinner than the narrowest
zone allowed, or one with a narrow arm) is merged with a neighbouring zone and the union decomposed; when merging
gives up, the whole footprint is decomposed instead. The convex zones always tile the footprint exactly.

This is the decomposition step of `notebooks/OSM_2_Decomposition.ipynb` for one building, with its settings as one
object with page metadata, and a preview for a settings page. The notebook's `run_building_decomposition(...,
fallback="merge")` (`convexdecomp.osm.diagnostics`) runs the same code for a table of buildings.

The package needs only numpy, shapely and ConvexDecomp's own engine and perimeter; it does not draw.

## Use

```python
from shapely.geometry import box
from convexdecomp.corner_perimeter import corner_ray_perimeter
from convexdecomp.convex_zones import ZoneConfig, decompose_zones

footprint = box(0, 0, 20, 12)
perimeter = corner_ray_perimeter(footprint)
result = decompose_zones(perimeter.zones, footprint=footprint)                  # the default settings
result = decompose_zones(perimeter.zones, ZoneConfig(min_zone_width_m=1.5), footprint=footprint)

result.ok                    # every piece has a convex decomposition
result.fallback              # None, "merge" or "whole"
for zone, k in result.parts:  # the convex zones, each with its piece in result.pieces
    piece = result.pieces[k]  # kind (zone, thin, core, merged, footprint), sources (the perimeter zones it holds)
```

`zones` are `(polygon, kind)` pairs as `PerimeterResult.zones` holds them (kinds `zone`, `thin`, `core`), or zones
edited by hand in the same form; a plain polygon counts as a zone. `footprint` is the outline the whole-footprint
fallback decomposes (the union of the zones when it is not given). From the command line:

```bash
python -m convexdecomp.convex_zones settings                           # the settings table as JSON
python -m convexdecomp.convex_zones zones "POLYGON ((...))" min_zone_width_m=1.5
```

## How a building is decomposed

1. **Every zone on its own.** The zones are searched in their order with the core parts last. The engine's search
   for one zone is a beam search over the ways to cut it: a cut starts at a reflex corner and runs along one of its
   two walls carried on, or along the bisector of its angle, to the first wall it meets. No cut may leave a zone
   thinner than 1.2 m or smaller than 2 m², and no cut may make a corner under 30° unless no cut of the piece passes
   that rule. Of the convex variants found, the one with the lowest score is kept: the number of zones plus 20 times
   one minus their mean compactness.
2. **Merge a zone that cannot be cut.** The first zone without a decomposition is tried with each neighbour (a zone
   sharing at least 0.05 m of edge), longest shared edge first; the first union that decomposes replaces the two.
   When none does, the zone is merged with the neighbour it shares most edge with and tried again from there, as
   long as the merged piece holds at most half the building's floor area. This repeats until every piece is
   decomposed.
3. **The whole footprint.** When merging gives up, the whole footprint is decomposed instead of the zones. When that
   fails too, the building keeps its zones as they were and has no decomposition.

Steps 2 and 3 together have a time budget, 10 s by default: every search stops when the time is up, and a building
not decomposed by then has no decomposition (`BuildingZones.out_of_time`). The first pass has no limit.

A footprint the perimeter cannot zone is decomposed whole as one piece of kind `footprint`, with no fallback.
`decompose_pieces(zones, decompose, ...)` is the same with the engine call given as a function and the fallback's
settings as arguments: `decompose(polygon, max_seconds)` returns the engine's result dict for the polygon, its search
stopped after `max_seconds` (None in the first pass, the time left of the budget in the fallback).
`engine_call(cfg)` makes one from an engine config; a caller that stores every piece's search passes its own.

## Zones that need no search

A zone that is convex (the engine's own test) and at least as wide as the narrowest zone goes to the engine and comes
back as it is, with no search: `is_final(zone, config)`. A convex zone thinner than that has no decomposition of its
own and goes to the fallback; a convex zone under the smallest zone area is final (only cuts must not make zones that
small). `already_convex(zones, config)` says that every zone of a building is final: `decompose_zones` then gives
exactly its zones, each as one convex zone, with no fallback. On the 990-building working set 7,168 of the 8,026
zones are final and 533 buildings are already convex; every final zone came back unsearched, and every other zone
was searched or, 23 thin convex zones, refused.

## Variants and choices

The engine keeps every convex variant it finds for a piece, best first, in its result (`ranked_variants`: `parts`,
`score`, `fallback_used`): ranked by score, a variant without fallback cuts first among equal scores, then in the
order the search found them, so the first is the engine's choice. `variant_records(piece.stats, config)` gives them
as rows: `rank`, `fingerprint`, `score`, `terms` and `shares` (each term of the score and its share of it, as the
search explorer's list of variants shows them; the terms add up to the score), `zones`, `narrowest_m`,
`smallest_m2`, `fallback_used` and `parts`. On the working set 462 of 7,960 pieces have more than one variant, at
most 10.

`fingerprint(polygon or parts)` names a piece or a variant by its geometry (normalized, rounded to a micrometre), so
a choice can be found again after the building is decomposed again with the same settings. `decompose_zones`,
`decompose_pieces` and `preview` take `choices={piece fingerprint: variant fingerprint}`: the piece keeps that
variant instead of the engine's choice (`BuildingZones.chosen` records the rank kept, `Piece.variant` too). A choice
whose piece or variant is not there (other settings, or a zone the merge fallback merged away) is listed in
`BuildingZones.choices_missing` and changes nothing. A merged piece and a polygon of the whole footprint have
fingerprints of their own and take choices like any piece.

## The search tree

`search_tree(piece, config)` records the engine's search of one piece with the settings of `config`
(`convexdecomp.osm.search_trace`: the traced run is the same run, with the same states, variants and choice) and
returns it as plain data for a page: the states (`nodes`, depth first, children in the order the search generated
them), each with its parent, depth, the cut that made it, what the search did with it (`status`), the pieces it holds
and those a thumbnail shows in colour, and its variant's rank for a success; the ranked variants (`variants`, as
`variant_records` with the parts as piece numbers, each with the id of its state); every distinct piece once
(`pieces`: rings, area, compactness, width, convexity); the search's counts (`stats`). `max_states` keeps the first
states in generation order and the path to every variant (`truncated` says how many it left out); `max_seconds`
stops the search. It needs Python 3.12 or later. On the working set most searched pieces have tens of states; the
largest, a core part of osm_0321, has 1,724 (2.9 s, 0.8 MB as JSON).

## Settings

`ZoneConfig` holds every setting; the defaults are those of `OSM_2_Decomposition.ipynb`, with a time budget for the
fallback that the notebook leaves off. `engine_config(config)`
gives the engine's `ConvexDecompositionConfig`; `engine_config(config, **PAPER_2026_SEARCH)` searches as in the
EG-ICE 2026 paper. `settings_table()` gives the five sections below as plain data, each field with its kind
(`switch`, `integer` or `number`), default, title, help, unit, the values a run accepts (`ge`, `le`), a slider range
that holds the default (`lo`, `hi`, `step`), the switch it `needs` and whether it is `advanced`.

**Zone rules.** What every convex zone must satisfy. A cut that would leave a zone thinner than the narrowest zone
or smaller than the smallest zone is never made. The corner rule is relaxed where no cut of a piece passes it.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `min_zone_width_m` | 1.2 m | 0 to 3 | **Narrowest zone.** No zone may be thinner than this: its smallest width in any direction. A hard rule: a piece that cannot be cut into zones this wide, a thin convex piece included, has no decomposition of its own. 0 turns the rule off. |
| `min_corner_deg` | 30 deg | 0 to 60 | **Sharpest new corner.** No cut may make a corner sharper than this; the outline's own sharp corners never block a cut. Relaxed where no cut of a piece passes it. 0 turns the rule off. |
| `min_area_m2` | 2 m² | 0 to 10 | **Smallest zone.** No cut may leave a zone smaller than this. A convex piece that is smaller stays as it is (one under 0.5 m² has no decomposition). |

**Score.** Of the convex variants the search finds for a piece, the one with the lowest score is kept: the number of
zones plus the compactness weight times one minus the zones' mean compactness.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `weight_compactness` | 20 | 0 to 50 | **Compactness weight.** How much the zones' mean compactness (4 pi area / perimeter squared: 0.785 for a square) counts against their number: at 20, zones that are 0.05 more compact on average are worth one zone more. |
| `weight_parts`<br>(advanced) | 1 | 0 to 5 | **Zone count weight.** What each zone costs in the score. |
| `area_weighting`<br>(advanced) | 0 | 0 to 1 | **Area weighting of the compactness.** 0 lets every zone count the same in the mean compactness; 1 weights each zone by its area, as the EG-ICE 2026 paper did, so a small or thin zone hardly counts. |
| `weight_narrow`<br>(advanced) | 0 | 0 to 10 | **Narrow zone cost.** Cost of a zone narrower than the narrow width, in proportion to how much narrower it is: at 5 and 2 m, a 1 m zone costs as much as 2.5 zones more. 0 turns the cost off. |
| `narrow_width_m`<br>(advanced) | 2 m | 0.5 to 4 | **Narrow below.** The width below which a zone costs the narrow zone cost. |

**Cuts.** A cut starts at a reflex corner and runs along one of the corner's two walls carried on, or along the
bisector of its angle, to the first wall it meets. Every cut splits a piece into two.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `bisector_always` | on | switch | **Bisector cuts.** Every reflex corner also offers the cut along the bisector of its angle, next to its two walls carried on. Otherwise the bisector is tried only where neither wall gives a cut. |
| `min_concavity_deg`<br>(advanced) | 2 deg | 0 to 10 | **Reflex from.** A corner that turns inwards by less than this counts as straight and starts no cut. |
| `cut_snap_vertex_tolerance_m`<br>(advanced) | 0.1 m | 0 to 0.5 | **Snap a cut to a corner within.** A cut that passes this close to a corner before reaching a wall ends at the corner, so no hairline sliver is left beside it. |

**Search.** A beam search over the ways to cut a piece: it starts shallow and narrow and searches deeper, then
wider, only while it has found no convex variant. It stops after enough convex variants or dead ends.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `adaptive` | on | switch | **Search deeper only where needed.** Start with the first depth and width; while no convex variant is found, search one cut deeper, then one state wider, up to the deepest search and the widest beam. Off: search at the deepest and widest at once. |
| `start_depth`<br>(needs `adaptive`) | 4 | 1 to 24 | **First depth.** How many cuts in a row the first search makes at most. |
| `start_width`<br>(needs `adaptive`) | 3 | 1 to 16 | **First width.** How many open states the first search keeps. |
| `depth` | 24 | 1 to 40 | **Deepest search.** The most cuts in a row the search makes. |
| `width` | 4 | 1 to 16 | **Widest beam.** The most open states the search keeps. |
| `max_variants` | 32 | 1 to 128 | **Enough convex variants.** The search of a piece stops once it has found this many convex variants. |
| `max_dead_ends` | 500 | 50 to 5000 | **Dead ends before giving up.** The search of a piece gives up after this many dead ends (states it cannot cut further, or at the depth limit, without being convex). It also bounds the search's time. |
| `max_reflex_per_node`<br>(advanced) | 8 | 1 to 24 | **Reflex corners tried per state.** How many reflex corners of a piece offer cuts in one state, the most promising first. |
| `depth_step`<br>(needs `adaptive`, advanced) | 1 | 1 to 8 | **Depth added per retry.** How many cuts deeper each new search goes. |
| `width_step`<br>(needs `adaptive`, advanced) | 1 | 1 to 8 | **Width added per retry.** How many states wider each new search goes once it is at the deepest search. |
| `beam_area_weighting`<br>(advanced) | 1 | 0 to 1 | **Area weighting of the beam.** The area weighting of the score that ranks the open states while the search runs. 1 weights each piece by its area, so a state that has cut one neat block off a big remainder is not dropped. |

**When a piece cannot be cut.** A piece the search cannot make convex is merged with a neighbouring piece and the
union decomposed. When merging gives up, the whole footprint is decomposed instead, both within a time budget. A
building where neither works has no decomposition.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `merge_failed` | on | switch | **Merge a piece that cannot be cut.** A piece the search cannot make convex is merged with a neighbour (sharing the longest edge first) and the union decomposed; the first union that decomposes replaces the two pieces. When none does, the piece is merged with the neighbour it shares most edge with and tried again from there. |
| `merge_max_share`<br>(needs `merge_failed`) | 0.5 | 0.1 to 1 | **Largest merged piece.** Merging gives up when a merged piece would hold more than this share of the building's floor area. |
| `min_shared_edge_m`<br>(needs `merge_failed`, advanced) | 0.05 m | 0 to 1 | **Neighbours share at least.** Two pieces are neighbours when they share at least this much edge. |
| `whole_footprint` | on | switch | **Whole footprint as the last resort.** When a piece cannot be cut (and merging is off or gives up), the whole footprint is decomposed instead of the perimeter's zones; the building keeps its zones when that fails too. |
| `fallback_max_seconds` | 10 s | 0 to 60 | **Time for the fallback.** The merges and the whole footprint of one building may search this long together: a search still running when the time is up stops, and a building not decomposed by then has no decomposition. 0 sets no limit. On the 990 test buildings the slowest fallback that succeeds takes 3 s on a busy machine; a building near the limit could decompose on a faster machine and not on a slower one. |

## Previews for a settings page

`preview(footprint, zones, config)` decomposes one building and never raises (without `zones` it runs the corner-ray
perimeter first, with `perimeter=CornerRayConfig(...)`):

- `layers`: shapely geometries in the footprint's frame, `before` the decomposition (`footprint`,
  `perimeter_zones`, `thin_zones`, `core`, `not_convex` as outlines: the perimeter's zones as its own page draws
  them) and `after` it (`perimeter_parts`, `core_parts`, `merged_parts`, `footprint_parts`: the convex zones by the
  piece they come from; `failed_pieces`; `zone_lines`, the boundaries between pieces; `removed_lines`, the zone
  boundaries a merge or the whole footprint removed; `cut_lines`, the engine's cuts), in drawing order;
  `LAYER_TITLES` names them. The lines are the polygons' boundaries noded together, without their outline, so a cut
  that ends on another part's edge, or that two parts share only up to rounding, is drawn too; a line between parts
  whose edges differ by rounding can be drawn twice, one on the other.
- `numbers`: `NUMBERS` in order, `(key, label, unit)`: the zones from the perimeter and how many were convex
  already, the zones the engine could not cut, merges and the floor area they cover, whether the whole footprint was
  decomposed, the pieces left without a decomposition, whether the fallback's time ran out, the convex zones and
  cuts, the narrowest and smallest zone, the zones narrower than 2 m and under 5 m², their mean compactness, the
  pieces cut with the corner rule relaxed or searched deeper or wider, and the search time with the part spent on
  attempts not kept.
- `fate`: `{"status": "kept", "fallback": None | "merge" | "whole"}`, or `{"status": "dropped", "reason":
  "decomposition_failed", "detail": {"pieces", "kinds", "out_of_time"}}` for a building with a piece left without a
  decomposition (`out_of_time` when the fallback's budget ran out), or the reason `decomposition_error` when the
  engine raised.
- `seconds` and `result` (the `BuildingZones`, or `None` after an error).

`zone_records(result)` gives the convex zones as rows (`zone`, `piece`, `kind`, `sources`, `area_m2`,
`min_width_m`, `compactness`, `geometry`), `piece_records(result)` the pieces (`piece`, `kind`, `sources`,
`decomposed`, `parts`, `fallback_used`, `attempts`, `dead_ends`, `terminated_by`, `seconds`, `geometry`).

## How it was checked

On the 990 footprints of the BEM4AI sample's working set (`data/bem4ai/subset_buildings_gdf.pkl`, written by
`OSM_1_Preprocessing.ipynb`), with the defaults, `decompose_zones` on each footprint's corner-ray zones gives the
same zones as `run_building_decomposition(..., fallback="merge")`, which runs this package:

| | Buildings |
| --- | --- |
| Fully convex | 988 |
| Without a fallback | 935 |
| After merging (66 merges) | 55 |
| After decomposing the whole footprint | 0 |
| Without a decomposition | 2 (osm_0517, osm_0719) |

They have 9,050 convex zones: a median of 7 per building and at most 54 (209 buildings have more than 12, 40 more
than 24); none narrower than 1.2 m, 322 narrower than 2 m, 237 under 5 m² and 12 under 2 m² (convex zones of the
perimeter that small, which no cut made).

**Time.** The perimeter and the zones of one building together, on three processes at a load average of 7 to 14:
a median of 0.03 s, 90 % within 0.23 s and 99 % within 2.7 s; the slowest building that decomposes took 6.6 s
(osm_0798, after one merge; 2.8 s on a quiet machine). The slowest fallback that succeeds took 3.0 s at a load
average of about 10 (osm_0798 again; the next 1.2 s), so the 10 s budget changes no building that decomposes: with
and without it, the 990 buildings get the same zones. It bounds the buildings that fail: without it, osm_0719 took
462 s on a quiet machine (437 s in 42 searches of merged pieces, 26 s for the whole footprint), with it 11 s, its fate
`out_of_time`. osm_0517 fails in under 2 s without running out of time. The whole sample took 43 s on three
processes instead of 573 s.

`tests/test_convex_zones.py` checks the settings table, the zones that need no search, the ranked variants and their
terms, fingerprints, choices (kept, missing, and a zone merged away), the search tree (its variants and their states,
determinism, `max_states`), that the default engine settings are those of `OSM_2_Decomposition.ipynb`, a rectangle's
zones and lines, the merge, the whole footprint, a footprint that cannot be cut, the order in which the pieces are
searched, the time budget (the fallback stops and the engine's search stops at its limit), an engine that raises, and
eight of the perimeter's test footprints (the zones tile them and none is narrower than 1.2 m);
`tests/test_decomposition_fallback.py` the same fallback through `run_building_decomposition`.
