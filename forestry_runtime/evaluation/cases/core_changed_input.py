"""Bindings for the changed-input rerun case."""

from .base import Case, Verifier


CASE = Case(
    id="core.changed_input",
    track="agent",
    group="core",
    checks={
        "fresh_job": Verifier("evaluation.verify.core_repair", "rerun_produced_new_result"),
        "new_result": Verifier("evaluation.verify.core_repair", "summary_matches"),
        "lineage": Verifier("evaluation.verify.core_repair", "source_was_not_modified"),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/core_changed_input",
    gold="evaluation/fixtures/gold/core_changed_input.json",
)


__all__ = ["CASE"]
