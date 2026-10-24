"""Build the search explorer as a static site, which runs the search in the browser with Pyodide.

    python tools/search_explorer/build_static.py
    python -m http.server 8766 --directory tmp/search_explorer_site

The site is the explorer's page with `static/config.js` switched to the browser,
and `explorer.zip`: exactly the files of this repository the explorer imports
(found by importing `explorer_core` in a fresh Python) and `examples.json`. The browser loads Pyodide, numpy and shapely from the jsDelivr
CDN, then runs `explorer_core.py`, the module the server runs. Any static host
serves the folder as it is; nothing else is needed.

The examples are footprints of the anonymized OpenStreetMap set this repository
ships (`data/osm/osm_building_footprints_openstreetmap_odbl.gpkg`, ODbL), chosen
by hand (`DEMO_EXAMPLES`, or `--examples`) and moved to local coordinates;
`--local-examples` adds the local BEM4AI examples, which are not in the
repository. The build prints how long each takes here; in a browser it takes
about 1.5 times as long.
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
from typing import Any, Dict, List, Sequence

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PYODIDE_VERSION = "314.0.7"  # Python 3.14, shapely 2.1.2
# Caps on the search budget in the browser, and the time after which a run is stopped.
LIMITS = {"width": 8, "depth": 30, "max_reflex": 16, "max_variants": 64, "max_failed": 1000, "seconds": 60}
OSM_FOOTPRINTS = REPO_ROOT / "data" / "osm" / "osm_building_footprints_openstreetmap_odbl.gpkg"
OSM_ATTRIBUTION = "Footprints © OpenStreetMap contributors, ODbL 1.0"
PAGE_SCRIPTS = ("app.js", "style.css", "worker.js")  # static/config.js is written by the build
# Simple to complex: 3 to 6 reflex corners, a courtyard, and a courtyard building whose search
# makes some 2,000 states (about 3 s here).
DEMO_EXAMPLES = ("osm_0023", "osm_0038", "osm_0043", "osm_0014", "osm_0109", "osm_0975")


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


def osm_examples(sample_ids: Sequence[str]) -> List[Dict[str, Any]]:
    """The footprints `sample_ids` of the bundled OSM set, in local coordinates, with the time each takes here."""
    if not sample_ids:
        return []
    sys.path.insert(0, str(HERE))
    import explorer_core as core  # noqa: E402  (puts src/ on sys.path)
    import geopandas as gpd
    from shapely import affinity
    from shapely.geometry import Polygon

    footprints = gpd.read_file(OSM_FOOTPRINTS).set_index("sample_id")
    examples = []
    for sample_id in sample_ids:
        if sample_id not in footprints.index:
            sys.exit(f"{sample_id} is not in {OSM_FOOTPRINTS.name}")
        parts = list(getattr(footprints.loc[sample_id].geometry, "geoms", [footprints.loc[sample_id].geometry]))
        if len(parts) != 1:
            sys.exit(f"{sample_id} has {len(parts)} parts; the explorer takes one polygon")
        poly = parts[0]
        minx, miny, maxx, maxy = poly.bounds
        poly = affinity.translate(poly, -(minx + maxx) / 2, -(miny + maxy) / 2)
        poly = Polygon([(round(x, 2), round(y, 2)) for x, y in poly.exterior.coords],
                       [[(round(x, 2), round(y, 2)) for x, y in r.coords] for r in poly.interiors])
        what = f"{core.T.ENGINE.count_reflex(poly)} reflex corners"
        if poly.interiors:
            what += f", {len(poly.interiors)} courtyard" + ("s" if len(poly.interiors) > 1 else "")
        example = {"id": sample_id, "label": f"OSM {sample_id} ({poly.area:.0f} m², {what})",
                   "polygon": core.rings(poly), "references": []}
        core.EXAMPLES[sample_id] = example
        t0 = time.perf_counter()
        run = core.run({"example": sample_id})
        example["seconds_here"] = round(time.perf_counter() - t0, 2)
        example["states_here"] = len(run["nodes"])
        example["decomposed_here"] = run["stats"]["success"]
        examples.append(example)
    return examples


def build(out: Path, sample_ids: Sequence[str], local_examples: bool) -> None:
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

    examples = osm_examples(sample_ids)
    attribution = OSM_ATTRIBUTION if examples else ""
    if local_examples:
        examples += json.loads(core.LOCAL_EXAMPLES_FILE.read_text())["examples"]
        attribution = OSM_ATTRIBUTION
    files = imported_files() + [core.BUILT_IN_EXAMPLE]  # read when no examples file is found
    with zipfile.ZipFile(out / "explorer.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(REPO_ROOT).as_posix())
        shipped = [{k: v for k, v in e.items() if not k.endswith("_here")} for e in examples]
        z.writestr("examples.json", json.dumps({"examples": shipped, "attribution": attribution}))

    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{out}: {len(files)} Python files and {len(examples)} examples, {size / 1024:.0f} kB; Pyodide {PYODIDE_VERSION}")
    for e in examples:
        here = f": {e['states_here']} states, {e['seconds_here']} s here" if "seconds_here" in e else ""
        failed = "" if e.get("decomposed_here", True) else ", no decomposition"
        print(f"  {e['label']}{here}{failed}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "tmp" / "search_explorer_site")
    parser.add_argument("--examples", default=",".join(DEMO_EXAMPLES),
                        help="sample ids of the bundled OSM set, comma-separated (default: %(default)s)")
    parser.add_argument("--local-examples", action="store_true",
                        help="add data/bem4ai/explorer_examples.json (local, not in the repository)")
    args = parser.parse_args()
    build(args.out.resolve(), [s for s in args.examples.split(",") if s], args.local_examples)


if __name__ == "__main__":
    main()
