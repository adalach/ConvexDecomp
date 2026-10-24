# Hand-off: a Perimeter page in the BEM4AI workbench

For the coordinator of the BEM4AI workbench (branch `workbench`). Agata asked for the corner-ray
perimeter to appear in the navigator as its own page under Geometry, **after Windows and before Zones**, laid out
like the other step pages (Geometry cleaning, Neighbourhood, Windows). This file says what the package gives the
page and proposes how it fits; the decisions marked as hers are hers. Read `README.md` in this folder first.

## What to load

- The package `convexdecomp.corner_perimeter`, in this folder, at the ConvexDecomp commit named in the message that
  brought this file (branch `osm-preprocessing`). It imports only numpy, shapely and `convexdecomp.core.scores`; it
  neither draws nor writes.
- Load it the way `engines/convexdecomp.py` loads the decomposer: from its own read-only snapshot
  (`git archive <commit> src` into `_code/cd-<commit12>/src`, first on `sys.path`, the module checked to lie under
  it). Nothing of it is copied into BEM4AI.
- Keep the decomposer's own pin (`01c9289`) for the zones: the branch at this commit also holds later changes to
  `convexdecomp.osm`, which would change the decomposer's results. The perimeter does not need the decomposer, so
  the two can sit in two snapshots.

## Where it sits in Stage 4

Stage 4's group `S6` today is notebook 4's perimeter (cell `18b7c3957ced30b2`, `nb.perimeter_and_pieces`: an
offset band and trapezoids, the `perimeter_*` settings, layers `perimeter` and `core`). A proposal:

- a switch `perimeter_method` in group `S6`: `notebook` (the default: notebook 4's behaviour, so the equivalence
  suites stay green) or `corner_rays`;
- the corner-ray settings as new `Stage4Settings` fields in group `S6` (prefix them, for example
  `corner_band_m`), their home `default` with the package's defaults until `config/geometry.json` lands;
- input: each building's footprint as Geometry cleaning left it (`geom_local`, local metres; courtyards allowed).
  The neighbourhood and windows do not change it, but the page follows Windows, so it takes the buildings Windows
  kept;
- `until` gains `S6` ("Perimeter"), so "Apply to all" can stop after it like the other pages.

Which perimeter the decomposition takes (Zones, later), and whether `corner_rays` becomes the default, are Agata's
decisions. With corner rays the pieces handed to the decomposition would be the core parts and the perimeter zones
that are not convex.

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
wing-end corners as a black cross.

`upto` (one of `STAGES`, titles in `STAGE_TITLES`; `STEPS` adds each step's rule number and functions) gives the
state after any step, if the page wants to step through them.

**Telling examples.** Proposed strata for `S6`: `lost` (`perimeter_failed`), `vertices` (most corners), `holes`
(courtyards), `thin` (lowest circularity: long narrow wings, where the wing ends act), `small` and `types`; and,
once a run has applied the step, sets from its numbers like Geometry cleaning's: the buildings with the most thin
zones and with the most zones that are not convex.

**Fates and outputs of a run.** The only loss is `perimeter_failed` (none of the 990 footprints of the test
sample). Thin and non-convex zones are kept: what to do with them is the Zones step's. Per building, `counts()` (or
the preview's numbers) for the run's table and `zone_records(result)` as a long sidecar (`sample_id`, `zone`,
`kind` zone/thin/core, `area_m2`, `min_width_m`, `convex`, geometry). The memo key of the group: the cleaned
footprint, the `S6` settings and the snapshot's commit.

**Time.** About 0.01 s per building at the median and at most 0.45 s, 24 s for 990 buildings in one process on a
quiet machine (load average about 30: median 0.03 s, at most 2.7 s).

## Checks for the task

- A preview of a building equals a run with the same settings, zone for zone (as `test_s4_preview.py` does for
  `S2`-`S5`).
- On synthetic footprints (BEM4AI fixtures use synthetic ids only), with the default settings: a 20 m × 12 m
  rectangle gives 4 perimeter zones and 1 core part, all convex; a round building drawn as a regular 24-gon of
  radius 12 m gives 24 perimeter zones and 1 core part, no wing-end corner, and 24 wing-end corners with
  `wing_end_parallel_deg = 180`; a 30 m square with a 10 m square courtyard in its middle gives 8 perimeter zones
  and 1 core part. In every case the zones tile the footprint and keep every corner of its outline.
- The page in both profiles and at 1024, 1280 and 1500 px, in both themes, like the other step pages.
- ConvexDecomp's own tests at the commit: `tests/test_corner_perimeter.py` and `test_corner_perimeter_tidy.py` (19 tests, nine real footprints with
  their expected counts).

## Touch points seen in `workbench` at `c54527a` (to be confirmed by the coordinator)

`stages/s4_transform/settings.py` (`until`, `UNTIL_TITLES`, the new fields), `pages.py` (`PAGES["S6"]`, `MEMO_GROUP`),
`preview.py` (`GROUPS`, `NEEDS`, `LAYERS_OF`, `NUMBERS`, `STRATA`, `compute`), `worker.py` (the `S6` group),
`navigator/web/src/modules/buildings/{common.tsx (STEPS), StepPage.tsx (a PerimeterPage, ROW_PAGES, ROW_SETS)}`,
`app/routes.tsx` (`/buildings/perimeter` between `/buildings/windows` and `/buildings/zones`), `app/nav.ts` (the
menu entry and `BUILDINGS_OF`), and the Zones placeholder's sentence, which names the steps before it.
