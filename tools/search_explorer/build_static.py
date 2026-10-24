"""Build the search explorer as a static site, which runs the search in the browser with Pyodide.

    python tools/search_explorer/build_static.py
    python -m http.server 8766 --directory tmp/search_explorer_site

The site is the explorer's page with `static/config.js` switched to the browser,
and `explorer.zip`: exactly the files of this repository the explorer imports
(found by importing `explorer_core` in a fresh Python), the paper polygon and
`examples.json`. The browser loads Pyodide, numpy and shapely from the jsDelivr
CDN, then runs `explorer_core.py`, the module the server runs. Any static host
serves the folder as it is; nothing else is needed.

The examples are a few footprints of the anonymized OpenStreetMap set this
repository ships
(`data/osm/osm_building_footprints_openstreetmap_odbl.gpkg`, ODbL), moved to
local coordinates, and the EG-ICE 2026 figure polygon; `--local-examples` adds the local BEM4AI examples, which are
not in the repository. Each example must decompose with the default settings
within `--max-seconds` here, so that it runs in a few seconds in a browser.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PYODIDE_VERSION = "314.0.7"  # Python 3.14, shapely 2.1.2
# Caps on the search budget in the browser, and the time after which a run is stopped.
LIMITS = {"width": 8, "depth": 30, "max_reflex": 16, "max_variants": 64, "max_failed": 1000, "seconds": 60}
OSM_FOOTPRINTS = REPO_ROOT / "data" / "osm" / "osm_building_footprints_openstreetmap_odbl.gpkg"
OSM_ATTRIBUTION = "Footprints © OpenStreetMap contributors, ODbL 1.0"
PAGE_SCRIPTS = ("app.js", "style.css", "worker.js")  # static/config.js is written by the build


def imported_files() -> List[Path]:
    """The files of this repository that `import explorer_core` loads, in a fresh interpreter."""
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "import explorer_core\n"
        "print(json.dumps(sorted({m.__file__ for m in list(sys.modules.values()) if getattr(m, '__file__', None)})))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    files = [Path(f).resolve() for f in json.loads(out.stdout.strip().splitlines()[-1])]
    return [f for f in files if f.is_relative_to(REPO_ROOT)]


def osm_examples(n: int, max_seconds: float) -> List[Dict[str, Any]]:
    """`n` footprints of the bundled OSM set, one per number of reflex corners from 2 up, plus one courtyard."""
    if n <= 0:
        return []
    sys.path.insert(0, str(HERE))
    import explorer_core as core  # noqa: E402  (puts src/ on sys.path)
    import geopandas as gpd
    from shapely import affinity
    from shapely.geometry import Polygon

    from convexdecomp.osm.decompose import ConvexDecompositionConfig, decompose_polygon_with_stats

    count_reflex = core.T.ENGINE.count_reflex
    footprints = gpd.read_file(OSM_FOOTPRINTS).sort_values("sample_id")
    wanted_reflex = list(range(2, 2 + max(n - 1, 0)))
    picked: Dict[Any, Dict[str, Any]] = {}
    for sample_id, geom in zip(footprints["sample_id"], footprints.geometry):
        if len(picked) >= n:
            break
        parts = list(getattr(geom, "geoms", [geom]))
        if len(parts) != 1 or not isinstance(parts[0], Polygon) or not parts[0].is_valid:
            continue
        poly = parts[0]
        minx, miny, maxx, maxy = poly.bounds
        poly = affinity.translate(poly, -(minx + maxx) / 2, -(miny + maxy) / 2)
        poly = Polygon([(round(x, 2), round(y, 2)) for x, y in poly.exterior.coords],
                       [[(round(x, 2), round(y, 2)) for x, y in r.coords] for r in poly.interiors])
        corners = len(poly.exterior.coords) - 1 + sum(len(r.coords) - 1 for r in poly.interiors)
        if not poly.is_valid or not 80 <= poly.area <= 2000 or corners > 24:
            continue
        key: Any = "courtyard" if poly.interiors else count_reflex(poly)
        if key in picked or (key != "courtyard" and key not in wanted_reflex):
            continue
        t0 = time.perf_counter()
        result = decompose_polygon_with_stats(poly, ConvexDecompositionConfig())
        if not result["convex_success"] or time.perf_counter() - t0 > max_seconds:
            continue
        what = "1 courtyard" if key == "courtyard" else f"{key} reflex corners"
        picked[key] = {"id": sample_id, "label": f"OSM {sample_id} ({poly.area:.0f} m², {what})",
                       "polygon": core.rings(poly), "references": []}
    return sorted(picked.values(), key=lambda e: e["id"])


def build(out: Path, n_osm: int, local_examples: bool, max_seconds: float) -> None:
    sys.path.insert(0, str(HERE))
    import explorer_core as core  # noqa: E402

    if out.exists():
        if any(out.iterdir()) and not (out / "explorer.zip").exists():
            sys.exit(f"{out} exists and is not a build of this script; choose another --out")
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    shutil.copy2(HERE / "static" / "index.html", out / "index.html")
    for name in PAGE_SCRIPTS:
        shutil.copy2(HERE / "static" / name, out / "static" / name)
    pyodide = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
    config = {"backend": "pyodide", "pyodide": pyodide, "bundle": "explorer.zip", "limits": LIMITS}
    (out / "static" / "config.js").write_text(
        "/* Written by build_static.py: the searches run in the browser with Pyodide. */\n"
        f"window.EXPLORER_CONFIG = {json.dumps(config, indent=2)};\n")

    # The paper polygon last: with the default rules (zones 1.2 m wide, corners of 30°) it has no
    # decomposition, and its search runs into the dead-end limit, so it is no example to start with.
    examples = osm_examples(n_osm, max_seconds) + [core.paper_example()]
    attribution = OSM_ATTRIBUTION if n_osm else ""
    if local_examples:
        examples += json.loads(core.LOCAL_EXAMPLES_FILE.read_text())["examples"]
        attribution = OSM_ATTRIBUTION
    files = imported_files() + [core.BUILT_IN_EXAMPLE]
    with zipfile.ZipFile(out / "explorer.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(REPO_ROOT).as_posix())
        z.writestr("examples.json", json.dumps({"examples": examples, "attribution": attribution}))

    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{out}: {len(files)} Python files and {len(examples)} examples, {size / 1024:.0f} kB; Pyodide {PYODIDE_VERSION}")
    for e in examples:
        print(f"  {e['label']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "tmp" / "search_explorer_site")
    parser.add_argument("--osm-examples", type=int, default=6, help="footprints from the bundled OSM set (0: none)")
    parser.add_argument("--local-examples", action="store_true",
                        help="add data/bem4ai/explorer_examples.json (local, not in the repository)")
    parser.add_argument("--max-seconds", type=float, default=2.0,
                        help="an OSM footprint is offered only if it decomposes this fast here")
    args = parser.parse_args()
    build(args.out.resolve(), args.osm_examples, args.local_examples, args.max_seconds)


if __name__ == "__main__":
    main()
