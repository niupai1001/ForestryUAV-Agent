"""Assemble the retrieval sources one Run actually has.

Built from whatever the toolbox carries rather than from a global: a fabric that
reaches for a singleton is untestable and, worse, quietly serves one project's
knowledge to another. Every source is optional, and a source that is missing or
broken leaves the rest of the result intact -- a Run with no memory still has its
tools and its failures.

The switch is ``RETRIEVAL_FABRIC_ENABLED``, and it is off by default. That is a
measurement decision, not a code-readiness one: the current evaluation baseline
(grounded-v1.2 and the controlled A/B experiment) was produced without retrieved
evidence in the request, and turning it on mid-measurement would confound the
comparison the baseline exists to make.
"""
from __future__ import annotations

import os

from .models import RetrievalQuery
from .retrievers import FailureRetriever, MemoryRetriever, ToolRetriever
from .router import RetrievalRouter

ENV_FLAG = "RETRIEVAL_FABRIC_ENABLED"
CODE_ENV_FLAG = "RETRIEVAL_CODE_ENABLED"


def enabled() -> bool:
    return os.getenv(ENV_FLAG, "false").strip().lower() == "true"


def build(toolbox, *, code=None, memory=None, failures=None, tools=None) -> RetrievalRouter:
    """One router over the sources this Run can reach.

    ``code`` is accepted rather than constructed because indexing a workspace is not
    free; the caller decides whether this Run wants to pay for it.
    """
    router = RetrievalRouter()
    router.register("tool", tools if tools is not None else ToolRetriever())

    store = failures
    if store is None:
        preflight = getattr(toolbox, "failure_preflight", None)
        store = getattr(preflight, "store", None)
    if store is not None:
        router.register("failure", FailureRetriever(store))

    if memory is not None:
        router.register("memory", memory)
    elif getattr(toolbox, "memory", None) is not None:
        manager = toolbox.memory
        owner = getattr(toolbox, "owner", "")
        chat_id = getattr(toolbox, "chat_id", "")

        def project_of() -> str | None:
            try:
                selected = manager.selected_project(owner, chat_id) or {}
            except Exception:
                return None
            return selected.get("project_id")

        def recall(query: str, limit: int = 5) -> dict:
            """Confirmed memory bearing on this query."""
            project_id = project_of()
            if not project_id:
                return {"mode": "none", "results": []}
            return {"mode": "memory",
                    "results": manager.recall_memories(owner, project_id, query, limit)}

        def search(query: str, limit: int = 6) -> dict:
            """Knowledge indexed for this project."""
            project_id = project_of()
            if not project_id:
                return {"mode": "none", "results": []}
            return manager.search(owner, project_id, query, limit)

        router.register("memory", MemoryRetriever(recall, "memory"))
        router.register("knowledge", MemoryRetriever(search, "knowledge"))

    if code is not None:
        router.register("code", code)
    elif os.getenv(CODE_ENV_FLAG, "false").strip().lower() == "true":
        try:
            from ..code_intelligence import CodeIntelligenceService
            router.register("code", CodeIntelligenceService(str(toolbox.workspace)))
        except Exception:
            pass

    return router


def retrieve(router: RetrievalRouter, text: str, *, project_id: str | None = None,
             top_k: int = 8, token_budget: int = 4000) -> object:
    """One query through the fabric, in the shape the compiler expects."""
    return router.retrieve(RetrievalQuery(
        text=text, project_id=project_id, top_k=top_k, token_budget=token_budget,
    ))


__all__ = ["CODE_ENV_FLAG", "ENV_FLAG", "build", "enabled", "retrieve"]
