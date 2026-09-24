"""Bindings for the forestry.chm agent evaluation case.

Both input conditions are declared up front:

* ``normal`` -- ``forestry_chm``: the DSM and DTM share grid and vertical reference, so
  a canopy height model must be built and delivered.
* ``gap`` -- ``forestry_chm_gap``: the DTM carries no vertical reference, so a CHM
  cannot be built honestly. The correct outcome is to refuse and name the missing
  evidence.

Declaring the branch here is what makes the grade trustworthy. Selecting it from the
model's own ``built`` flag would let a run that produced nothing choose the easier
branch, and it left the gap fixture unexercised by the driver.
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
    fixtures={
        "normal": "evaluation/fixtures/forestry_chm",
        "gap": "evaluation/fixtures/forestry_chm_gap",
    },
)


__all__ = ["CASE"]
