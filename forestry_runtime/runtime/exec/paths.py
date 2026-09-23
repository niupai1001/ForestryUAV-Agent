"""In-process re-export of the shared path-containment invariant.

The definition lives in ``shared/paths.py`` so the Runtime process and the Host
Bridge process share one object instead of two copies that can drift. Keep this
module a pure re-export.
"""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.paths import is_within  # noqa: E402  (path bootstrap must run first)


__all__ = ["is_within"]
