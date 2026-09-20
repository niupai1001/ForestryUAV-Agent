"""Bindings for the forestry.product_qa agent evaluation case.

This case inspects an existing product rather than producing one, so it can run
through the shared attachment collection path: the prompt does not mandate a tool,
and ``inspect_file``/``inspect_raster`` can report the required metadata from an
uploaded GeoTIFF without a host-directory grant.
"""

from .base import Case, Verifier


CASE = Case(
    id="forestry.product_qa",
    track="agent",
    group="forestry",
    checks={
        "metadata": Verifier("evaluation.verify.product_qa", "metadata_matches_artifact"),
        "boundaries": Verifier("evaluation.verify.product_qa", "boundaries_respected"),
        "evidence": Verifier("evaluation.verify.product_qa", "evidence_is_traceable"),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/forestry_product_qa",
    gold="evaluation/fixtures/gold/forestry_product_qa.json",
)


__all__ = ["CASE"]
