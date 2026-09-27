"""One retrieval contract across tools, memory, knowledge, code and failures.

Before this, each of those was reached a different way -- memory by injecting the
most recent twenty records, tools by substring against a hand-written keyword list,
knowledge only by calling a tool, failures not at all -- so none of them could be
composed, budgeted, or measured. The contract is unified here; storage is not, and
should not be: SQLite FTS5, ripgrep and an in-memory index have nothing in common.

The order a query flows through is fixed and is the substance of the design:

    exact / structured facts  ->  lexical recall  ->  RRF fusion
        ->  diversity  ->  token budget  ->  agent context

A known fact is not asked to compete with a plausible text match, and no single source
is allowed to spend the whole budget.
"""
from __future__ import annotations

from .budget import apply_budget, estimate_tokens
from .fabric import CODE_ENV_FLAG, ENV_FLAG, build, enabled, retrieve
from .fusion import diversify, rrf_fuse
from .models import SOURCE_TYPES, Candidate, RetrievalQuery, RetrievalResult, render
from .retrievers import (
    FailureRetriever, MemoryRetriever, ToolRetriever, score_text, terms_of,
)
from .router import RetrievalRouter

__all__ = [
    "CODE_ENV_FLAG", "ENV_FLAG", "SOURCE_TYPES", "Candidate", "FailureRetriever",
    "MemoryRetriever", "RetrievalQuery", "RetrievalResult", "RetrievalRouter",
    "ToolRetriever", "apply_budget", "build", "diversify", "enabled",
    "estimate_tokens", "render", "retrieve", "rrf_fuse", "score_text", "terms_of",
]
