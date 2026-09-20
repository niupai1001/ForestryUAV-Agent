"""Bindings for the forestry.ndvi agent evaluation case."""

from .base import Case, Verifier


CASE = Case(
    id="forestry.ndvi",
    track="agent",
    group="forestry",
    checks={
        "pixels": Verifier("evaluation.verify.ndvi", "compare_ndvi_pixels"),
        "grid_mask": Verifier("evaluation.verify.ndvi", "compare_ndvi_grid_mask"),
        "answer": Verifier(
            "evaluation.verify.ndvi", "reported_facts_match_artifact"
        ),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/forestry_ndvi",
    gold="evaluation/fixtures/gold/forestry_ndvi.json",
)


__all__ = ["CASE"]
