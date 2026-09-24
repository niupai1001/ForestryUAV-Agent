"""Bindings for the read-only directory-inventory case."""

from .base import Case, Verifier


CASE = Case(
    id="core.paths",
    track="agent",
    group="core",
    checks={
        "scope": Verifier("evaluation.verify.paths", "directory_scope_is_read_only"),
        "references": Verifier("evaluation.verify.paths", "names_match_references"),
        "answer": Verifier("evaluation.verify.paths", "answer_matches_listing"),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/core_paths",
    gold="evaluation/fixtures/gold/core_paths.json",
)


__all__ = ["CASE"]
