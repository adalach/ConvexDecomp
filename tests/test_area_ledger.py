"""Checks for the area ledgers that stop a decomposition from losing a piece unnoticed.

Runs under pytest, or directly with ``python tests/test_area_ledger.py`` so the
repository keeps working without an extra dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.osm.diagnostics import area_ledger, check_area_conservation
from convexdecomp.resplan.decompose import check_plan_area_conservation, plan_area_ledger


def rect(x0, y0, w, h):
    return Polygon([(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)])


FOOTPRINT = rect(0, 0, 10, 10)
CORE = rect(3, 3, 4, 4)
BAND = FOOTPRINT.difference(CORE)
ROW = pd.Series({"sample_id": "b1", "geometry": FOOTPRINT, "perimeter_defined": True,
                 "perimeter_geom": BAND, "interior_geom": CORE})
PARTS = [rect(0, 0, 3, 10), rect(7, 0, 3, 10), rect(3, 0, 4, 3), rect(3, 7, 4, 3), CORE]


def _try(func):
    try:
        func()
    except ValueError:
        return True
    return False


def test_a_complete_decomposition_is_conserved():
    ledger = area_ledger(ROW, [BAND, CORE], PARTS)
    assert ledger["building_area_conserved"]
    assert abs(ledger["parts_area_error_m2"]) < 1e-9
    assert abs(ledger["perimeter_area_error_m2"]) < 1e-9


def test_a_missing_piece_is_caught_and_stops_the_run():
    ledger = area_ledger(ROW, [BAND, CORE], PARTS[:-1])  # the core is missing
    assert not ledger["building_area_conserved"]
    assert round(ledger["parts_area_error_m2"], 6) == -16.0
    frame = pd.DataFrame([{"sample_id": "b1", **ledger}])
    assert len(check_area_conservation(frame, raise_on_violation=False)) == 1
    assert _try(lambda: check_area_conservation(frame))


def test_a_failed_decomposition_is_not_an_area_violation():
    frame = pd.DataFrame([{"sample_id": "b1", **area_ledger(ROW, [BAND, CORE], None)}])
    assert check_area_conservation(frame, raise_on_violation=False).empty


def test_plan_ledger_catches_a_room_that_lost_area():
    plan = {"id": 7, "bedroom": [rect(0, 0, 4, 5)], "kitchen": [rect(4, 0, 3, 5)]}
    good = {"id": 7, "bedroom": [rect(0, 0, 2, 5), rect(2, 0, 2, 5)], "kitchen": [rect(4, 0, 3, 5)]}
    bad = {"id": 7, "bedroom": [rect(0, 0, 2, 5)], "kitchen": [rect(4, 0, 3, 5)]}
    assert not check_plan_area_conservation(plan_area_ledger(plan, good))
    rows = plan_area_ledger(plan, bad)
    assert [r["room_key"] for r in check_plan_area_conservation(rows, raise_on_violation=False)] == ["bedroom"]
    assert _try(lambda: check_plan_area_conservation(rows))


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"ok   {name}")
            except AssertionError as error:
                failures += 1
                print(f"FAIL {name}: {error}")
    sys.exit(1 if failures else 0)
