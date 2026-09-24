"""Bindings for the read-only UAV flight-archive inventory case."""

from .base import Case, Verifier


CASE = Case(
    id="forestry.inventory",
    track="agent",
    group="forestry",
    checks={
        "roles": Verifier("evaluation.verify.inventory", "roles_match_manifest"),
        "answer": Verifier("evaluation.verify.inventory", "answer_matches_manifest"),
        "read_only": Verifier("evaluation.verify.inventory", "stayed_read_only"),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/forestry_inventory",
    gold="evaluation/fixtures/gold/forestry_inventory.json",
)


__all__ = ["CASE"]
