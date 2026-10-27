# Hand-off: a Perimeter page in the BEM4AI workbench

For the coordinator of the BEM4AI workbench (branch `workbench`). Agata asked for the corner-ray
perimeter to appear in the navigator as its own page under Geometry, **after Windows and before Zones**, laid out
like the other step pages (Geometry cleaning, Neighbourhood, Windows). This file says what the package gives the
page and proposes how it fits; the decisions marked as hers are hers. Read `README.md` in this folder first.

## What to load

- The package `convexdecomp.corner_perimeter`, in this folder, at the ConvexDecomp commit named in the message that
  brought this file (branch `osm-preprocessing`; the public `main` holds the same package since `dafdb78`). It imports
  only numpy, shapely and `convexdecomp.core.scores`; it neither draws nor writes.
- Load it the way `engines/convexdecomp.py` loads the decomposer: from its own read-only snapshot
  (`git archive <commit> src` into `_code/cd-<commit12>/src`, first on `sys.path`, the module checked to lie under
  it). Nothing of it is copied into BEM4AI.
- The decomposer: ConvexDecomp's own OSM path now decomposes exactly this perimeter's output
  (`convexdecomp.osm.perimeter_zones`, below), with the engine's `min_zone_width_m` at 1.2 m since `dafdb78`, the
  narrowest core the perimeter keeps. The decomposer pin `01c9289` has a hard 2 m width and refuses the core parts
  and zones between 1.2 and 2 m wide, so a Zones step that takes the corner-ray zones should pin the decomposer at
  this commit too. The decomposer is still being worked on in ConvexDecomp: ask before moving its pin again.

## Where it sits in Stage 4

Stage 4's group `S6` today is notebook 4's perimeter (cell `18b7c3957ced30b2`, `nb.perimeter_and_pieces`: an
offset band and trapezoids, the `perimeter_*` settings, layers `perimeter` and `core`). In ConvexDecomp the corner
rays have replaced that offset band: `OSM_2_Decomposition.ipynb` runs the corner-ray perimeter instead of the offset
perimeter and the trapezoid pre-check (`77be666`; `osm/perimeter.py` is still in the repository but no longer
called there). Nothing in the corner-ray package depends on the offset band. A proposal:

- the corner rays as the method of `S6`. If notebook 4's offset band should stay selectable (its equivalence suite),
  a switch `perimeter_method` (`offset_band` or `corner_rays`); whether the switch stays, and its default, is
  Agata's call;
- the corner-ray settings as new `Stage4Settings` fields in group `S6` (prefix them, for example
  `corner_band_m`), their home `default` with the package's defaults until `config/geometry.json` lands;
- input: each building's footprint as Geometry cleaning left it (`geom_local`, local metres; courtyards allowed).
  The neighbourhood and windows do not change it, but the page follows Windows, so it takes the buildings Windows
  kept;
- `until` gains `S6` ("Perimeter"), so "Apply to all" can stop after it like the other pages.

What ConvexDecomp's decomposition takes from it (`convexdecomp.osm.perimeter_zones.corner_perimeter_record`, which
needs pandas and geopandas besides the package): the perimeter zones in `perimeter_parts` with their roles
(`perimeter_zone` or `thin_zone`) and the core parts in `interior_geom`; every zone and core part then goes to the
decomposition as it is (a convex one comes back as one part, the others are cut); a footprint the procedure cannot
zone is decomposed whole. On ConvexDecomp's current working set (see "Samples" below) with the engine at 1.2 m:
the perimeter gives 7,339 zones (108 thinner than 2 m, 371 not convex) and 689 core parts. The decomposition
alone leaves 69 buildings with a zone the search cannot cut; with OSM_2's fallback
(`run_building_decomposition(..., fallback="merge")`: such a zone is merged with a neighbouring zone and the union
decomposed, else the whole footprint) 988 of 990 buildings are fully convex, 59 of them after merging (69 merges), no
area lost; osm_0719 and osm_0517 have no decomposition. Whether the workbench's
Zones takes the same is Agata's decision.

## The page

Like `StepPage` for `S2`, `S3` and `S5`: the settings in sections beside a row of telling examples drawn before and
after at one scale, computed again when the sliders rest, then "Apply to all".

**Settings.** `settings_table()` (or `python -m convexdecomp.corner_perimeter settings`) gives nine sections in
order (Band and rays; Wing ends; Short walls; Bays; Rays that meet; Narrow core; Close points and narrow zones; Thin
zones, sharp corners and specks; Tidy zone corners) with each field's title, help, unit, the values a run accepts
(`ge`, `le`), a slider range that holds the
default (`lo`, `hi`, `step`), the switch it `needs` and whether it is `advanced`: what `pages.py`'s `Section` and
`Slider` take. Switches are booleans; every other field is a number. The README lists them all.

**Previews.** `preview(footprint, CornerRayConfig(**values))` gives, for one building, without ever raising:

| Key | What |
| --- | --- |
| `layers` | shapely geometries in the footprint's frame (convert to WKB): before `footprint`; after, in drawing order, `perimeter_zones`, `thin_zones`, `core`, `not_convex` (outlines), `band_line`, `rays`, `merged_rays`, `corner_lines`, `wing_ends`, `bay_corners` (`LAYERS`, `LAYER_TITLES`) |
| `numbers` | `NUMBERS` in order, as `(key, label, unit)`: corners, wing-end corners, perimeter zones, core parts, core share, thin and non-convex zones, corners under 20°, specks, the narrowest zone, then how often each rule acted |
| `fate` | `{"status": "kept"}`, or `{"status": "dropped", "reason": "perimeter_failed", "detail": {"error": ...}}` |
| `seconds`, `result` | the time, and the `PerimeterResult` (or `None` when dropped) |

