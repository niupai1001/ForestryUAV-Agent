"""The shapes every retriever returns, so retrieval can have one contract.

The Runtime grew its retrieval one caller at a time: memory injected its most recent
twenty records, tools were pre-selected by substring against a hand-written keyword
list, knowledge was reachable only by calling a tool, and failures were not reachable
at all. Each of those answered a different question in a different shape, so none of
them could be composed, budgeted, or measured.

Everything here is a ``Candidate``: a source type, content, and an exact reference
back to where it came from. Storage is deliberately *not* unified -- SQLite FTS5,
ripgrep and an in-memory index have nothing in common -- only the result contract is.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SOURCE_TYPES = ("tool", "code", "memory", "knowledge", "artifact", "failure", "task_state")


@dataclass
class RetrievalQuery:
    text: str = ""
    source_types: tuple[str, ...] = ()
    project_id: str | None = None
    resource_scope: list[str] = field(default_factory=list)
    top_k: int = 8
    candidate_k: int = 40
    token_budget: int = 6000


@dataclass
class Candidate:
    id: str = ""
    source_type: str = ""
    content: str = ""
    #: Where this came from, precisely enough to fetch or cite it. A candidate that
    #: cannot be traced back is a rumour, and the model has no way to check one.
    exact_reference: dict = field(default_factory=dict)
    version: str | None = None
    lexical_rank: int | None = None
    dense_rank: int | None = None
    structural_score: float | None = None
    fusion_score: float = 0.0
    metadata: dict = field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, str]:
        return (self.source_type, self.id)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "source_type": self.source_type, "content": self.content,
            "exact_reference": dict(self.exact_reference), "version": self.version,
            "lexical_rank": self.lexical_rank, "dense_rank": self.dense_rank,
            "structural_score": self.structural_score,
            "fusion_score": round(self.fusion_score, 6),
            "metadata": dict(self.metadata),
        }


@dataclass
class RetrievalResult:
    query: RetrievalQuery = field(default_factory=RetrievalQuery)
    candidates: list[Candidate] = field(default_factory=list)
    #: What was asked for, what each source contributed, and what the budget dropped.
    #: Recorded because a retrieval that silently returns less than was asked for is
    #: indistinguishable, from the model's side, from a retrieval that found nothing.
    manifest: dict = field(default_factory=dict)

    @property
    def ids(self) -> list[str]:
        return [item.id for item in self.candidates]

    def by_source(self, source_type: str) -> list[Candidate]:
        return [item for item in self.candidates if item.source_type == source_type]

    def as_dict(self) -> dict:
        return {
            "query": {"text": self.query.text, "source_types": list(self.query.source_types)},
            "candidates": [item.as_dict() for item in self.candidates],
            "manifest": dict(self.manifest),
        }


def render(result: RetrievalResult, max_content: int = 400) -> str:
    """A bounded rendering for a model request."""
    if not result.candidates:
        return ""
    lines = []
    for item in result.candidates:
        content = item.content.strip().replace("\n", " ")
        if len(content) > max_content:
            content = content[:max_content] + "…"
        reference = item.exact_reference.get("path") or item.exact_reference.get("id") or ""
        lines.append(f"- [{item.source_type}] {item.id}"
                     + (f" ({reference})" if reference else "") + f": {content}")
    return "\n".join(lines)


__all__ = [
    "SOURCE_TYPES", "Candidate", "RetrievalQuery", "RetrievalResult", "render",
]
