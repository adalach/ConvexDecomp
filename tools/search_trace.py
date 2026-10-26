"""The search tracer (``convexdecomp.osm.search_trace``) for the tools, with this repository's ``src/`` first on
``sys.path``, so the tools trace the checkout they live in. ``REPO_ROOT`` is the repository."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from convexdecomp.osm import search_trace as _trace  # noqa: E402

globals().update({name: value for name, value in vars(_trace).items() if not name.startswith("__")})
__all__ = [*_trace.__all__, "REPO_ROOT"]
