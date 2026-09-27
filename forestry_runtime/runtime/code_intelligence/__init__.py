"""Code understanding, at the precision each question can actually be given.

Substring search answers "where does this string occur". It cannot answer "where is
this defined", "who calls it" or "what breaks if I change it", and returning every
mention as if it were a definition is how a Run ends up editing a comment.

Three layers, each reporting its own precision -- ``heuristic`` for text, ``structural``
for the symbol index, and reserved for a future LSP/SCIP backend the questions that
need real semantic analysis. A question that cannot be answered precisely is answered
as degraded, never as a confident guess.

``fs_search`` is untouched: it is the right tool for strings, and this is not a
replacement for it.
"""
from __future__ import annotations

from .index_store import LexicalBackend, SymbolIndex, file_version
from .models import CodeLocation, ImpactResult, Symbol
from .service import CodeIntelligenceService, looks_like_symbol

__all__ = [
    "CodeIntelligenceService", "CodeLocation", "ImpactResult", "LexicalBackend",
    "Symbol", "SymbolIndex", "file_version", "looks_like_symbol",
]
