#!/usr/bin/env python3
"""Export one quick visual-check PNG per ResPlan sample."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from resplan_utils import plot_plan


def export_plans(
    floorplans: list[dict],
    out_dir: Path,
    *,
    size_px: int = 240,
    dpi: int = 100,
    reset_dir: bool = True,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if reset_dir:
        for path in out_dir.glob("*.png"):
            path.unlink()
        manifest = out_dir / "index.csv"
        if manifest.exists():
            manifest.unlink()

    fig_size = size_px / dpi
    manifest_rows: list[dict[str, object]] = []

    for idx, plan in enumerate(floorplans):
        plan_id = plan.get("id", idx)
        file_name = f"{idx:05d}_id_{plan_id}.png"
        out_path = out_dir / file_name

        fig, ax = plt.subplots(figsize=(fig_size, fig_size), dpi=dpi)
        plot_plan(plan, ax=ax)
        ax.set_axis_off()
        fig.tight_layout(pad=0)
        fig.savefig(out_path, bbox_inches="tight", pad_inches=0)
        plt.close(fig)

        manifest_rows.append({"index": idx, "plan_id": plan_id, "file": file_name})
        if idx == 0 or (idx + 1) % 100 == 0:
            print(f"[export] {idx + 1}/{len(floorplans)} -> {file_name}")

    manifest_path = out_dir / "index.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["index", "plan_id", "file"])
        writer.writeheader()
        writer.writerows(manifest_rows)
    return manifest_path
