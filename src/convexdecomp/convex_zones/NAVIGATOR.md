# Hand-off: a Convex decomposition page in the BEM4AI workbench

For the coordinator of the BEM4AI workbench (branch `workbench`). Agata asked for the convex decomposition to appear
in the navigator as its own page **right after the Perimeter page**, laid out like the other step pages (Geometry
cleaning, Neighbourhood, Windows, Perimeter). This file says what the package gives the page and proposes how it
fits; the decisions marked as hers are hers (those she has made are marked with the date). The page was first
called Zones; Agata named it "Convex decomposition" on 2026-10-26. Read `README.md` in this folder first, and
`../corner_perimeter/NAVIGATOR.md` for how the Perimeter page was set up: this page follows the same pattern.

## What to load

- The package `convexdecomp.convex_zones`, in this folder, at the ConvexDecomp commit named in the message that
  brought this file. It imports only numpy, shapely and ConvexDecomp's engine (`convexdecomp.osm.decompose` with
  `convexdecomp.core` and `convexdecomp.geometry`) and perimeter (`convexdecomp.corner_perimeter`); it neither draws
  nor writes.
- Load it from the read-only snapshot of that commit (`git archive <commit> src`), first on `sys.path`, the module
  checked to lie under it, as `engines/convexdecomp.py` and `engines/corner_perimeter.py` load theirs.
- **One commit for the perimeter and the decomposer.** The perimeter's code at this commit is the code of the
  workbench's perimeter pin `4175b11` (only its README, NAVIGATOR and figures changed since), so moving the
  perimeter pin to this commit changes no zone. The decomposer pin `01c9289` is older: its engine refuses zones
  thinner than 2 m, which refuses the core parts and perimeter zones between 1.2 and 2 m wide that the corner-ray
  perimeter keeps; at this commit the width rule is 1.2 m (`dafdb78`), the narrowest core the perimeter keeps. The
  workbench's help text for `convexdecomp_preset` ("no zone thinner than 2 m") and the docstring of
  `engines/convexdecomp.py` describe the old pin.

## Where it sits in Stage 4

Today, after `S6` (the perimeter), Stage 4 runs `S7` (the pieces: notebook 4's offset band cut into trapezoids and
corner triangles by the pre-check), `S8` (each piece decomposed by the engine, notebook 4's or ConvexDecomp's;
buildings with a piece that would not go convex, or with more than `max_zones_per_building` zones, lost), `S9`
(zone quality, and a whole-building retry for hard failures that are perimeter-only or over-segmented) and `S10` (the
gates). A run with corner rays stops after `S6` (`CORNER_STOPS`), because the zones still take the offset band.

In ConvexDecomp the corner-ray zones go to the engine as they are: `OSM_2_Decomposition.ipynb` has no trapezoid
pre-check and no offset band, and a zone the engine cannot cut is merged with its neighbours, with the whole
footprint as the last resort. This package is that step for one building. A proposal for the corner-ray path:

- **Pieces (`S7`)**: the perimeter's finished zones as `(polygon, kind)` pairs, the zones after the edits by hand
  included (what the `S6` value holds and the Perimeter page draws), kinds `zone`, `thin` and `core`. No pre-check.
- **Decomposition (`S8`)**: each zone searched by the engine with `engine_config(ZoneConfig(**values))`. A piece
  stays the memo's unit: the first pass is one search per zone, like today's pieces.
