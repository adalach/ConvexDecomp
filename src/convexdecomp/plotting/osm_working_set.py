from __future__ import annotations

from typing import Any

__all__ = ["plot_working_set_prepost_comparison"]


def _clean_integer_values(values, *, cap_value: int):
    import numpy as np

    cleaned = np.asarray(values, dtype=float)
    cleaned = cleaned[np.isfinite(cleaned)]
    if cleaned.size == 0:
        raise ValueError("Comparison plot requires at least one finite value.")
    return np.minimum(np.rint(cleaned).astype(int), int(cap_value))


def _tick_step(low: int, high: int) -> int:
    n_values = int(high - low + 1)
    if n_values <= 18:
        return 1
    if n_values <= 36:
        return 2
    if n_values <= 70:
        return 5
    return 10


def _plot_split_distribution(
    ax_top: Any,
    ax_bottom: Any,
    original_values,
    final_values,
    *,
    support,
    xlim: tuple[float, float],
    ymax: float,
    ticks,
    title: str,
    xlabel: str,
    original_color: str,
    final_color: str,
) -> None:
    import numpy as np
    import pandas as pd

    support = np.asarray(support, dtype=int)
    original_counts = pd.Series(original_values).value_counts().sort_index().reindex(support, fill_value=0)
    final_counts = pd.Series(final_values).value_counts().sort_index().reindex(support, fill_value=0)

    for ax, counts, color, label in [
        (ax_top, original_counts, original_color, "Original"),
        (ax_bottom, final_counts, final_color, "Final"),
    ]:
        ax.bar(support, counts.values, width=1.0, color=color, alpha=0.9, edgecolor="black", linewidth=0.4)
        ax.set_xlim(*xlim)
        ax.set_ylim(0, ymax)
        ax.set_xticks(ticks)
        ax.set_ylabel("Count")
        ax.grid(axis="y", alpha=0.22)
        ax.text(
            0.01,
            0.92,
            label,
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=10,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75},
        )

    ax_top.set_title(title, fontsize=11)
    ax_top.tick_params(labelbottom=False)
    ax_bottom.set_xlabel(xlabel)


def _plot_transition_heatmap(
    ax: Any,
    original_values,
    final_values,
    *,
    low_value: int,
    cap_value: int,
    xlim: tuple[float, float],
    ylim: tuple[float, float],
    ticks,
    title: str,
    xlabel: str,
    ylabel: str,
    cmap: str,
    vmax: int,
):
    import numpy as np
    from matplotlib.colors import LogNorm

    heat = np.zeros((cap_value - low_value + 1, cap_value - low_value + 1), dtype=int)
    for x_val, y_val in zip(original_values, final_values):
        heat[y_val - low_value, x_val - low_value] += 1

    masked = np.ma.masked_where(heat == 0, heat)
    ax.plot(
        [low_value, cap_value],
        [low_value, cap_value],
        color="0.55",
        linewidth=1.2,
        alpha=1.0,
        linestyle="--",
        zorder=0,
    )
    image = ax.imshow(
        masked,
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        extent=(low_value - 0.5, cap_value + 0.5, low_value - 0.5, cap_value + 0.5),
        cmap=cmap,
        norm=LogNorm(vmin=1, vmax=max(1, int(vmax))),
        zorder=1,
    )
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_xticks(ticks)
    ax.set_yticks(ticks)
    ax.set_title(title, fontsize=11)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    return image


