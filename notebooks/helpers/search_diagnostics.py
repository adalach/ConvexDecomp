from __future__ import annotations

from typing import Iterable, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

__all__ = [
    "as_diagnostics_dataframe",
    "plot_tree_search_distributions",
]


def as_diagnostics_dataframe(records: Sequence[dict] | pd.DataFrame) -> pd.DataFrame:
    if isinstance(records, pd.DataFrame):
        return records.copy()
    return pd.DataFrame(list(records))


def plot_tree_search_distributions(
    records: Sequence[dict] | pd.DataFrame,
    *,
    width_col: str = "search_max_width",
    depth_col: str = "search_max_depth",
    total_col: str = "search_total_states",
    success_col: str = "success",
    title_prefix: str = "Tree-search size distributions",
) -> tuple[plt.Figure, np.ndarray]:
    df = as_diagnostics_dataframe(records)
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))

    if df.empty:
        for ax, label in zip(axes, ("width", "depth", "total")):
            ax.set_title(label)
            ax.text(0.5, 0.5, "no diagnostics", ha="center", va="center")
            ax.set_axis_off()
        fig.suptitle(title_prefix)
        plt.tight_layout()
        return fig, axes

    success_series = df.get(success_col, pd.Series(True, index=df.index)).fillna(False).astype(bool)

    def _plot_hist(ax, column: str, title: str, *, log_x: bool = False):
        plot_df = df[[column]].copy()
        plot_df["__success__"] = success_series
        plot_df = plot_df[np.isfinite(plot_df[column])]
        if plot_df.empty:
            ax.text(0.5, 0.5, "no data", ha="center", va="center")
            ax.set_title(title)
            ax.set_axis_off()
            return

        values = plot_df[column].to_numpy(dtype=float)
        if log_x:
            positive = values[values > 0]
            if positive.size == 0:
                bins = np.linspace(0, 1, 8)
            else:
                lo = max(float(positive.min()), 1.0)
                hi = float(positive.max())
                if hi <= lo:
                    hi = lo + 1.0
                bins = np.geomspace(lo, hi, 20)
                ax.set_xscale("log")
        else:
            lo = float(values.min())
            hi = float(values.max())
            if hi <= lo:
                hi = lo + 1.0
            bins = np.linspace(lo, hi, 20)

        success_values = plot_df.loc[plot_df["__success__"], column].to_numpy(dtype=float)
        failed_values = plot_df.loc[~plot_df["__success__"], column].to_numpy(dtype=float)
        ax.hist(success_values, bins=bins, alpha=0.65, label="successful", color="#4c78a8")
        ax.hist(failed_values, bins=bins, alpha=0.65, label="failed", color="#e45756")
        ax.set_title(title)
        ax.grid(alpha=0.2)

    _plot_hist(axes[0], width_col, "max branching width")
    _plot_hist(axes[1], depth_col, "max recursion depth")
    _plot_hist(axes[2], total_col, "total states visited", log_x=True)
    axes[0].legend()
    fig.suptitle(title_prefix)
    plt.tight_layout()
    return fig, axes