The colours the note uses, if the page wants the same look: perimeter zone `#9ecae1` (edge `#08306b`), thin zone
`#fcbba1` (edge `#cb181d`), core `#d9d9d9` hatched, a zone not convex dotted, band line `#2171b5`, rays from convex
corners `#fd8d3c` and from reflex corners `#d94801`, merged rays `#1a9850`, corner-to-corner lines `#6a3d9a`,
wing-end corners as a black cross; bay corners (their rays left out so the bay becomes one zone) need a mark of
their own.

`upto` (one of `STAGES`, titles in `STAGE_TITLES`; `STEPS` adds each step's rule number and functions) gives the
state after any step, if the page wants to step through them.

**Telling examples.** Proposed strata for `S6`: `lost` (`perimeter_failed`), `vertices` (most corners), `holes`
(courtyards), `thin` (lowest circularity: long narrow wings, where the wing ends act), `small` and `types`; and,
once a run has applied the step, sets from its numbers like Geometry cleaning's: the buildings with the most thin
zones and with the most zones that are not convex.

**Fates and outputs of a run.** The only loss is `perimeter_failed` (none of the 990 footprints, in either sample
below). Thin and non-convex zones are kept: what to do with them is the Zones step's. Per building, `counts()` (or
the preview's numbers) for the run's table and `zone_records(result)` as a long sidecar (`sample_id`, `zone`,
`kind` zone/thin/core, `area_m2`, `min_width_m`, `convex`, geometry). The memo key of the group: the cleaned
footprint, the `S6` settings and the snapshot's commit.

**Time.** At `dafdb78`, in one process at a load average of about 12: the tuning snapshot's 990 buildings in 68 s
(median 0.02 s per building, at most 4.4 s), the working set's in 83 s (median 0.02 s, at most 6.3 s). The wing-end
and bay checks run the procedure again for some buildings. None failed.

**Samples.** Two local files of 990 BEM4AI footprints each, with the same sample ids, neither in the repository: the
tuning snapshot `temp/perimeter/subset_snapshot.pkl` (2026-10-14), on which the method was developed and every figure
and rule count of the README was measured, and the current working set `data/bem4ai/subset_buildings_gdf.pkl`, which
the decomposition notebook runs on; 161 footprints differ between the two.

## Checks for the task

- A preview of a building equals a run with the same settings, zone for zone (as `test_s4_preview.py` does for
  `S2`-`S5`).
- On synthetic footprints (BEM4AI fixtures use synthetic ids only), with the default settings: a 20 m × 12 m
  rectangle gives 4 perimeter zones and 1 core part, all convex; a round building drawn as a regular 24-gon of
  radius 12 m gives 24 perimeter zones and 1 core part, no wing-end corner, and 24 wing-end corners with
  `wing_end_parallel_deg = 180` (then 1 perimeter zone, not convex, and the core); a 30 m square with a 10 m
  square courtyard in its middle gives 8 perimeter zones and 1 core part. In every case the zones tile the footprint
  and keep every corner of its outline.
- Totals with the defaults, for comparison at scale: on the tuning snapshot 7,250 perimeter zones, 93 thinner than
  2 m, 358 not convex, no corner under 20°, no speck under 0.5 m², 2 zone arms narrower than 0.3 m, 722 footprints
  with none of these, 363 without a core; on the working set 7,339 perimeter zones, 108 thinner than 2 m, 371 not
  convex, no corner under 20°, no speck, 3 narrow arms, 715 footprints with none of these, 364 without a core, 689
  core parts. Rule 1 ends 36 merged rays on the band line in each.
- The page in both profiles and at 1024, 1280 and 1500 px, in both themes, like the other step pages.
- ConvexDecomp's own tests at the commit (outside `src/`, so not in the snapshot): `tests/test_corner_perimeter.py`
  (15 tests, nine real footprints with their expected counts, with the defaults and with the short walls, bays and
  tidy step off), `tests/test_corner_perimeter_tidy.py` (4) and `tests/test_perimeter_zones.py` (3, the hand-over to
  the decomposition); all 22 pass at `dafdb78`.

## Touch points seen in `workbench` at `c54527a` (to be confirmed by the coordinator)

`stages/s4_transform/settings.py` (`until`, `UNTIL_TITLES`, the new fields), `pages.py` (`PAGES["S6"]`, `MEMO_GROUP`),
`preview.py` (`GROUPS`, `NEEDS`, `LAYERS_OF`, `NUMBERS`, `STRATA`, `compute`), `worker.py` (the `S6` group),
`navigator/web/src/modules/buildings/{common.tsx (STEPS), StepPage.tsx (a PerimeterPage, ROW_PAGES, ROW_SETS)}`,
`app/routes.tsx` (`/buildings/perimeter` between `/buildings/windows` and `/buildings/zones`), `app/nav.ts` (the
menu entry and `BUILDINGS_OF`), and the Zones placeholder's sentence, which names the steps before it.
