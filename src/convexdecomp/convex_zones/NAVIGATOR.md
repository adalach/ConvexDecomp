# Hand-off: a Zones page in the BEM4AI workbench

For the coordinator of the BEM4AI workbench (branch `workbench`). Agata asked for the convex decomposition to appear
in the navigator as its own page **right after the Perimeter page**, laid out like the other step pages (Geometry
cleaning, Neighbourhood, Windows, Perimeter). This file says what the package gives the page and proposes how it
fits; the decisions marked as hers are hers. Read `README.md` in this folder first, and
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
  building-level step after it. `decompose_pieces(zones, decompose, footprint=..., merge=..., max_share=...,
  min_shared_edge_m=..., whole=...)` takes the engine call as a function: the workbench passes one that looks a
  piece up in its memo or searches it, so the first pass is never searched twice and every union is memoised like a
  piece. `decompose_zones` is the same with ConvexDecomp's own engine call.
- **Zone quality (`S9`)**: notebook 4's hard failure needs a zone narrower than 1.0 m (and elongated or far from
  square), and its awkward zones are those narrower than 1.2 m; the 1.2 m width rule leaves neither, so on the
  corner-ray path `S9` would flag nothing and its whole-building retry would never run. Whether it stays there is
  Agata's call.
- **Where a run stops**: `until` gains the Zones step (`S9`, or wherever the coordinator puts it), and
  `CORNER_STOPS` no longer applies to a run that stops there. Whether a whole run (`all`) goes on from the corner-ray
  zones to the gates and Stage 5, and so whether corner rays become a whole run's default instead of notebook 4's
  offset band, is Agata's call.

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
| `numbers` | `NUMBERS` in order, as `(key, label, unit)`: zones from the perimeter and those convex already, zones the engine could not cut, merges and the floor area merged, whole footprint, pieces left without a decomposition, convex zones, cuts, the narrowest and smallest zone, zones narrower than 2 m and under 5 m², mean compactness, pieces cut with the corner rule relaxed or searched deeper or wider, search time and the time of attempts not kept |
| `fate` | `{"status": "kept", "fallback": None, "merge" or "whole"}`, or `{"status": "dropped", "reason": "decomposition_failed", "detail": {"pieces": ..., "kinds": [...]}}`, or the reason `decomposition_error` (the engine raised; `detail.error`) |
| `seconds`, `result` | the time, and the `BuildingZones` (`None` after an error) |

Pass the zones of the building's `S6` value: without `zones` the preview runs the perimeter itself with
`perimeter=CornerRayConfig(...)` (the package's defaults when not given), which would not hold the edits by hand.
`decompose=` takes the same engine call as `decompose_pieces`, so a preview can use the memo too.

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
its `S6` key, the Zones settings and the snapshot's commit; of a piece: its geometry, the engine settings and the
commit.

**On ConvexDecomp's sample** (the 990 footprints of `data/bem4ai/subset_buildings_gdf.pkl`, local, the sample's
`osm_` ids; the working set the perimeter's hand-off counts on), with the defaults: 988 buildings fully
convex, 923 of them without a fallback and 67 after 76 merges, the whole footprint never needed; osm_0517 and
osm_0719 have no decomposition. This is exactly what `OSM_2_Decomposition.ipynb` gives, zone for zone. 9,048 convex
zones, a median of 7 per building and at most 54: **41 buildings have more than 24 zones** (the workbench's
`max_zones_per_building` would drop them) and 208 more than 12 (where `S9` would retry a failing building whole).
No zone is narrower than 1.2 m; 319 are narrower than 2 m and 232 smaller than 5 m².

**Time.** Perimeter and zones of one building together: a median of 0.03 s, 99 % within 2.5 s at a load average of
about 7, the slowest building that decomposes 9.3 s. **A building that fails is slow**: osm_0719 took 462 s on a quiet
machine, nearly all in 42 searches of merged pieces (up to 20 s each) before the whole footprint (26 s) also failed.
With the workbench's 60 s per building it becomes a timeout instead of `decomposition_failed`: the same building is
lost either way, but a preview needs the same time limit (the engine has no way to stop a search early, so it is
the workbench's timeout signal and hard kill). A time budget for the fallback inside ConvexDecomp is Agata's call.

## Checks for the task

- A preview of a building equals a run with the same settings, zone for zone (as `test_s4_preview.py` does for the
  other groups).
- On synthetic shapes (`tests/test_convex_zones.py` at this commit, outside `src/`): a 20 m × 12 m rectangle gives
  5 zones from the perimeter, all convex, 5 convex zones, no cut; a 10 m × 1 m strip along a 10 m × 5 m block
  (`thin` and `zone`) merges into one piece (fallback `merge`); a 1 m arm beside two blocks whose unions keep the arm
  is decomposed as the whole 10 m × 10 m footprint (fallback `whole`); a 10 m × 1 m footprint decomposed whole has
  no decomposition (`decomposition_failed`). In every kept case the convex zones tile the footprint.
- ConvexDecomp's own tests at the commit: `tests/test_convex_zones.py`, `tests/test_decomposition_fallback.py`
  (the same fallback through `run_building_decomposition`) and the perimeter's.

## Touch points seen in `workbench` at `565220c` (to be confirmed by the coordinator)

`stages/s4_transform/settings.py` (`until`, `UNTIL_TITLES`, `UNTIL_STEPS`, `CORNER_STOPS`, the new fields),
`pages.py` (`PAGES`, `PAGE_VALUES`, `MEMO_GROUP`, `FIXED`), `preview.py` (`GROUPS`, `LAYERS_OF`, `NUMBERS`, `STRATA`,
`compute`), `worker.py` and `nb.py` (`S7`-`S9` on the corner-ray path), `stage.py` (the steps' modules and
`INVALIDATION`), `engines/convexdecomp.py` (load this package; the 2 m in its docstring), `navigator/web/src/modules/
buildings/{common.tsx (STEPS: the Perimeter step's `next` and its note that Zones still takes the offset band),
StepPage.tsx}`, `app/routes.tsx` (`/buildings/zones`, today a placeholder) and `app/nav.ts`.
