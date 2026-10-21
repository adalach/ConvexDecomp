"""The settings of the corner-ray perimeter: one frozen object, :class:`CornerRayConfig`, passed to every step.

Each field carries what a settings page needs in its metadata: the section it belongs to, a title, a help text, the
unit, the values a run accepts (``ge``/``le``), a slider range inside them (``lo``/``hi``/``step``) that holds the
default, the switch it needs and whether it is an advanced value. :func:`settings_table` gives the whole table as
plain data (``python -m convexdecomp.corner_perimeter settings`` prints it as JSON).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

#: the sections of the settings, in the order the procedure uses them: (key, title, what the section does)
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("band", "Band and rays",
     "The footprint is offset inwards by the band depth; its inner boundary is the band line. Every corner casts a "
     "ray along the bisector of its corner, cut off at the band line and stopped by the first ray it reaches."),
    ("wing_ends", "Wing ends",
     "The two corners of a wing's end wall cast no ray when the wing is too narrow for two zones side by side; the "
     "wing's zones then reach from one side to the other."),
    ("rays", "Rays that meet",
     "Rule 1: rays that meet merge into one ray, which goes on in the middle. Rule 2: two rays running in opposite "
     "directions close to each other's corners become one line from corner to corner. Rule 3: a ray from a convex "
     "corner that reaches nothing is discarded."),
    ("narrow_core", "Narrow core",
     "Rule 4: legs, notches and narrow parts of the core become lines: the shorter of the two sides moves onto the "
     "longer one, or both move half way when their lengths are similar."),
    ("zones", "Close points and narrow zones",
     "Rule 5: zone corners close to each other become one; it runs again after rule 6 with a longer reach. Rule 6: "
     "a narrow four-cornered zone gives way to its shorter diagonal."),
    ("tiny", "Tiny zones and sharp corners",
     "Rule 7: zones thinner than the zone width that touch become one convex zone. Rule 8: a lone thin zone merges "
     "into a neighbour, after relinking one of its edges if needed. Rule 9: very sharp zone corners are cut off."),
)


def setting(default: Any, *, section: str, title: str, help: str, unit: str | None = None,
            ge: float | None = None, le: float | None = None, lo: float | None = None, hi: float | None = None,
            step: float | None = None, needs: str | None = None, advanced: bool = False) -> Any:
    """A field of :class:`CornerRayConfig` with its page metadata."""
    return field(default=default, metadata=dict(section=section, title=title, help=help, unit=unit, ge=ge, le=le,
                                                lo=lo, hi=hi, step=step, needs=needs, advanced=advanced))


@dataclass(frozen=True)
class CornerRayConfig:
    """Every threshold of the corner-ray perimeter. The defaults are the settings chosen on 2026-10-20."""

    # band and rays
    band_m: float = setting(
        4.5, section="band", title="Band depth", unit="m", ge=0.5, le=20.0, lo=2.0, hi=8.0, step=0.1,
        help="How deep the perimeter band along the facades is: the footprint offset inwards by this distance, "
             "with mitred corners. What lies inside the band line is the core.")
    tie_m: float = setting(
        0.10, section="band", title="Tie between two rays", unit="m", ge=0.0, le=1.0, lo=0.0, hi=0.5, step=0.01,
        advanced=True,
        help="Rays grow from their corners at the same speed; two rays reaching a crossing within this distance of "
             "each other both stop there.")
    min_turn_deg: float = setting(
        1.0, section="band", title="Smallest corner turn", unit="deg", ge=0.0, le=10.0, lo=0.0, hi=5.0, step=0.1,
        advanced=True, help="A vertex where the outline turns by less than this is not a corner and casts no ray.")

    # wing ends
    wing_end_max_m: float = setting(
        4.0, section="wing_ends", title="Narrowest wing that keeps its end rays", unit="m", ge=0.0, le=10.0, lo=0.0,
        hi=6.0, step=0.1,
        help="The two convex corners of an end wall shorter than this cast no ray (0 switches the wing ends off). "
             "Two zones side by side need twice the zone width.")
    wing_end_parallel_deg: float = setting(
        30.0, section="wing_ends", title="Side walls parallel within", unit="deg", ge=0.0, le=180.0, lo=0.0,
        hi=60.0, step=1.0,
        help="Only an end wall whose two side walls are parallel within this angle counts as a wing's end (the two "
             "corner angles add up to 180 degrees within it). On a curved facade drawn as many short walls the walls "
             "turn little at each corner, so no short wall there loses its rays; 180 lets every short wall count.")
    wing_end_long_sides: bool = setting(
        False, section="wing_ends", title="Only long wings", advanced=True,
        help="Also require both side walls to be at least as long as the end wall (a wing, not a step).")

    # rays that meet (rules 1 to 3)
    opposite_gap_m: float = setting(
        1.0, section="rays", title="Opposite rays closer than", unit="m", ge=0.0, le=5.0, lo=0.0, hi=3.0, step=0.05,
        help="Rule 2: two rays running in opposite directions that each pass within this distance of the other's "
             "corner become one line from corner to corner.")
    opposite_angle_deg: float = setting(
        20.0, section="rays", title="Opposite within", unit="deg", ge=0.0, le=90.0, lo=0.0, hi=45.0, step=1.0,
        help="Rule 2: how far from exactly opposite the two rays' directions may be.")
    keep_reflex_rays: bool = setting(
        True, section="rays", title="Keep the rays of reflex corners",
        help="Rule 3 discards a ray that reaches the far facade without meeting the band line or another ray. A ray "
             "from a reflex corner is kept and cast again after rule 1: a zone can only be convex if a line leaves "
             "every reflex corner.")
    recast_discarded: bool = setting(
        False, section="rays", title="Discard no ray", advanced=True,
        help="Rule 3 off: every ray that reaches nothing is cast again after rule 1, from convex corners too.")

    # narrow core (rule 4)
    leg_width_m: float = setting(
        2.0, section="narrow_core", title="Narrowest leg kept as core", unit="m", ge=0.0, le=10.0, lo=0.0, hi=4.0,
        step=0.05,
        help="A leg of the core (out along one side, across a short tip, back along a nearly parallel side) "
             "narrower than this becomes a line.")
    leg_angle_deg: float = setting(
        15.0, section="narrow_core", title="Sides parallel within", unit="deg", ge=0.0, le=45.0, lo=0.0, hi=30.0,
        step=0.5, help="How far from parallel the two sides of a leg, or a notch's floor and its side, may be.")
    fill_notches: bool = setting(
        True, section="narrow_core", title="Fill notches",
        help="A stretch of the core's side stepped into the core, where it leaves the core thinner than the notch "
             "passage, moves onto the line of that side, so the notch fills.")
    notch_passage_m: float = setting(
        1.0, section="narrow_core", title="Notch passage", unit="m", ge=0.0, le=5.0, lo=0.0, hi=3.0, step=0.05,
        needs="fill_notches", help="A notch is filled where the core across its floor is thinner than this.")
    narrow_core_m: float = setting(
        1.0, section="narrow_core", title="Narrowest core", unit="m", ge=0.0, le=5.0, lo=0.0, hi=3.0, step=0.05,
        help="Core thinner than this becomes a line: whole parts (with the switch below), tapered tips and "
             "passages between wider parts.")
    collapse_narrow_parts: bool = setting(
        True, section="narrow_core", title="Whole narrow parts become lines",
        help="A whole core part thinner than the narrowest core everywhere becomes a line between its two ends.")
    similar_sides_ratio: float = setting(
        0.9, section="narrow_core", title="Similar sides", unit="ratio", ge=0.0, le=1.0, lo=0.0, hi=1.0, step=0.05,
        help="The shorter side moves onto the longer one; when the shorter is at least this share of the longer, "
             "both move half way.")

    # close points and narrow zones (rules 5 and 6)
    close_points_m: float = setting(
        0.5, section="zones", title="Close points", unit="m", ge=0.0, le=3.0, lo=0.0, hi=1.5, step=0.05,
        help="Rule 5: zone corners closer than this become one point at their mean. Points on the outline are "
             "never moved.")
    corner_group_m: float = setting(
        1.2, section="zones", title="Close points, second pass", unit="m", ge=0.0, le=5.0, lo=0.0, hi=3.0,
        step=0.05, help="Rule 5 again, after rule 6: zone corners closer than this become one.")
    corner_group_span_m: float = setting(
        2.0, section="zones", title="Widest group, second pass", unit="m", ge=0.0, le=10.0, lo=0.0, hi=5.0,
        step=0.1, help="Rule 5 again: a group of corners merged into one may be at most this wide.")
    narrow_zone_m: float = setting(
        2.0, section="zones", title="Narrow zone", unit="m", ge=0.0, le=10.0, lo=0.0, hi=4.0, step=0.05,
        help="Rule 6: a four-cornered perimeter zone narrower than this, with a perimeter zone across each long "
             "side, gives way to its shorter diagonal.")

    # tiny zones and sharp corners (rules 7 to 9)
    min_zone_width_m: float = setting(
        2.0, section="tiny", title="Zone width", unit="m", ge=0.0, le=10.0, lo=0.0, hi=4.0, step=0.05,
        help="A perimeter zone narrower than this is thin: rules 7 and 8 merge thin zones, and a merge must leave "
             "a zone at least this wide.")
    relink_m: float = setting(
        4.0, section="tiny", title="Relink reach", unit="m", ge=0.0, le=20.0, lo=0.0, hi=8.0, step=0.25,
        help="Rule 8: an inner edge of a lone thin zone may move one end to a zone corner this close, so the zone "
             "can merge into a neighbour.")
    sharp_corner_deg: float = setting(
        20.0, section="tiny", title="Sharp corner", unit="deg", ge=0.0, le=60.0, lo=0.0, hi=45.0, step=1.0,
        help="Rule 9: a zone corner sharper than this is cut off; the zone across its shorter edge takes the tip.")
    sharp_keep_convex: bool = setting(
        False, section="tiny", title="Keep convex zones convex", advanced=True,
        help="Rule 9: skip a cut that would make the convex zone taking the tip non-convex.")
    repeat_tiny_after_sharp: bool = setting(
        True, section="tiny", title="Merge tiny zones again", advanced=True,
        help="Rules 7 and 8 run once more after rule 9, for thin zones its cuts leave.")

    def __post_init__(self) -> None:
        for f in fields(self):
            value, m = getattr(self, f.name), f.metadata
            if isinstance(f.default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{f.name} must be true or false, not {value!r}")
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{f.name} must be a number, not {value!r}")
            if m["ge"] is not None and value < m["ge"] or m["le"] is not None and value > m["le"]:
                raise ValueError(f"{f.name} = {value} is outside {m['ge']} to {m['le']}")

    def values(self) -> dict[str, Any]:
        """The settings as a plain dict."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def changed(self) -> dict[str, Any]:
        """The settings that differ from the defaults."""
        default = CornerRayConfig()
        return {k: v for k, v in self.values().items() if v != getattr(default, k)}


def settings_table() -> list[dict[str, Any]]:
    """The settings as the sections of a page: each with its fields, their default and metadata."""
    by_section: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in SECTIONS}
    for f in fields(CornerRayConfig):
        m = dict(f.metadata)
        kind = "switch" if isinstance(f.default, bool) else "number"
        by_section[m.pop("section")].append({"name": f.name, "kind": kind, "default": f.default, **m})
    return [{"key": key, "title": title, "about": about, "fields": by_section[key]} for key, title, about in SECTIONS]


__all__ = ["CornerRayConfig", "SECTIONS", "settings_table"]
