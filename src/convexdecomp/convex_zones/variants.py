"""The convex variants of a piece, ranked as the engine ranks them, with their score terms; and fingerprints, so a
piece and a chosen variant can be found again after the building is decomposed again.

The engine keeps every convex variant it found in its result (``ranked_variants``: best first, by score, a variant
without fallback cuts first among equal scores, then in the order found). :func:`variant_records` gives them as rows
with each score term and its share of the score, as the search explorer's list of variants shows them.
:func:`fingerprint` names a polygon or a set of parts by its geometry (normalized, rounded to a micrometre).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping
from typing import Any

import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from convexdecomp.core.convexity import is_convex_polygon
from convexdecomp.core.scores import PartitionScore, ZoneMetrics, minimum_width, zone_metrics
from convexdecomp.osm.decompose import ConvexDecompositionConfig

from .settings import ZoneConfig, engine_config

#: the score's terms, in the order the explorer draws them
TERMS = ("parts", "compactness", "rectangularity", "narrow", "small")


def fingerprint(geometry: BaseGeometry | Iterable[Polygon]) -> str:
    """16 hex digits that name a polygon, or a set of parts in any order, by its geometry: each polygon normalized
    (GEOS's canonical vertex order) and its coordinates rounded to 1e-6 m."""
    parts = [geometry] if isinstance(geometry, BaseGeometry) else list(geometry)
    texts = sorted(shapely.to_wkt(shapely.normalize(p), rounding_precision=6, trim=True) for p in parts)
    return hashlib.sha256("|".join(texts).encode()).hexdigest()[:16]


def _score(cfg: ConvexDecompositionConfig) -> PartitionScore | None:
    """The engine's score of a variant as a PartitionScore (None for a score function of another kind)."""
    if cfg.score_fn is not None:
        return cfg.score_fn if isinstance(cfg.score_fn, PartitionScore) else None
    return PartitionScore(weight_parts=cfg.weight_parts, weight_compactness=cfg.weight_compactness,
                          area_weighting=cfg.area_weighting, weight_narrow=cfg.weight_narrow,
                          narrow_width_m=cfg.narrow_width_m)


def _metrics(part: Polygon, rectangularity: bool) -> ZoneMetrics:
    if rectangularity:
        return zone_metrics(part)
    area, perimeter = float(part.area), float(part.length)
    compactness = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter > 0.0 else 0.0
    return ZoneMetrics(area, compactness, 0.0, minimum_width(part), bool(is_convex_polygon(part)))


def score_terms(parts: list[Polygon], cfg: ConvexDecompositionConfig) -> dict[str, float]:
    """Each term of the engine's score of ``parts`` (:data:`TERMS`); they add up to the score. Empty for a score
    function that is not a ``PartitionScore``."""
    score = _score(cfg)
    if score is None:
        return {}
    return score.terms_from_metrics([_metrics(p, bool(score.weight_rectangularity)) for p in parts])


def variant_records(stats: Mapping[str, Any], config: ZoneConfig | ConvexDecompositionConfig | None = None,
                    ) -> list[dict[str, Any]]:
    """A piece's convex variants as rows, best first (``stats`` is the engine's result for the piece, for example
    ``Piece.stats``): ``rank`` (0 is the engine's choice), ``fingerprint``, ``score``, ``terms`` and ``shares`` (each
    term and its share of the score), ``zones``, ``narrowest_m``, ``smallest_m2``, ``fallback_used`` and ``parts``.
    ``config`` gives the score's weights (the package's defaults when None)."""
    cfg = config if isinstance(config, ConvexDecompositionConfig) else engine_config(config)
    rows = []
    for rank, variant in enumerate(stats.get("ranked_variants") or []):
        parts = list(variant["parts"])
        terms = score_terms(parts, cfg)
        score = float(variant["score"])
        rows.append({
            "rank": rank,
            "fingerprint": fingerprint(parts),
            "score": score,
            "terms": terms,
            "shares": {k: (v / score if score else 0.0) for k, v in terms.items()},
            "zones": len(parts),
            "narrowest_m": min(minimum_width(p) for p in parts),
            "smallest_m2": min(p.area for p in parts),
            "fallback_used": variant.get("fallback_used"),
            "parts": parts,
        })
    return rows


__all__ = ["TERMS", "fingerprint", "score_terms", "variant_records"]
