"""One retrieval call, whatever is being looked for.

``matching_domain_tools`` used to be the only routing, and it answered exactly one
question -- "which domain group does this substring belong to" -- while memory,
failures and knowledge had no way in at all. The router replaces that with one query
that can ask several sources at once, fuse them, and return a result that fits a
budget.

Order matters and is fixed: exact/structured facts first, then lexical recall, then
fusion, then diversity, then the budget. Letting an embedding rank a known fact is how
a Run ends up re-deriving something it has already proven false.
"""
from __future__ import annotations

from .budget import apply_budget
from .fusion import diversify, rrf_fuse
from .models import RetrievalQuery, RetrievalResult, SOURCE_TYPES


class RetrievalRouter:
    def __init__(self, retrievers: dict | None = None):
        self.retrievers = dict(retrievers or {})

    def register(self, source_type: str, retriever) -> None:
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"unknown source type: {source_type!r}")
        self.retrievers[source_type] = retriever

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        wanted = tuple(query.source_types) or tuple(self.retrievers)
        ranked_lists = []
        per_source = {}
        for source_type in wanted:
            retriever = self.retrievers.get(source_type)
            if retriever is None:
                continue
            try:
                found = retriever.retrieve(query.text, top_k=query.candidate_k)
            except Exception:
                # One broken source must not empty the result. A retrieval that raises
                # is invisible to the model, which would read it as "nothing relevant
                # exists" and act on that.
                per_source[source_type] = {"error": True, "count": 0}
                continue
            per_source[source_type] = {"count": len(found)}
            if found:
                ranked_lists.append(found)

        fused = rrf_fuse(ranked_lists)
        fused = self._in_scope(fused, query)
        fused = diversify(fused)
        kept, budget_manifest = apply_budget(fused, query.token_budget)
        return RetrievalResult(
            query=query,
            candidates=kept[:query.top_k],
            manifest={
                "sources_asked": list(wanted),
                "per_source": per_source,
                "fused_candidates": len(fused),
                **budget_manifest,
            },
        )

    @staticmethod
    def _in_scope(candidates, query: RetrievalQuery):
        """Keep cross-project leakage out.

        A memory or knowledge hit from another project is worse than no hit: it looks
        like evidence and is not. Scope is checked on the way out as well as in, so a
        retriever that forgets to filter cannot leak.
        """
        if not query.resource_scope and not query.project_id:
            return candidates
        scope = {str(item).casefold() for item in query.resource_scope}
        kept = []
        for item in candidates:
            project = str(item.metadata.get("project_id") or "").casefold()
            if query.project_id and project and project != str(query.project_id).casefold():
                continue
            if scope:
                reference = " ".join(str(value) for value in item.exact_reference.values())
                if not any(token in reference.casefold() for token in scope):
                    continue
            kept.append(item)
        return kept


__all__ = ["RetrievalRouter"]
