"""Lightweight capability discovery facade.

Keep execution imports out of package initialisation so the kernel can lazily
load tool declarations without importing optional capability dependencies.
"""

from .core_specs import load_specs


__all__ = ["load_specs"]
