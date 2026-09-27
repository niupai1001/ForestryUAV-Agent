"""Cut a retrieval result down to what a request can afford.

Retrieval is only useful if what it returns fits beside everything else in the
request. A result that overruns the budget is worse than a smaller one: the context
compiler will then silently drop something else, usually the task state, which is the
one thing the model must not lose.

The cut is recorded rather than performed quietly, so a report can tell "retrieval
found nothing" from "retrieval found ten things and we could only afford four".
"""
from __future__ import annotations

from .models import Candidate


def estimate_tokens(text: str) -> int:
    """A cheap upper bound, deliberately not a tokenizer.

    Over-estimating costs a little context; under-estimating costs an overflow mid
    request. Chinese text is denser per character than English, so the divisor is
    lower when CJK dominates.
    """
    if not text:
        return 0
    cjk = sum(1 for char in text if "一" <= char <= "鿿")
    divisor = 2.0 if cjk > len(text) / 3 else 4.0
    return max(1, int(len(text) / divisor))


def apply_budget(candidates: list[Candidate], budget: int) -> tuple[list[Candidate], dict]:
    kept: list[Candidate] = []
    used = 0
    dropped = 0
    for item in candidates:
        cost = estimate_tokens(item.content)
        if used + cost > budget:
            dropped += 1
            continue
        used += cost
        kept.append(item)
    manifest = {
        "token_budget": budget,
        "tokens_used": used,
        "kept": len(kept),
        "dropped_for_budget": dropped,
    }
    return kept, manifest


__all__ = ["apply_budget", "estimate_tokens"]
