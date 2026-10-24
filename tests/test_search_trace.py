"""Checks that the search tracer records the search without changing it, and that the explorer serves it.

The tracer (``tools/search_trace.py``) wraps the decomposer's functions and watches
the search's own helpers while one call runs. A traced run must therefore choose
the same variant with the same counts as a plain run, its tree must account for
every state the search processed, and every success it records must cover the
polygon exactly.

Runs under pytest, or directly with ``python tests/test_search_trace.py``.
"""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from shapely.geometry import LineString, Polygon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import search_trace as T  # noqa: E402
from convexdecomp.osm import decompose  # noqa: E402

L_SHAPE = Polygon([(0, 0), (8, 0), (8, 3), (3, 3), (3, 7), (0, 7)])
COURTYARD = Polygon([(0, 0), (12, 0), (12, 10), (0, 10)], [[(4, 3), (8, 3), (8, 7), (4, 7)]])
# A 2.5 m band with two bends that are not right angles.
BENT_BAND = LineString([(0, 0), (10, 0), (14, 6), (14, 14)]).buffer(1.25, cap_style="flat", join_style="mitre")

SHAPES = {"L shape": L_SHAPE, "courtyard": COURTYARD, "bent band": BENT_BAND}


def _successes(trace: T.SearchTrace):
    return [n for n in trace.nodes if T.node_status(n).ring == "success"]


def test_traced_and_untraced_runs_agree():
    for name, poly in SHAPES.items():
        trace = T.trace_search(poly)
        assert not T.compare_with_untraced(trace), name
        assert not trace.notes, (name, trace.notes)
        assert trace.stats["convex_success"], name
        assert trace.best and trace.best[0] in _successes(trace), name


def test_successes_cover_the_polygon():
    for name, poly in SHAPES.items():
        trace = T.trace_search(poly)
        for node in _successes(trace):
            assert all(trace.engine.is_convex(p) for p in node.pieces), (name, node.name)
            assert math.isclose(sum(p.area for p in node.pieces), poly.area, rel_tol=1e-9), (name, node.name)


def test_a_hole_is_opened_like_any_other_cut():
    trace = T.trace_search(COURTYARD)
    openings = [n for n in trace.root.children if not n.is_duplicate]
    assert openings and all(n.cut_kind == "opening" for n in openings)
    for node in openings:
        assert node.cut.startswith("h") and " + " in node.cut  # e.g. "h0 prev + h2 next"
        assert len(node.cut_origins) == 2 and node.cut_chords
        assert T.incoming_chord(node) is not None


def test_the_engine_is_left_as_it_was():
    names = ("run_polygon_state_search", "_state_key", "cut_polygon_at_vertex", "cut_polygon_at_vertex_bisector",
             "cut_polygon_at_vertex_pair", "hole_openings", "holeless_polygons")
    before = {name: getattr(decompose, name) for name in names}
    T.trace_search(COURTYARD)
    assert all(getattr(decompose, name) is fn for name, fn in before.items())


def _explorer_client():
    import pytest

    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")  # TestClient runs on it
    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location("search_explorer_app", ROOT / "tools" / "search_explorer" / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # registered like an imported module
    spec.loader.exec_module(module)
    return module, TestClient(module.app)


def test_explorer_run_returns_a_tree_whose_successes_cover_the_polygon():
    module, client = _explorer_client()
    module.EXAMPLES["test_courtyard"] = {"id": "test_courtyard", "label": "courtyard",
                                         "polygon": module._rings(COURTYARD), "references": []}
    engine = client.get("/api/engine").json()
    assert set(engine["options"]) == {"bisector_always", "min_corner_deg", "min_zone_width_m", "beam_area_weighting"}
    assert all(engine["options"].values())

    reply = client.post("/api/run", json={"example": "test_courtyard", "search": {"bisector_always": True}})
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["stats"]["success"] and not data["stats"]["notes"]
    successes = [n for n in data["nodes"] if n["status"] == "success"]
    assert successes and any(n["best"] for n in successes)
    for node in successes:
        area = sum(data["pieces"][i]["area"] for i in node["pieces"])
        assert math.isclose(area, COURTYARD.area, rel_tol=1e-6), node["number"]
        assert all(data["pieces"][i]["convex"] for i in node["pieces"])


def _explorer_core():
    sys.path.insert(0, str(ROOT / "tools" / "search_explorer"))
    import explorer_core

    return explorer_core


def test_explorer_core_runs_without_a_server_and_caps_the_budget():
    core = _explorer_core()
    core.EXAMPLES["test_l_shape"] = {"id": "test_l_shape", "label": "L shape", "polygon": core.rings(L_SHAPE),
                                     "references": []}
    data = core.run({"example": "test_l_shape", "search": {"width": 30}}, limits={"width": 8})
    assert data["stats"]["success"] and data["request"]["search"]["width"] == 8
    # What the browser worker calls: errors come back as JSON with the server's status code.
    reply = json.loads(core.call_json("run", json.dumps({"example": "test_l_shape", "search": {"width": 0}})))
    assert reply["status"] == 422 and "width" in reply["error"]
    assert json.loads(core.call_json("run", json.dumps({"example": "nowhere"})))["status"] == 404


def test_the_static_build_runs_from_its_own_zip():
    with tempfile.TemporaryDirectory() as tmp:
        out, unpacked = Path(tmp) / "site", Path(tmp) / "explorer"
        subprocess.run([sys.executable, str(ROOT / "tools" / "search_explorer" / "build_static.py"),
                        "--out", str(out), "--examples", "osm_0023"], check=True, capture_output=True)
        assert (out / "index.html").exists() and (out / "static" / "worker.js").exists()
        assert '"backend": "pyodide"' in (out / "static" / "config.js").read_text()
        zipfile.ZipFile(out / "explorer.zip").extractall(unpacked)
        # What the worker does, in a fresh Python that sees the unpacked files and its own packages only.
        code = ("import json, sys\n"
                "sys.path.insert(0, sys.argv[1] + '/tools/search_explorer')\n"
                "import explorer_core as core\n"
                "core.use_examples(sys.argv[1] + '/examples.json')\n"
                "print(core.call_json('run', json.dumps({'example': 'osm_0023'}), '{}'))\n")
        reply = subprocess.run([sys.executable, "-I", "-c", code, str(unpacked)], check=True, capture_output=True, text=True)
        data = json.loads(reply.stdout.strip().splitlines()[-1])
        assert data["example"] == "osm_0023" and data["stats"]["success"]


if __name__ == "__main__":
    for test in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        test()
        print(f"ok  {test.__name__}")
