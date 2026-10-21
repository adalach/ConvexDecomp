"""The corner-ray perimeter: perimeter zones along the facades from a band and corner rays, cleaned up by nine rules,
without ever moving the footprint's outline. See README.md in this folder.

Needs only numpy, shapely and ``convexdecomp.core.scores``.
"""

from .preview import LAYER_TITLES, LAYERS, NUMBERS, layers, numbers, preview, zone_records
from .procedure import STAGE_TITLES, STAGES, PerimeterResult, corner_ray_perimeter
from .settings import SECTIONS, CornerRayConfig, settings_table
from .zones import PerimeterError, sharp_corners, zone_counts

__all__ = ["CornerRayConfig", "LAYERS", "LAYER_TITLES", "NUMBERS", "PerimeterError", "PerimeterResult", "SECTIONS",
           "STAGES", "STAGE_TITLES", "corner_ray_perimeter", "layers", "numbers", "preview", "settings_table",
           "sharp_corners", "zone_counts", "zone_records"]
