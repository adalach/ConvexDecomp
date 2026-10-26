"""The settings of a building's convex zones: one frozen object, :class:`ZoneConfig`, for the engine's rules, score,
cuts and search and for the fallback when a piece cannot be cut.

Each field carries what a settings page needs in its metadata, as :mod:`convexdecomp.corner_perimeter.settings`
does: the section, a title, a help text, the unit, the values a run accepts (``ge``/``le``), a slider range inside
them (``lo``/``hi``/``step``) that holds the default, the switch it needs and whether it is an advanced value.
:func:`settings_table` gives the whole table as plain data (``python -m convexdecomp.convex_zones settings`` prints it
as JSON). :func:`engine_config` turns the settings into the engine's ``ConvexDecompositionConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from convexdecomp.core.search import AdaptiveSearchConfig
from convexdecomp.osm.decompose import ConvexDecompositionConfig

#: the sections of the settings: (key, title, what the section does)
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("rules", "Zone rules",
     "What every convex zone must satisfy. A cut that would leave a zone thinner than the narrowest zone or "
     "smaller than the smallest zone is never made. The corner rule is relaxed where no cut of a piece passes it."),
    ("score", "Score",
     "Of the convex variants the search finds for a piece, the one with the lowest score is kept: the number of "
     "zones plus the compactness weight times one minus the zones' mean compactness."),
    ("cuts", "Cuts",
     "A cut starts at a reflex corner and runs along one of the corner's two walls carried on, or along the "
     "bisector of its angle, to the first wall it meets. Every cut splits a piece into two."),
    ("search", "Search",
     "A beam search over the ways to cut a piece: it starts shallow and narrow and searches deeper, then wider, "
     "only while it has found no convex variant. It stops after enough convex variants or dead ends."),
    ("fallback", "When a piece cannot be cut",
     "A piece the search cannot make convex is merged with a neighbouring piece and the union decomposed. When "
     "merging gives up, the whole footprint is decomposed instead, both within a time budget. A building where "
     "neither works has no decomposition."),
)


def setting(default: Any, *, section: str, title: str, help: str, unit: str | None = None,
            ge: float | None = None, le: float | None = None, lo: float | None = None, hi: float | None = None,
            step: float | None = None, needs: str | None = None, advanced: bool = False) -> Any:
    """A field of :class:`ZoneConfig` with its page metadata."""
    return field(default=default, metadata=dict(section=section, title=title, help=help, unit=unit, ge=ge, le=le,
                                                lo=lo, hi=hi, step=step, needs=needs, advanced=advanced))


@dataclass(frozen=True)
class ZoneConfig:
    """Every threshold and switch of a building's convex zones; the defaults are the chosen settings (those of
    ``OSM_2_Decomposition.ipynb``, with a time budget for the fallback that the notebook leaves off). Values outside a
    field's ``ge``/``le`` raise ``ValueError``."""

    # zone rules
    min_zone_width_m: float = setting(
        1.2, section="rules", title="Narrowest zone", unit="m", ge=0.0, le=10.0, lo=0.0, hi=3.0, step=0.1,
        help="No zone may be thinner than this: its smallest width in any direction. A hard rule: a piece that "
             "cannot be cut into zones this wide, a thin convex piece included, has no decomposition of its own. "
             "0 turns the rule off.")
    min_corner_deg: float = setting(
        30.0, section="rules", title="Sharpest new corner", unit="deg", ge=0.0, le=89.0, lo=0.0, hi=60.0, step=1.0,
        help="No cut may make a corner sharper than this; the outline's own sharp corners never block a cut. "
             "Relaxed where no cut of a piece passes it. 0 turns the rule off.")
    min_area_m2: float = setting(
        2.0, section="rules", title="Smallest zone", unit="m2", ge=0.0, le=100.0, lo=0.0, hi=10.0, step=0.5,
        help="No cut may leave a zone smaller than this. A convex piece that is smaller stays as it is (one under "
             "0.5 m2 has no decomposition).")

    # score
    weight_compactness: float = setting(
        20.0, section="score", title="Compactness weight", ge=0.0, le=200.0, lo=0.0, hi=50.0, step=1.0,
        help="How much the zones' mean compactness (4 pi area / perimeter squared: 0.785 for a square) counts "
             "against their number: at 20, zones that are 0.05 more compact on average are worth one zone more.")
    weight_parts: float = setting(
        1.0, section="score", title="Zone count weight", ge=0.0, le=10.0, lo=0.0, hi=5.0, step=0.1, advanced=True,
        help="What each zone costs in the score.")
    area_weighting: float = setting(
        0.0, section="score", title="Area weighting of the compactness", ge=0.0, le=2.0, lo=0.0, hi=1.0, step=0.1,
        advanced=True,
        help="0 lets every zone count the same in the mean compactness; 1 weights each zone by its area, as the "
             "EG-ICE 2026 paper did, so a small or thin zone hardly counts.")
    weight_narrow: float = setting(
        0.0, section="score", title="Narrow zone cost", ge=0.0, le=50.0, lo=0.0, hi=10.0, step=0.5, advanced=True,
        help="Cost of a zone narrower than the narrow width, in proportion to how much narrower it is: at 5 and "
             "2 m, a 1 m zone costs as much as 2.5 zones more. 0 turns the cost off.")
    narrow_width_m: float = setting(
        2.0, section="score", title="Narrow below", unit="m", ge=0.0, le=10.0, lo=0.5, hi=4.0, step=0.1,
        advanced=True, help="The width below which a zone costs the narrow zone cost.")

    # cuts
    bisector_always: bool = setting(
        True, section="cuts", title="Bisector cuts",
        help="Every reflex corner also offers the cut along the bisector of its angle, next to its two walls "
             "carried on. Otherwise the bisector is tried only where neither wall gives a cut.")
    min_concavity_deg: float = setting(
        2.0, section="cuts", title="Reflex from", unit="deg", ge=0.0, le=45.0, lo=0.0, hi=10.0, step=0.5,
        advanced=True, help="A corner that turns inwards by less than this counts as straight and starts no cut.")
    cut_snap_vertex_tolerance_m: float = setting(
        0.1, section="cuts", title="Snap a cut to a corner within", unit="m", ge=0.0, le=1.0, lo=0.0, hi=0.5,
        step=0.01, advanced=True,
        help="A cut that passes this close to a corner before reaching a wall ends at the corner, so no hairline "
             "sliver is left beside it.")

    # search
    adaptive: bool = setting(
        True, section="search", title="Search deeper only where needed",
        help="Start with the first depth and width; while no convex variant is found, search one cut deeper, then "
             "one state wider, up to the deepest search and the widest beam. Off: search at the deepest and widest "
             "at once.")
    start_depth: int = setting(
        4, section="search", title="First depth", ge=1, le=100, lo=1, hi=24, step=1, needs="adaptive",
        help="How many cuts in a row the first search makes at most.")
    start_width: int = setting(
        3, section="search", title="First width", ge=1, le=100, lo=1, hi=16, step=1, needs="adaptive",
        help="How many open states the first search keeps.")
    depth: int = setting(
        24, section="search", title="Deepest search", ge=1, le=100, lo=1, hi=40, step=1,
        help="The most cuts in a row the search makes.")
    width: int = setting(
        4, section="search", title="Widest beam", ge=1, le=100, lo=1, hi=16, step=1,
        help="The most open states the search keeps.")
    max_variants: int = setting(
        32, section="search", title="Enough convex variants", ge=1, le=1000, lo=1, hi=128, step=1,
        help="The search of a piece stops once it has found this many convex variants.")
    max_dead_ends: int = setting(
        500, section="search", title="Dead ends before giving up", ge=1, le=100000, lo=50, hi=5000, step=50,
        help="The search of a piece gives up after this many dead ends (states it cannot cut further, or at the "
             "depth limit, without being convex). It also bounds the search's time.")
    max_reflex_per_node: int = setting(
        8, section="search", title="Reflex corners tried per state", ge=1, le=100, lo=1, hi=24, step=1,
        advanced=True, help="How many reflex corners of a piece offer cuts in one state, the most promising first.")
    depth_step: int = setting(
        1, section="search", title="Depth added per retry", ge=1, le=24, lo=1, hi=8, step=1, needs="adaptive",
        advanced=True, help="How many cuts deeper each new search goes.")
    width_step: int = setting(
        1, section="search", title="Width added per retry", ge=1, le=16, lo=1, hi=8, step=1, needs="adaptive",
        advanced=True, help="How many states wider each new search goes once it is at the deepest search.")
    beam_area_weighting: float = setting(
        1.0, section="search", title="Area weighting of the beam", ge=0.0, le=2.0, lo=0.0, hi=1.0, step=0.1,
        advanced=True,
        help="The area weighting of the score that ranks the open states while the search runs. 1 weights each "
             "piece by its area, so a state that has cut one neat block off a big remainder is not dropped.")

    # fallback
    merge_failed: bool = setting(
        True, section="fallback", title="Merge a piece that cannot be cut",
        help="A piece the search cannot make convex is merged with a neighbour (sharing the longest edge first) and "
             "the union decomposed; the first union that decomposes replaces the two pieces. When none does, the "
             "piece is merged with the neighbour it shares most edge with and tried again from there.")
    merge_max_share: float = setting(
        0.5, section="fallback", title="Largest merged piece", ge=0.0, le=1.0, lo=0.1, hi=1.0, step=0.05,
        needs="merge_failed",
        help="Merging gives up when a merged piece would hold more than this share of the building's floor area.")
    min_shared_edge_m: float = setting(
        0.05, section="fallback", title="Neighbours share at least", unit="m", ge=0.0, le=5.0, lo=0.0, hi=1.0,
        step=0.01, needs="merge_failed", advanced=True,
        help="Two pieces are neighbours when they share at least this much edge.")
    whole_footprint: bool = setting(
        True, section="fallback", title="Whole footprint as the last resort",
        help="When a piece cannot be cut (and merging is off or gives up), the whole footprint is decomposed "
             "instead of the perimeter's zones; the building keeps its zones when that fails too.")
    fallback_max_seconds: float = setting(
        10.0, section="fallback", title="Time for the fallback", unit="s", ge=0.0, le=3600.0, lo=0.0, hi=60.0,
        step=1.0,
        help="The merges and the whole footprint of one building may search this long together: a search still "
             "running when the time is up stops, and a building not decomposed by then has no decomposition. 0 sets "
             "no limit. On the 990 test buildings the slowest fallback that succeeds takes 3 s on a busy machine; "
             "a building near the limit could decompose on a faster machine and not on a slower one.")

    def __post_init__(self) -> None:
        for f in fields(self):
            value, m = getattr(self, f.name), f.metadata
            if isinstance(f.default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{f.name} must be true or false, not {value!r}")
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{f.name} must be a number, not {value!r}")
            if isinstance(f.default, int) and value != int(value):
                raise ValueError(f"{f.name} must be a whole number, not {value!r}")
            if m["ge"] is not None and value < m["ge"] or m["le"] is not None and value > m["le"]:
                raise ValueError(f"{f.name} = {value} is outside {m['ge']} to {m['le']}")

    def values(self) -> dict[str, Any]:
        """The settings as a plain dict."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def changed(self) -> dict[str, Any]:
        """The settings that differ from the defaults."""
        default = ZoneConfig()
        return {k: v for k, v in self.values().items() if v != getattr(default, k)}


def engine_config(config: ZoneConfig | None = None, **overrides: Any) -> ConvexDecompositionConfig:
    """The engine's ``ConvexDecompositionConfig`` for these settings (with ``track_time`` on); ``overrides`` are
    passed on to it last, for example ``**PAPER_2026_SEARCH`` of :mod:`convexdecomp.osm.decompose`."""
    c = config or ZoneConfig()
    kwargs: dict[str, Any] = dict(
        search_depth=int(c.depth),
        search_width=int(c.width),
        max_reflex_per_node=int(c.max_reflex_per_node),
        max_variants_per_polygon=int(c.max_variants),
        max_failed_states=int(c.max_dead_ends),
        track_time=True,
        min_area_m2=float(c.min_area_m2),
        weight_parts=float(c.weight_parts),
        weight_compactness=float(c.weight_compactness),
        area_weighting=float(c.area_weighting),
        beam_area_weighting=float(c.beam_area_weighting),
        weight_narrow=float(c.weight_narrow),
        narrow_width_m=float(c.narrow_width_m),
        min_concavity_deg=float(c.min_concavity_deg),
        cut_snap_vertex_tolerance_m=float(c.cut_snap_vertex_tolerance_m),
        bisector_always=bool(c.bisector_always),
        min_corner_deg=float(c.min_corner_deg),
        min_zone_width_m=float(c.min_zone_width_m),
        # without the widening, the search starts (and stays) at the deepest and widest
        adaptive_search=AdaptiveSearchConfig(
            enabled=bool(c.adaptive),
            initial_depth=int(c.start_depth) if c.adaptive else None,
            initial_width=int(c.start_width) if c.adaptive else None,
            max_depth=int(c.depth),
            max_width=int(c.width),
            depth_step=int(c.depth_step),
            width_step=int(c.width_step),
        ),
    )
    kwargs.update(overrides)
    return ConvexDecompositionConfig(**kwargs)


def settings_table() -> list[dict[str, Any]]:
    """The settings as the sections of a page: each with its fields, their kind (``switch``, ``integer`` or
    ``number``), default and metadata."""
    by_section: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in SECTIONS}
    for f in fields(ZoneConfig):
        m = dict(f.metadata)
        kind = "switch" if isinstance(f.default, bool) else "integer" if isinstance(f.default, int) else "number"
        by_section[m.pop("section")].append({"name": f.name, "kind": kind, "default": f.default, **m})
    return [{"key": key, "title": title, "about": about, "fields": by_section[key]} for key, title, about in SECTIONS]


__all__ = ["SECTIONS", "ZoneConfig", "engine_config", "settings_table"]
