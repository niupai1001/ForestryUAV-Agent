from __future__ import annotations

import time

from .registry import tool_spec_summary


def enrich_event(event: dict, *, occurred_at: float | None = None) -> dict:
    """Add stable trace metadata without changing existing event fields."""
    enriched = dict(event)
    if enriched.get("type") == "tool_start" and "spec" not in enriched:
        summary = tool_spec_summary(str(enriched.get("name") or ""))
        if summary is not None:
            enriched["spec"] = summary
    if enriched.get("type") == "user_message":
        enriched.setdefault(
            "received_at", float(occurred_at if occurred_at is not None else time.time())
        )
    return enriched


def normalize_event(event: dict, *, seq: int | None = None,
                    created_at: float | None = None) -> dict:
    """Return the additive stable event view consumed by external collectors."""
    normalized = dict(event)
    if seq is not None:
        normalized["seq"] = int(seq)
    if created_at is not None:
        normalized["created_at"] = float(created_at)
    return normalized
