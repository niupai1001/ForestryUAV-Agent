"""Bindings for the diagnose-and-repair case."""

from .base import Case, Verifier


CASE = Case(
    id="core.repair",
    track="agent",
    group="core",
    checks={
        "failure_observed": Verifier("evaluation.verify.core_repair", "failure_was_observed"),
        "repair": Verifier("evaluation.verify.core_repair", "summary_matches"),
        "delivery": Verifier("evaluation.verify.text", "claims_match_table"),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/core_repair",
    gold="evaluation/fixtures/gold/core_repair.json",
)


__all__ = ["CASE"]
