"""Combine ranked lists from different backends without pretending they are alike.

Reciprocal rank fusion is used because the backends are not comparable: a ripgrep hit,
a BM25 row and an exact failure lookup produce scores on different scales, and
normalising them would require believing those numbers mean the same thing. Rank
fusion only assumes "being first in a list is good", which is true of all of them.

Exact and structured findings are given a structural score rather than being fused
blindly: a fact that is *known* -- this path does not exist, this record is the one --
should not have to compete on rank with a plausible text match.
"""
from __future__ import annotations

from .models import Candidate

#: The standard RRF constant. Larger values flatten the difference between top ranks;
#: 60 is the value the original evaluation settled on and behaves well here.
RRF_K = 60


def rrf_fuse(ranked_lists: list[list[Candidate]], *, k: int = RRF_K) -> list[Candidate]:
    fused: dict[tuple[str, str], Candidate] = {}
    for ranked in ranked_lists:
        for position, candidate in enumerate(ranked, start=1):
            key = candidate.identity
            existing = fused.get(key)
            if existing is None:
                merged = Candidate(
                    id=candidate.id, source_type=candidate.source_type,
                    content=candidate.content,
                    exact_reference=dict(candidate.exact_reference),
                    version=candidate.version, metadata=dict(candidate.metadata),
                )
                fused[key] = merged
                existing = merged
            existing.fusion_score += 1.0 / (k + position)
            if candidate.lexical_rank is not None and existing.lexical_rank is None:
                existing.lexical_rank = candidate.lexical_rank
            if candidate.dense_rank is not None and existing.dense_rank is None:
                existing.dense_rank = candidate.dense_rank
            if candidate.structural_score is not None:
                existing.structural_score = max(
                    existing.structural_score or 0.0, candidate.structural_score,
                )
    return sorted(
        fused.values(),
        key=lambda item: (item.structural_score or 0.0, item.fusion_score),
        reverse=True,
    )


def diversify(candidates: list[Candidate], per_source: int = 3) -> list[Candidate]:
    """Keep any one source from filling the whole budget.

    Without this, a source that matches everything -- the knowledge index on a broad
    query -- crowds out the tool and failure facts that would actually change what the
    model does next.
    """
    counts: dict[str, int] = {}
    kept: list[Candidate] = []
    for item in candidates:
        seen = counts.get(item.source_type, 0)
        if seen >= per_source:
            continue
        counts[item.source_type] = seen + 1
        kept.append(item)
    return kept


__all__ = ["RRF_K", "diversify", "rrf_fuse"]
