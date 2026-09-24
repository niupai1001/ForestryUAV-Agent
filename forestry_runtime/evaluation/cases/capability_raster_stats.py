"""Bindings for the capability raster-alignment and zonal-statistics case."""

from .base import Case, Verifier


CASE = Case(
    id="capability.raster_stats",
    track="agent",
    group="capability",
    checks={
        "zonal": Verifier(
            "evaluation.verify.capability_raster", "zonal_statistics_match"
        ),
        "mask": Verifier(
            "evaluation.verify.capability_raster", "mask_and_denominator"
        ),
        "area": Verifier(
            "evaluation.verify.capability_raster", "area_is_derived_or_withheld"
        ),
    },
    expected_terminal="completed",
    fixture="evaluation/fixtures/capability_raster_stats",
    gold="evaluation/fixtures/gold/capability_raster_stats.json",
    fixtures={
        "normal": "evaluation/fixtures/capability_raster_stats",
        "gap": "evaluation/fixtures/capability_raster_stats_gap",
    },
)


__all__ = ["CASE"]
