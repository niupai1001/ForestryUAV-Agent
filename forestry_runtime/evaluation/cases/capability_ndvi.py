"""Bindings for the capability NDVI case.

``normal`` delivers a scene whose band descriptions name Red and NIR, so the index
must be computed and compared pixel by pixel. ``gap`` removes the band descriptions
entirely: the roles cannot be established, and the correct outcome is to refuse and
say what is missing rather than guess a band order.
"""

from .base import Case, Verifier


CASE = Case(
    id="capability.ndvi",
    track="agent",
    group="capability",
    checks={
        "pixels": Verifier("evaluation.verify.ndvi", "compare_ndvi_pixels"),
        "grid_mask": Verifier("evaluation.verify.ndvi", "compare_ndvi_grid_mask"),
        "answer": Verifier(
            "evaluation.verify.ndvi", "reported_facts_match_artifact"
        ),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/capability_ndvi",
    gold="evaluation/fixtures/gold/capability_ndvi.json",
    fixtures={
        "normal": "evaluation/fixtures/capability_ndvi",
        "gap": "evaluation/fixtures/capability_ndvi_gap",
    },
)


__all__ = ["CASE"]
