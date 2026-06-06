"""Public access to OSM perimeter correspondence helpers."""

from convexdecomp.osm.perimeter import (
    match_ring_correspondence,
    ring_segment_records,
    segment_angle_deg,
    segment_length,
)

__all__ = [
    "match_ring_correspondence",
    "ring_segment_records",
    "segment_angle_deg",
    "segment_length",
]
