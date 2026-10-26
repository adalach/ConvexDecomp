"""A building's convex zones: every zone of its perimeter decomposed by the engine, a zone the engine cannot cut
merged with its neighbours, and the whole footprint as the last resort. See README.md in this folder.

Needs only numpy, shapely and ConvexDecomp's engine (``convexdecomp.osm.decompose``, ``convexdecomp.core``,
``convexdecomp.geometry``) and corner-ray perimeter (``convexdecomp.corner_perimeter``).
"""

from .building import (BuildingZones, Decompose, Piece, decompose_pieces, decompose_zones, engine_call,
                       merge_failed_pieces)
from .preview import (AFTER_LAYER, BEFORE_LAYER, LAYER_TITLES, LAYERS, NUMBERS, layers, lines, numbers, piece_records,
                      preview, zone_records)
from .settings import SECTIONS, ZoneConfig, engine_config, settings_table

__all__ = ["AFTER_LAYER", "BEFORE_LAYER", "BuildingZones", "Decompose", "LAYERS", "LAYER_TITLES", "NUMBERS", "Piece",
           "SECTIONS", "ZoneConfig", "decompose_pieces", "decompose_zones", "engine_call", "engine_config", "layers",
           "lines",
           "merge_failed_pieces", "numbers", "piece_records", "preview", "settings_table", "zone_records"]
