"""Bindings for the first end-to-end Agent evaluation case."""

from .base import Case, Verifier


CASE = Case(
    id="core.csv",
    track="agent",
    group="core",
    checks={
        "table": Verifier("evaluation.verify.csv", "compare_by_business_key"),
        "artifact": Verifier(
            "evaluation.verify.provenance", "artifact_links_to_action"
        ),
        "answer": Verifier("evaluation.verify.text", "claims_match_table"),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/core_csv",
    gold="evaluation/fixtures/gold/core_csv.json",
)


__all__ = ["CASE"]
