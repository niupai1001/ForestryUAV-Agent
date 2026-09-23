"""Host Bridge package: authorized host file access and Docker job control.

``server.py`` is intentionally runnable both as a script (``setup.ps1`` starts it
as ``host_bridge/server.py``) and as a module (``host_bridge.server`` in tests).
Adding the project root to ``sys.path`` lets it import the shared, dependency-free
``shared.paths`` helper in both modes, so the containment invariant keeps exactly
one definition across the Runtime and the Bridge.
"""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