- **Fallback**: the merge and the whole footprint need the first pass of the whole building, so they are a
  building-level step after it, within a time budget (`fallback_max_seconds`, 10 s by default).
  `decompose_pieces(zones, decompose, footprint=..., merge=..., max_share=..., min_shared_edge_m=..., whole=...,
  max_seconds=...)` takes the engine call as a function `decompose(polygon, max_seconds)`: `max_seconds` is None in
  the first pass and the time left of the budget in the fallback, and `engine_call(cfg)` makes one from an engine
  config (it sets the engine's own `max_seconds`, which stops a search with `search_terminated_by = "time_limit"`).
  The workbench passes one that looks a piece up in its memo or searches it, so the first pass is never searched
  twice. A union's search can be memoised like a piece unless the time limit stopped it (`search_terminated_by ==
  "time_limit"`): that result depends on the time that was left. `decompose_zones` is the same with ConvexDecomp's
  own engine call.
- **Zone quality (`S9`)**: notebook 4's hard failure needs a zone narrower than 1.0 m (and elongated or far from
  square), and its awkward zones are those narrower than 1.2 m; the 1.2 m width rule leaves neither, so on the
  corner-ray path `S9` would flag nothing and its whole-building retry would never run. Agata (2026-10-26): it is
  not on the new path; its counts of zones under 2 m and under 5 m² become checks of the page.
- **Where a run stops**: `until` gains the Convex decomposition step (`S9`, or wherever the coordinator puts it), and
  `CORNER_STOPS` no longer applies to a run that stops there. Whether a whole run (`all`) goes on from the corner-ray
  zones to the gates and Stage 5, and so whether corner rays become a whole run's default instead of notebook 4's
  offset band, was Agata's call. She chose (2026-10-26) corner rays and these zones for whole runs; the offset band
  stays only for notebook 4's equivalence suites.

## The page

Like `StepPage` for `S2`, `S3`, `S5` and `S6`: the settings in sections beside a row of telling examples drawn
before and after at one scale, computed again when the sliders rest, then "Apply to all".

**Settings.** `settings_table()` (or `python -m convexdecomp.convex_zones settings`) gives five sections in order:
Zone rules (narrowest zone, sharpest new corner, smallest zone), Score (compactness weight; zone count weight, area
weighting and the narrow zone cost advanced), Cuts (bisector cuts; reflex threshold and snapping advanced), Search
(adaptive widening, first depth and width, deepest search, widest beam, enough variants, dead ends; reflex corners
per state, steps and the beam's area weighting advanced), and When a piece cannot be cut (merge, largest merged
piece, whole footprint; the shared edge advanced). Each field has its title, help, unit, the values a run accepts
(`ge`, `le`), a slider range that holds the default (`lo`, `hi`, `step`), the switch it `needs` and whether it is
`advanced`. A field's `kind` is `switch`, `number` or, new on this page, `integer` (the search's depths, widths and
counts: the engine needs whole numbers). As for the perimeter, the fields could become `Stage4Settings` fields with
a prefix (for example `zones_min_zone_width_m`), home `default` with the package's defaults; notebook 4's
`decomp_*` fields stay with notebook 4's engine. The paper's search is `engine_config(config,
**PAPER_2026_SEARCH)` (`convexdecomp.osm.decompose`), if the workbench keeps its `convexdecomp_preset`.

**Previews.** `preview(footprint, zones, ZoneConfig(**values))` gives, for one building, without ever raising:

| Key | What |
| --- | --- |
| `layers` | shapely geometries in the footprint's frame (convert to WKB): before `footprint`, `perimeter_zones`, `thin_zones`, `core`, `not_convex` (outlines; the Perimeter page's own layers); after, in drawing order, `perimeter_parts`, `core_parts`, `merged_parts`, `footprint_parts` (the convex zones by the piece they come from), `failed_pieces`, `zone_lines` (boundaries between pieces), `removed_lines` (zone boundaries a merge or the whole footprint removed), `cut_lines` (the engine's cuts) (`LAYERS`, `LAYER_TITLES`) |
| `numbers` | `NUMBERS` in order, as `(key, label, unit)`: zones from the perimeter and those convex already, zones the engine could not cut, merges and the floor area merged, whole footprint, pieces left without a decomposition, whether the fallback ran out of time, convex zones, cuts, the narrowest and smallest zone, zones narrower than 2 m and under 5 m², mean compactness, pieces cut with the corner rule relaxed or searched deeper or wider, search time and the time of attempts not kept |
| `fate` | `{"status": "kept", "fallback": None, "merge" or "whole"}`, or `{"status": "dropped", "reason": "decomposition_failed", "detail": {"pieces": ..., "kinds": [...], "out_of_time": ...}}` (`out_of_time` when the fallback's budget ran out), or the reason `decomposition_error` (the engine raised; `detail.error`) |
| `seconds`, `result` | the time, and the `BuildingZones` (`None` after an error) |

Pass the zones of the building's `S6` value: without `zones` the preview runs the perimeter itself with
`perimeter=CornerRayConfig(...)` (the package's defaults when not given), which would not hold the edits by hand.
`decompose=` takes the same engine call as `decompose_pieces`, so a preview can use the memo too, and the
budget bounds a preview as it bounds a run.

Colours, if the page wants the Perimeter page's look before and a clear one after (a proposal): the convex zones of
perimeter zones `#9ecae1` (edge `#08306b`), of the core `#d9d9d9`, of merged pieces `#fdd0a2` (edge `#d94801`), of
the whole footprint `#c7e9c0` (edge `#238b45`); pieces without a decomposition `#fb6a4a` (edge `#a50f15`); zone
boundaries `#08306b`, removed boundaries dashed `#969696`, cuts `#e7298a`.

**Telling examples.** Proposed strata: `lost` (`decomposition_failed`), `merged` (buildings the merge rescued),
`whole` (the whole footprint decomposed), `cuts` (most cuts: footprints with long non-convex zones), `thin` (most
thin zones from the perimeter), `holes` (courtyards) and `types`.

**Fates and outputs of a run.** The only losses are `decomposition_failed` and `decomposition_error` (notebook 4's
`S8` loss of a building with a piece that would not go convex is the first; the zone count gate stays the
workbench's). Per building, the preview's numbers for the run's table, `zone_records(result)` as a long sidecar of
convex zones (`zone`, `piece`, `kind`, `sources`: the perimeter zones it lies in, `area_m2`, `min_width_m`,
`compactness`, geometry) and `piece_records(result)` for the pieces (`kind`, `sources`, `decomposed`, `parts`, the
engine's `fallback_used`, `attempts`, `dead_ends`, `terminated_by`, `seconds`, geometry). The memo key of a building:
its `S6` key, the page's settings and the snapshot's commit; of a piece: its geometry, the engine settings and the
commit.

**On ConvexDecomp's sample** (the 990 footprints of `data/bem4ai/subset_buildings_gdf.pkl`, local, the sample's
`osm_` ids; the working set the perimeter's hand-off counts on), with the defaults: 988 buildings fully
convex, 931 of them without a fallback and 59 after 69 merges, the whole footprint never needed; osm_0517 and
osm_0719 have no decomposition. `run_building_decomposition` (the OSM notebook's step) gives the same, zone for zone.
9,050 convex zones, a median of 7 per building and at most 54: **41 buildings have more than 24 zones** (the workbench's
`max_zones_per_building` would drop them) and 210 more than 12 (where `S9` would retry a failing building whole).
No zone is narrower than 1.2 m; 320 are narrower than 2 m and 232 smaller than 5 m².

**Time.** Perimeter and zones of one building together, at a load average of 7 to 14: a median of 0.03 s, 99 %
within 2.7 s, the slowest building that decomposes 6.6 s. The fallback's budget (`fallback_max_seconds`, 10 s by
default; Agata asked for it on 2026-10-26) changes no building that decomposes: the slowest fallback that succeeds
takes 3.0 s on a busy machine, and with and without the budget the 990 buildings get the same zones. It bounds the
buildings that fail: osm_0719 took 462 s without it and takes 11 s with it (`decomposition_failed` with
`out_of_time`); osm_0517 fails in under 2 s without running out of time. The engine stops a search at its limit, so
the building's time stays within the budget plus its first pass and perimeter; the workbench's own per-building
timeout stays the safety net. A budget makes a building near it depend on the machine's speed; none of the 990 is
near it.

## The sub-page: variants, choices and the search tree

Agata (2026-10-26): the page applies to the buildings with a zone that is not convex and skips the others; a click on
a decomposed zone switches it to the next variant in the ranking; and a read-only view of a zone's search tree, like
the search explorer's but without its settings, opens as a sub-page, with the variants listed and evaluated beside it.
The package gives all three (README, "Zones that need no search", "Variants and choices", "The search tree").

**Skipping.** `is_final(zone, ZoneConfig(**values))` is the engine's own test for a zone it returns as it is, with no
search: convex and at least `min_zone_width_m` wide. A convex zone thinner than that goes to the engine, has no
decomposition and goes to the fallback; a convex zone under `min_area_m2` (even under 0.5 m²) is final, since the area
rule only stops cuts. `already_convex(zones, config)`: every zone final. For such a building `decompose_zones` gives
exactly its zones (each one convex zone, in the package's order: core parts last), no search, no fallback, so the
workbench can skip it ("already convex") without calling the package. On our 990: 535 buildings skipped, and every
one of them equal to the package's result as a set of zones; 7,169 of 8,028 zones final.

**Variants.** The engine's result for a piece now holds `ranked_variants` (`parts`, `score`, `fallback_used`), best
first by the engine's own ranking: score, then a variant without fallback cuts, then the order found; the first is
`best_variant`. `variant_records(piece.stats, config)` gives the rows the right panel needs: `rank`, `fingerprint`,
`score`, `terms` and `shares` (zones, compactness and, when its weight is on, narrow; they add up to the score),
`zones`, `narrowest_m`, `smallest_m2`, `fallback_used`, `parts`. Memoised engine results from an earlier pin have no
`ranked_variants`. On our 990, 468 of 7,959 pieces have more than one variant, at most 10.

**Choices.** `fingerprint(piece.polygon)` and `fingerprint(variant parts)` (16 hex digits from the normalized geometry
rounded to 1e-6 m; `Piece.fingerprint`, and `fingerprint` in `piece_records` and `variant_records`). `preview`,
`decompose_zones` and `decompose_pieces` take `choices={piece fingerprint: variant fingerprint}` and keep that
variant (`BuildingZones.chosen`: rank kept per piece; `Piece.variant`; `variant` in `piece_records`). A choice
applies to the pieces kept after the fallback: a merged piece and a polygon of the whole footprint have their own
fingerprints and take choices like any piece; a zone merged away takes none, and its choice is listed in
`BuildingZones.choices_missing`, as is a choice whose variant the search no longer finds (other settings). Choices
never change which pieces fail, so they never change the fallback. "Click for the next variant" is the choice of
`rank + 1` of that piece's `variant_records`.

**The tree.** `search_tree(piece.polygon, ZoneConfig(**values), max_states=...)` is the piece's search as data (README,
"The search tree", and the function's docstring for every field): `nodes` with parent, depth, the cut that made it
(`cut`, `cut_kind`), what the search did with it (`status`: `success`, `dead_end`, `parked`, `expanded`, `active`,
`merged`, `duplicate`; `status_lines` in words), `pieces`, `coloured` and `starts` (what a thumbnail shows in colour),
`chord` (the cut's lines), `children` (count in the whole tree), `on_best_path` and `variant` (rank, for a success);
`variants` as `variant_records` with each variant's state (`node`); `pieces` with rings and measures; `stats`;
`truncated`. It is the run's own search (the tracer checks that it is the same run), so the ranks match the run's,
for every piece searched without a time limit: the first pass always; a union searched in the fallback under the
time budget may have stopped early in the run (`search_terminated_by == "time_limit"` in its stats). It needs
Python 3.12 (the workbench's venv has 3.12.10). Sizes on our 990: most searched pieces have tens of states; the
largest, a core part, 1,724 states in 2.9 s and 0.8 MB of JSON, so `max_states=600` (the explorer's own limit for
thumbnails) keeps a page fast; it keeps the first states in generation order and the path to every variant.

**What the explorer's tree view encodes** (`tools/search_explorer/static/app.js`, for the page to mirror):

- Layout: a top-down tidy tree (d3.tree), node radius R = 24 px, node size 2.6 R + 14 by 6.4 R, siblings 1 apart and
  cousins 1.25; edges are vertical links from a node's bottom to its child's top. Children shown in this order:
  those with children first, then those on the chosen variant's path, successes, dead ends, then generation order.
  At most 10 children per state; the rest fold into a "+N more" box that shows them all on click. Duplicates are
  hidden unless switched on. A tree of up to 600 states starts fully open; a larger one starts with only the paths
  to the variants open (or to the deepest state when there is none). A small +/− circle at a node's lower right
  folds or unfolds its children.
- A state is a white disc with its thumbnail clipped to the disc (drawn when at most 600 states are shown): its
  pieces, those in `coloured` filled (convex: a pastel by piece number from #f6c89f, #c5e1a5, #ffd8e1, #d7c4e8,
  #fff1a8, #b9e4dd, #f4b6b6, #ffe0b2, #dcedc8, #e1bee7, #b2ebf2; not convex: #3070b3) with dark edges, the others
  #e4e4e4 with thin #8a8a86 edges; the cut's `chord` in orange #e37222 with round caps; `starts` as small white dots
  with a dark outline.
- The ring is the status: success #2e8540 (3.2 px), dead end #c4071b, parked #0065bd dashed, expanded #555, active
  #888 (1.6 px); merged and duplicate #999 dashed, a duplicate drawn at 0.7 R and labelled "= S<n>".
- Below the node: "S<number>", the cut (a hole opening on two lines, split at " + "), then "<n>p · <score>". Above
  the engine's choice: "chosen" in #2e8540. Edges #9a9a96 1 px; the chosen variant's path #333 3.5 px; edges to
  duplicates dashed.
- The right panel: the selected state drawn large with its zones numbered, "score = n zones + compactness (+
  narrow ...)", and a table per zone (m², compactness, width, convex); below it the variants ranked (#rank, S<number>,
  an "engine's choice" badge), "score · zones · narrowest (red when under the narrow width) · smallest m²", and a
  stacked bar of the terms scaled to the largest score listed: zones #98c6ea, compactness #0065bd, rectangularity
  #a2ad00, narrow #c4071b, small #e37222. The explorer ranks by its own score in the browser; the page should use
  the package's `rank`, which is the engine's.

## Checks for the task

- A preview of a building equals a run with the same settings, zone for zone (as `test_s4_preview.py` does for the
  other groups).
- On synthetic shapes (`tests/test_convex_zones.py` at this commit, outside `src/`): a 20 m × 12 m rectangle gives
  5 zones from the perimeter, all convex, 5 convex zones, no cut; a 10 m × 1 m strip along a 10 m × 5 m block
  (`thin` and `zone`) merges into one piece (fallback `merge`); a 1 m arm beside two blocks whose unions keep the arm
  is decomposed as the whole 10 m × 10 m footprint (fallback `whole`); a 10 m × 1 m footprint decomposed whole has
  no decomposition (`decomposition_failed`). In every kept case the convex zones tile the footprint.
- The skip equals the package: for every building `already_convex` holds for, `decompose_zones` gives its zones.
- A chosen variant shows in the preview and in a run, and a choice of a zone merged away is reported missing.
- The tree's variants are `variant_records` of the same piece, in the same order, each on a success state.
- ConvexDecomp's own tests at the commit: `tests/test_convex_zones.py`, `tests/test_decomposition_fallback.py`
  (the same fallback through `run_building_decomposition`) and the perimeter's.

## Touch points seen in `workbench` at `565220c` (to be confirmed by the coordinator)

`stages/s4_transform/settings.py` (`until`, `UNTIL_TITLES`, `UNTIL_STEPS`, `CORNER_STOPS`, the new fields),
`pages.py` (`PAGES`, `PAGE_VALUES`, `MEMO_GROUP`, `FIXED`), `preview.py` (`GROUPS`, `LAYERS_OF`, `NUMBERS`, `STRATA`,
`compute`), `worker.py` and `nb.py` (`S7`-`S9` on the corner-ray path), `stage.py` (the steps' modules and
`INVALIDATION`), `engines/convexdecomp.py` (load this package; the 2 m in its docstring), `navigator/web/src/modules/
buildings/{common.tsx (STEPS: the Perimeter step's `next` and its note that Zones still takes the offset band),
StepPage.tsx}`, `app/routes.tsx` (`/buildings/zones`, today a placeholder) and `app/nav.ts`.
