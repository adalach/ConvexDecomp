"""A building's convex zones: every zone of its perimeter decomposed by the engine, a zone the engine cannot cut
merged with its neighbours, and the whole footprint as the last resort. See README.md in this folder.

Needs only numpy, shapely and ConvexDecomp's engine (``convexdecomp.osm.decompose``, ``convexdecomp.core``,
``convexdecomp.geometry``) and corner-ray perimeter (``convexdecomp.corner_perimeter``).
"""

from .building import (BuildingZones, Decompose, Piece, already_convex, decompose_pieces, decompose_zones,
                       engine_call, is_final, merge_failed_pieces)
from .preview import (AFTER_LAYER, BEFORE_LAYER, LAYER_TITLES, LAYERS, NUMBERS, layers, lines, numbers, piece_records,
                      preview, zone_records)
from .settings import SECTIONS, ZoneConfig, engine_config, settings_table
from .tree import search_tree
from .variants import TERMS, fingerprint, score_terms, variant_records

__all__ = ["AFTER_LAYER", "BEFORE_LAYER", "BuildingZones", "Decompose", "LAYERS", "LAYER_TITLES", "NUMBERS", "Piece",
           "SECTIONS", "TERMS", "ZoneConfig", "already_convex", "decompose_pieces", "decompose_zones",
           "engine_call", "engine_config", "fingerprint", "is_final", "layers", "lines",
           "merge_failed_pieces", "numbers", "piece_records", "preview", "score_terms", "search_tree",
           "settings_table", "variant_records", "zone_records"]
