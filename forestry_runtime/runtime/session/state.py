"""Authoritative Run state-machine contract."""

from __future__ import annotations

from ..storage import AssetError


TERMINAL = {"completed", "failed", "canceled", "cancel_incomplete"}
ACTIVE = {"queued", "running", "waiting", "canceling"}
PAUSABLE = {"queued", "running"}

TRANSITIONS = {
    "queued": {"running", "failed", "canceling", "canceled"},
    "running": {"waiting", "paused", "completed", "failed", "canceling", "canceled"},
    "waiting": {"running", "paused", "canceled", "canceling"},
    "paused": {"queued", "canceling"},
    "canceling": {"canceled", "cancel_incomplete", "running"},
}


def transition(current: str, target: str) -> None:
    if current == target:
        return
    if current in TERMINAL:
        raise AssetError(f"{current} is terminal and cannot change to {target}")
    allowed = TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise AssetError(f"Illegal transition {current} -> {target}")


__all__ = ["ACTIVE", "PAUSABLE", "TERMINAL", "TRANSITIONS", "transition"]
