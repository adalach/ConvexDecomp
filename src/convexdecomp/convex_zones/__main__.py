"""``python -m convexdecomp.convex_zones settings`` prints the settings table as JSON;
``python -m convexdecomp.convex_zones zones "<WKT>" [name=value ...]`` runs the corner-ray perimeter of a footprint
and prints the fate, the numbers and the convex zones."""

from __future__ import annotations

import json
import sys

from shapely import wkt

from .preview import preview, zone_records
from .settings import ZoneConfig, settings_table


def main(argv: list[str]) -> int:
    if argv[:1] == ["settings"]:
        print(json.dumps(settings_table(), indent=2))
        return 0
    if argv[:1] == ["zones"] and len(argv) >= 2:
        overrides = {}
        for item in argv[2:]:
            name, _, value = item.partition("=")
            default = getattr(ZoneConfig(), name)
            if isinstance(default, bool):
                overrides[name] = value.lower() in ("1", "true", "yes", "on")
            else:
                overrides[name] = int(value) if isinstance(default, int) else float(value)
        got = preview(wkt.loads(argv[1]), config=ZoneConfig(**overrides))
        rows = [] if got["result"] is None else [{**r, "geometry": r["geometry"].wkt}
                                                 for r in zone_records(got["result"])]
        print(json.dumps({"fate": got["fate"], "numbers": got["numbers"], "seconds": got["seconds"], "zones": rows},
                         indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
