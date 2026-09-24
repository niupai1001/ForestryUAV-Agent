"""Bindings for the capability CHM case.

``normal`` supplies a DSM and a DTM that share a grid and a recorded vertical
datum, so ``DSM - DTM`` must be delivered with negative differences preserved.
``gap`` removes the vertical datum from both surfaces: a difference between them is
then not established as a height difference in metres, and the correct outcome is
to refuse and say so.
"""

from .base import Case, Verifier


CASE = Case(
    id="capability.chm",
    track="agent",
    group="capability",
    checks={
        "positive": Verifier("evaluation.verify.chm", "compare_chm_pixels"),
        "claim": Verifier("evaluation.verify.chm", "compare_chm_grid_mask"),
        "negative": Verifier(
            "evaluation.verify.chm", "chm_claims_match_artifact"
        ),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/capability_chm",
    gold="evaluation/fixtures/gold/capability_chm.json",
    fixtures={
        "normal": "evaluation/fixtures/capability_chm",
        "gap": "evaluation/fixtures/capability_chm_gap",
    },
)


__all__ = ["CASE"]
