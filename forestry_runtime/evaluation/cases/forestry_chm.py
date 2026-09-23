"""Bindings for the forestry.chm agent evaluation case.

The fixture is the positive half (a DSM and a DTM that share grid and vertical
reference). The negative half lives in ``forestry_chm_gap`` and is exercised by
the same contract through a variant trial, so both are registered side by side.
"""

from .base import Case, Verifier


CASE = Case(
    id="forestry.chm",
    track="agent",
    group="forestry",
    checks={
        "positive": Verifier("evaluation.verify.chm", "compare_chm_pixels"),
        "negative": Verifier("evaluation.verify.chm", "chm_claims_match_artifact"),
        "claim": Verifier("evaluation.verify.chm", "compare_chm_grid_mask"),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/forestry_chm",
    gold="evaluation/fixtures/gold/forestry_chm.json",
)


__all__ = ["CASE"]
