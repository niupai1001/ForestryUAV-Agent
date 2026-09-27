"""Answer code questions at the precision they can actually be answered at.

The failure this replaces: a Run needed to know where something was defined and where
it was used, had only a substring search, and so treated every mention -- definition,
call, docstring, comment -- as if it were the definition. The answers were not wrong
one by one; they were the wrong *kind* of answer, and nothing said so.

So every result states its precision, and a question that needs an index when no index
is available is answered as degraded rather than as a confident text match. ``fs_search``
keeps its contract unchanged: it is the right tool for strings, and it is not replaced.
"""
from __future__ import annotations

from pathlib import Path
import re

from ..retrieval.models import Candidate
from .index_store import LexicalBackend, SymbolIndex
from .models import CodeLocation, ImpactResult, Symbol


class CodeIntelligenceService:
    """Definition / references / callers / impact over one workspace."""

    source_type = "code"

    def __init__(self, root: Path | str, index: SymbolIndex | None = None):
        self.root = Path(root)
        self.index = index or SymbolIndex(self.root)
        self.lexical = LexicalBackend(self.root)
        self._indexed = False

    def _ensure_index(self) -> None:
        """Index lazily, once per service.

        Building it on first use rather than at construction keeps a workspace that
        never asks a code question from paying for one, and keeps construction cheap
        enough to do on every request.
        """
        if self._indexed:
            return
        try:
            self.index.index_workspace()
        except Exception:
            # An unparseable file must not remove code answers entirely; the lexical
            # backend still works without the index.
            pass
        self._indexed = True

    # ------------------------------------------------------------------ queries

    def search_text(self, query: str, limit: int = 30) -> list[CodeLocation]:
        return self.lexical.search(query, limit=limit)

    def find_symbol(self, name: str, limit: int = 10) -> list[Symbol]:
        self._ensure_index()
        return self.index.find_symbols(name, limit)

    def definition(self, name: str) -> list[CodeLocation]:
        self._ensure_index()
        found = self.index.definitions(name)
        if found:
            return found
        # No index entry: fall back to text, but say so. An answer that looks like a
        # definition and is actually a mention is worse than no answer.
        return [location for location in self.lexical.search(name, limit=5)]

    def references(self, name: str) -> list[CodeLocation]:
        self._ensure_index()
        return self.index.references(name)

    def callers(self, name: str) -> list[CodeLocation]:
        self._ensure_index()
        return self.index.callers(name)

    def impact(self, name: str) -> ImpactResult:
        self._ensure_index()
        symbols = self.index.find_symbols(name, limit=1)
        if not symbols:
            return ImpactResult(
                degraded=True, precision="heuristic",
                note=(f"No symbol named {name!r} in the index. Text matches are listed "
                      "as references, but they are mentions, not uses -- check each one."),
                references=self.lexical.search(name, limit=10),
            )
        symbol = symbols[0]
        return ImpactResult(
            symbol=symbol,
            definitions=[self.index._location(symbol)],
            references=self.index.references(symbol.short_name),
            callers=self.index.callers(symbol.short_name),
            precision="structural",
        )

    # ------------------------------------------------------------- invalidation

    def invalidate(self, path: str) -> None:
        """An edit changed this file; what the index says about it is no longer true."""
        try:
            self.index.invalidate(path)
        except Exception:
            pass

    # ------------------------------------------------------------ retrieval hook

    def retrieve(self, query: str, top_k: int = 8) -> list[Candidate]:
        """Code answers in the retrieval contract, so code can be fused with the rest.

        Definitions are given a structural score: when the question names a symbol, the
        definition is the answer, and it should not have to outrank comment matches on
        term overlap.
        """
        candidates: list[Candidate] = []
        self._ensure_index()
        for symbol in self.index.find_symbols(query, limit=3):
            location = self.index._location(symbol)
            candidates.append(Candidate(
                id=symbol.id, source_type=self.source_type,
                content=f"{symbol.kind} {symbol.qualified_name} "
                        f"({location.reference})\n{location.snippet}",
                exact_reference={"path": symbol.path, "line": symbol.start_line},
                version=symbol.version, structural_score=1.0,
                metadata={"kind": symbol.kind, "precision": "structural"},
            ))
        for location in self.lexical.search(query, limit=max(1, top_k - len(candidates))):
            candidates.append(Candidate(
                id=location.reference, source_type=self.source_type,
                content=location.snippet,
                exact_reference={"path": location.path, "line": location.start_line},
                version=location.version,
                metadata={"precision": location.precision},
            ))
        return candidates[:top_k]


def looks_like_symbol(query: str) -> bool:
    """Whether a query names a thing rather than describing one."""
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", (query or "").strip()))


__all__ = ["CodeIntelligenceService", "looks_like_symbol"]
