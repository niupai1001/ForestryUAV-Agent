"""What a code answer looks like, independent of how it was found.

``fs_search`` answers exactly one question -- "does this string occur, and where" --
by walking files and comparing each line. That is the right answer for an error code
or a config key, and the wrong one for "where is this defined", "who calls it", or
"what breaks if I change it": a substring match cannot tell a definition from a
call, nor a comment from code, and it reports all three identically.

The distinction that matters is **precision**, and every answer carries it. A
definition found in a symbol index is a fact; one guessed from a text match is not,
and the model is entitled to know which it got.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: How an answer was obtained. Carried on every result because "found in the index"
#: and "looks like it from the text" must not be reported as the same thing.
Precision = Literal["exact", "structural", "heuristic"]


@dataclass
class CodeLocation:
    path: str = ""
    start_line: int = 0
    end_line: int = 0
    snippet: str = ""
    #: File version at the time of the answer, so a stale answer is detectable.
    version: str = ""
    precision: Precision = "heuristic"

    def as_dict(self) -> dict:
        return {
            "path": self.path, "start_line": self.start_line, "end_line": self.end_line,
            "snippet": self.snippet, "version": self.version, "precision": self.precision,
        }

    @property
    def reference(self) -> str:
        return f"{self.path}:{self.start_line}"


@dataclass
class Symbol:
    id: str = ""
    path: str = ""
    qualified_name: str = ""
    short_name: str = ""
    kind: str = ""          # function | class | method
    start_line: int = 0
    end_line: int = 0
    version: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id, "path": self.path, "qualified_name": self.qualified_name,
            "short_name": self.short_name, "kind": self.kind,
            "start_line": self.start_line, "end_line": self.end_line,
            "version": self.version,
        }


@dataclass
class ImpactResult:
    """What changing one symbol would touch."""

    symbol: Symbol | None = None
    definitions: list[CodeLocation] = field(default_factory=list)
    references: list[CodeLocation] = field(default_factory=list)
    callers: list[CodeLocation] = field(default_factory=list)
    precision: Precision = "structural"
    #: Set when the answer could not be derived from an index, so a caller can fall
    #: back rather than presenting a guess as a finding.
    degraded: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol.as_dict() if self.symbol else None,
            "definitions": [item.as_dict() for item in self.definitions],
            "references": [item.as_dict() for item in self.references],
            "callers": [item.as_dict() for item in self.callers],
            "precision": self.precision, "degraded": self.degraded, "note": self.note,
        }


__all__ = ["CodeLocation", "ImpactResult", "Precision", "Symbol"]
