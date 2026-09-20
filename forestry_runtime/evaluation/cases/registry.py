"""Implemented evaluation cases and verifier entry points."""

from .core_csv import CASE as CORE_CSV


CASES = {case.id: case for case in (CORE_CSV,)}

GATE_VERIFIERS = {
    "gate.idempotency": ("evaluation.verify.idempotency_trial", "verify_trial"),
    "gate.permissions": ("evaluation.verify.permissions_trial", "verify_trial"),
}

# One browser collection produces independent records for both contracts.
BROWSER_CASES = {"ui.send", "ui.reconnect"}


__all__ = ["BROWSER_CASES", "CASES", "GATE_VERIFIERS"]
