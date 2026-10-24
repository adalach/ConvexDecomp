"""Build the search explorer as a static site, which runs the search in the browser with Pyodide.

    python tools/search_explorer/build_static.py
    python -m http.server 8766 --directory tmp/search_explorer_site

The site is the explorer's page with `static/config.js` switched to the browser,
and `explorer.zip`: exactly the files of this repository the explorer imports
(found by importing `explorer_core` in a fresh Python) and `examples.json`. The
browser loads Pyodide, numpy and shapely from the jsDelivr CDN, then runs
`explorer_core.py`, the module the server runs. Any static host serves the
folder as it is; `.github/workflows/pages.yml` builds it for GitHub Pages. The
build itself needs only shapely and numpy.

The examples are the demo's, `demo_examples.json`: footprints of the anonymized
OpenStreetMap set this repository ships
(`data/osm/osm_building_footprints_openstreetmap_odbl.gpkg`, ODbL), chosen by
hand (`DEMO_EXAMPLES`) and moved to local coordinates. The local server offers
them too when the BEM4AI examples are missing. `--refresh-demo` writes the file
again from the footprints (needs geopandas); `--examples` builds with other
footprints of the set instead, and `--local-examples` adds the local BEM4AI
examples, which are not in the repository. The build prints how long each
example takes here; in a browser it takes about 1.5 times as long.
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
LIMITS = {"start_width": 8, "width": 8, "depth": 30, "max_reflex": 16, "max_variants": 64, "max_failed": 1000,
          "seconds": 60}
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
    """The footprints `sample_ids` of the bundled OSM set as examples, in local coordinates (needs geopandas)."""
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
        examples.append({"id": sample_id, "label": f"OSM {sample_id} ({poly.area:.0f} m², {what})",
                         "polygon": core.rings(poly), "references": []})
    return examples


def write_demo_examples() -> None:
    """`demo_examples.json` from `DEMO_EXAMPLES`, one example per line."""
    sys.path.insert(0, str(HERE))
    import explorer_core as core  # noqa: E402

    lines = ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in osm_examples(DEMO_EXAMPLES))
    core.DEMO_EXAMPLES_FILE.write_text(
        f'{{\n "attribution": {json.dumps(OSM_ATTRIBUTION, ensure_ascii=False)},\n "examples": [\n{lines}\n ]\n}}\n',
        encoding="utf-8")
    print(f"wrote {core.DEMO_EXAMPLES_FILE.relative_to(REPO_ROOT)}")


def report(examples: Sequence[Dict[str, Any]]) -> None:
    """Each example's label, and how many states its search makes and how long it takes here."""
    sys.path.insert(0, str(HERE))
    import explorer_core as core  # noqa: E402

    for e in examples:
        core.EXAMPLES[e["id"]] = e
        t0 = time.perf_counter()
        run = core.run({"example": e["id"]})
        failed = "" if run["stats"]["success"] else ", no decomposition"
        print(f"  {e['label']}: {len(run['nodes'])} states, {time.perf_counter() - t0:.2f} s here{failed}")


def build(out: Path, examples: Sequence[Dict[str, Any]], attribution: str) -> None:
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

    files = imported_files()
    with zipfile.ZipFile(out / "explorer.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(REPO_ROOT).as_posix())
        z.writestr("examples.json", json.dumps({"examples": list(examples), "attribution": attribution}))
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{out}: {len(files)} Python files and {len(examples)} examples, {size / 1024:.0f} kB; Pyodide {PYODIDE_VERSION}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "tmp" / "search_explorer_site")
    parser.add_argument("--refresh-demo", action="store_true",
                        help=f"write demo_examples.json again from {', '.join(DEMO_EXAMPLES)} (needs geopandas)")
    parser.add_argument("--examples", help="other sample ids of the bundled OSM set, comma-separated (needs geopandas)")
    parser.add_argument("--local-examples", action="store_true",
                        help="add data/bem4ai/explorer_examples.json (local, not in the repository)")
    parser.add_argument("--no-report", action="store_true", help="do not run every example to time it")
    args = parser.parse_args()

    sys.path.insert(0, str(HERE))
    import explorer_core as core  # noqa: E402

    if args.refresh_demo:
        write_demo_examples()
    if args.examples:
        examples, attribution = osm_examples([s for s in args.examples.split(",") if s]), OSM_ATTRIBUTION
    else:
        demo = json.loads(core.DEMO_EXAMPLES_FILE.read_text(encoding="utf-8"))
        examples, attribution = demo["examples"], demo["attribution"]
    if args.local_examples:
        examples = examples + json.loads(core.LOCAL_EXAMPLES_FILE.read_text())["examples"]
    build(args.out.resolve(), examples, attribution)
    if not args.no_report:
        report(examples)


if __name__ == "__main__":
    main()
