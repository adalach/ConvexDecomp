#!/usr/bin/env python3
"""The corner-ray perimeter on every footprint of a sample, with the chosen settings or others::

    python tools/corner_perimeter/run_sample.py [--set wing_end_max_m=0] [--limit N] [--sheets] [--out DIR]

Writes ``run.csv`` (one row per footprint: counts, timing, how often each rule acted, or the error),
``zones.gpkg`` (layer ``zones``: sample_id, kind, area_m2, min_width_m, convex) and ``run.log`` with the totals into
``--out`` (default ``temp/corner_perimeter/run``); with ``--sheets`` also ``sheet_worst.png`` (the footprints with
most thin and non-convex zones and specks) and ``sheet_random.png``. A footprint gets at most 60 s.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drawing import ROOT, SAMPLE, ZONE_KEYS, draw_zones, legend, sample, whole  # noqa: E402

import geopandas as gpd  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.plotting import plot_polygon  # noqa: E402

from convexdecomp.core.scores import minimum_width  # noqa: E402
from convexdecomp.corner_perimeter import CornerRayConfig, corner_ray_perimeter  # noqa: E402
from convexdecomp.corner_perimeter.geometry import is_convex  # noqa: E402

TIME_LIMIT_S = 60


class TimeOut(Exception):
    pass


def _alarm(signum, frame):
    raise TimeOut()


@contextmanager
def time_limit(seconds: int):
    """Raise TimeOut if the block takes longer than seconds."""
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)


def overrides(items: list[str]) -> dict:
    """``name=value`` pairs as settings of :class:`CornerRayConfig` (numbers, or true/false for switches)."""
    out = {}
    default = CornerRayConfig()
    for item in items:
        name, _, value = item.partition("=")
        if not hasattr(default, name):
            raise SystemExit(f"no setting {name!r}")
        out[name] = value.lower() in ("1", "true", "yes", "on") if isinstance(getattr(default, name), bool) \
            else float(value)
    return out


def run_all(g: pd.DataFrame, cfg: CornerRayConfig, log=print):
    """One row per footprint and the zones of all of them."""
    rows, zone_rows = [], []
    t_all = time.time()
    for n, (sid, poly) in enumerate(g.geometry.items(), 1):
        row = dict(sample_id=sid, area_m2=round(poly.area, 1), holes=len(poly.interiors))
        t = time.time()
        try:
            with time_limit(TIME_LIMIT_S):
                r = corner_ray_perimeter(poly, cfg)
            row["error"] = ""
        except TimeOut:
            r, row["error"] = None, f"over {TIME_LIMIT_S} s"
        except Exception as exc:  # noqa: BLE001  (recorded per footprint)
            r = None
            row["error"] = f"{type(exc).__name__}: {exc}"[:160]
            row["where"] = traceback.extract_tb(exc.__traceback__)[-1].name
        row["seconds"] = round(time.time() - t, 2)
        if r is not None:
            pz = r.perimeter_zones
            row.update(**r.counts(), core_share=round(sum(z.area for z in r.core_parts) / poly.area, 3),
                       thin_area_m2=round(sum(z.area for z, k in r.zones if k == "thin"), 1),
                       min_width_m=round(min(minimum_width(z) for z in pz), 2) if pz else np.nan)
            zone_rows += [dict(sample_id=sid, kind=k, area_m2=round(z.area, 2), min_width_m=round(minimum_width(z), 2),
                               convex=is_convex(z), geometry=z) for z, k in r.zones]
        rows.append(row)
        if n % 100 == 0:
            log(f"{n} footprints, {time.time() - t_all:.0f} s")
    return pd.DataFrame(rows), zone_rows, time.time() - t_all


def summary(df: pd.DataFrame, seconds: float) -> str:
    ok = df[df.error == ""]
    lines = [f"{len(df)} footprints in {seconds:.0f} s; {len(ok)} done, {len(df) - len(ok)} failed"]
    lines += [f"  {sid}: {err}" for sid, err in zip(df.sample_id, df.error) if err]
    for col in ("zones", "thin", "not_convex", "sharp_20", "specks"):
        lines.append(f"{col:11} total {int(ok[col].sum()):5d}   footprints with any: {int((ok[col] > 0).sum()):4d}")
    clean = ((ok.thin == 0) & (ok.not_convex == 0) & (ok.sharp_20 == 0) & (ok.specks == 0)).sum()
    lines.append(f"clean footprints {clean}, without a core {int((ok.core_parts == 0).sum())}")
    lines.append(f"seconds: median {ok.seconds.median()} max {ok.seconds.max()}")
    return "\n".join(lines)


def sheet(ids, g, df, zones, path, cols=4):
    """A contact sheet of the footprints' zones."""
    rows = (len(ids) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5.0 * cols, 5.0 * rows), squeeze=False)
    for ax in axes.flat:
        ax.axis("off")
    for ax, sid in zip(axes.flat, ids):
        p = g.loc[sid, "geometry"]
        row = df.set_index("sample_id").loc[sid]
        if row.error:
            plot_polygon(p, ax=ax, add_points=False, facecolor="#f0f0f0", edgecolor="k")
            ax.set_aspect("equal")
            ax.set_title(f"{sid}\nfailed: {row.error[:60]}", fontsize=8)
            continue
        z = zones[zones.sample_id == sid]
        draw_zones(ax, SimpleNamespace(poly=p, zones=list(zip(z.geometry, z.kind)), sharp_cuts=[]), whole(p))
        ax.set_title(f"{sid}\n{int(row.zones)} zones, {int(row.thin)} thin, {int(row.not_convex)} not convex, "
                     f"{int(row.specks)} specks", fontsize=9)
    legend(fig, ZONE_KEYS, ncol=5)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=55)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", type=Path, default=SAMPLE, help="the footprint sample (pickle or GeoPackage)")
    ap.add_argument("--out", type=Path, default=ROOT / "temp" / "corner_perimeter" / "run")
    ap.add_argument("--set", nargs="*", default=[], metavar="NAME=VALUE", help="settings other than the defaults")
    ap.add_argument("--limit", type=int, default=0, help="only the first N footprints")
    ap.add_argument("--sheets", action="store_true", help="also draw contact sheets")
    a = ap.parse_args()
    cfg = CornerRayConfig(**overrides(a.set))
    g = sample(a.sample)
    if a.limit:
        g = g.head(a.limit)
    a.out.mkdir(parents=True, exist_ok=True)
    df, zone_rows, seconds = run_all(g, cfg)
    df.to_csv(a.out / "run.csv", index=False)
    if zone_rows:
        gpd.GeoDataFrame(zone_rows, crs=None).to_file(a.out / "zones.gpkg", layer="zones")
    text = f"settings changed: {cfg.changed() or 'none'}\n" + summary(df, seconds)
    (a.out / "run.log").write_text(text + "\n")
    print(text)
    if a.sheets and zone_rows:
        zones = gpd.GeoDataFrame(zone_rows, crs=None)
        ok = df[df.error == ""].copy()
        ok["badness"] = ok.thin + ok.not_convex + 2 * ok.specks
        worst = ok.sort_values(["badness", "thin"], ascending=False).head(16).sample_id.tolist()
        sheet(worst, g, df, zones, a.out / "sheet_worst.png")
        sheet(ok.sample(min(16, len(ok)), random_state=0).sample_id.tolist(), g, df, zones,
              a.out / "sheet_random.png")


if __name__ == "__main__":
    main()