def plot_working_set_prepost_comparison(
    *,
    plt: Any,
    original_vertex_values,
    final_vertex_values,
    original_direction_values,
    final_direction_values,
    cap_value: int = 42,
    original_color: str = "#d95f02",
    final_color: str = "#1b9e77",
) -> None:
    import numpy as np
    import pandas as pd

    original_vertices = _clean_integer_values(original_vertex_values, cap_value=cap_value)
    final_vertices = _clean_integer_values(final_vertex_values, cap_value=cap_value)
    original_directions = _clean_integer_values(original_direction_values, cap_value=cap_value)
    final_directions = _clean_integer_values(final_direction_values, cap_value=cap_value)

    vertex_low = int(min(original_vertices.min(), final_vertices.min()))
    direction_low = int(min(original_directions.min(), final_directions.min()))

    vertex_support = np.arange(vertex_low, int(cap_value) + 1, dtype=int)
    direction_support = np.arange(direction_low, int(cap_value) + 1, dtype=int)

    vertex_ymax = 1.08 * max(
        int(pd.Series(original_vertices).value_counts().max()),
        int(pd.Series(final_vertices).value_counts().max()),
    )
    direction_ymax = 1.08 * max(
        int(pd.Series(original_directions).value_counts().max()),
        int(pd.Series(final_directions).value_counts().max()),
    )

    vertex_ticks = np.arange(vertex_low, int(cap_value) + 1, _tick_step(vertex_low, int(cap_value)))
    direction_ticks = np.arange(direction_low, int(cap_value) + 1, _tick_step(direction_low, int(cap_value)))

    vertex_heat = np.zeros((cap_value - vertex_low + 1, cap_value - vertex_low + 1), dtype=int)
    for x_val, y_val in zip(original_vertices, final_vertices):
        vertex_heat[y_val - vertex_low, x_val - vertex_low] += 1

    direction_heat = np.zeros((cap_value - direction_low + 1, cap_value - direction_low + 1), dtype=int)
    for x_val, y_val in zip(original_directions, final_directions):
        direction_heat[y_val - direction_low, x_val - direction_low] += 1

    fig = plt.figure(figsize=(15, 12), constrained_layout=True)
    outer = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.15], wspace=0.22, hspace=0.18)

    vertex_grid = outer[0, 0].subgridspec(2, 1, hspace=0.02)
    direction_grid = outer[0, 1].subgridspec(2, 1, hspace=0.02)

    ax_vertex_top = fig.add_subplot(vertex_grid[0, 0])
    ax_vertex_bottom = fig.add_subplot(vertex_grid[1, 0], sharex=ax_vertex_top)
    ax_direction_top = fig.add_subplot(direction_grid[0, 0])
    ax_direction_bottom = fig.add_subplot(direction_grid[1, 0], sharex=ax_direction_top)
    ax_vertex_heat = fig.add_subplot(outer[1, 0])
    ax_direction_heat = fig.add_subplot(outer[1, 1])

    _plot_split_distribution(
        ax_vertex_top,
        ax_vertex_bottom,
        original_vertices,
        final_vertices,
        support=vertex_support,
        xlim=(vertex_low - 0.5, int(cap_value) + 0.5),
        ymax=vertex_ymax,
        ticks=vertex_ticks,
        title="Exterior vertices",
        xlabel="Vertices per footprint",
        original_color=original_color,
        final_color=final_color,
    )
    _plot_split_distribution(
        ax_direction_top,
        ax_direction_bottom,
        original_directions,
        final_directions,
        support=direction_support,
        xlim=(direction_low - 0.5, int(cap_value) + 0.5),
        ymax=direction_ymax,
        ticks=direction_ticks,
        title="Distinct exterior directions",
        xlabel="Direction groups per footprint",
        original_color=original_color,
        final_color=final_color,
    )

    vertex_image = _plot_transition_heatmap(
        ax_vertex_heat,
        original_vertices,
        final_vertices,
        low_value=vertex_low,
        cap_value=int(cap_value),
        xlim=(vertex_low - 0.5, int(cap_value) + 0.5),
        ylim=(vertex_low - 0.5, int(cap_value) + 0.5),
        ticks=vertex_ticks,
        title="Original to final vertices",
        xlabel="Original vertices",
        ylabel="Final vertices",
        cmap="viridis",
        vmax=int(vertex_heat.max()),
    )
    direction_image = _plot_transition_heatmap(
        ax_direction_heat,
        original_directions,
        final_directions,
        low_value=direction_low,
        cap_value=int(cap_value),
        xlim=(direction_low - 0.5, int(cap_value) + 0.5),
        ylim=(direction_low - 0.5, int(cap_value) + 0.5),
        ticks=direction_ticks,
        title="Original to final directions",
        xlabel="Original directions",
        ylabel="Final directions",
        cmap="magma",
        vmax=int(direction_heat.max()),
    )

    vertex_colorbar = fig.colorbar(vertex_image, ax=ax_vertex_heat, fraction=0.046, pad=0.04)
    vertex_colorbar.set_label("Footprint count (log scale)")
    direction_colorbar = fig.colorbar(direction_image, ax=ax_direction_heat, fraction=0.046, pad=0.04)
    direction_colorbar.set_label("Footprint count (log scale)")

    plt.show()
