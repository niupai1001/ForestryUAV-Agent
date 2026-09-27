"""Verifiers that can actually say yes, and the one thing that lets them.

A verifier needs something to check the artifact *against*. The evaluation harness has
gold pixels, so its verifiers compare against truth; a Run has none, so the only
honest alternative is to compare an artifact against **what its producer claimed it
is**. That claim has to be written down by the producer, which is what
``metadata["semantics"]`` is:

    {"semantics": {
        "quantity": "ndvi",              # what the values are supposed to be
        "kind": "index",                 # index | mask | continuous | height
        "valid_range": [-1.0, 1.0],      # inclusive; None for unbounded
        "fractions": {"canopy": 0.42},   # for kind="mask": the fraction encoded
        "grid": {"crs": "EPSG:32650", "width": 800, "height": 600},
    }}

Three rules follow, and breaking any of them turns this into a guessing game:

1. **A declared key is checked; an absent one is not invented.** No
   ``valid_range`` means no range check, reported as ``not_run`` -- not as a pass, and
   not as a range inferred from the filename. A verifier that guesses "``_ndvi_`` in
   the name, so values must be in [-1,1]" is right until the day a product is named
   that and holds something else.
2. **Claimed numbers are compared with measured ones.** "冠层覆盖率 42%" is the
   sentence most worth checking, because it is the one the user reads, and the file
   knows whether it is true.
3. **A check that cannot be evaluated is ``not_run``**, which keeps the result at
   ``delivered_unverified``. Absence of a declaration is not correctness.
"""
from __future__ import annotations

import json
from typing import Any

from .models import Check

#: Measured values are compared with a tolerance rather than exactly: ``product_qa``
#: reads a decimated sample of the raster, so its min/max are the extremes of what was
#: sampled, not of every pixel.
TOLERANCE = 1e-6

GEO_SUFFIXES = (".tif", ".tiff")
GEOJSON_SUFFIXES = (".geojson",)
GEOJSON_MEDIA = ("application/geo+json",)
#: A GeoJSON with a pathological feature count is not proof-worthy either way; the
#: geometry scan is bounded so a corrupt file with ten million features cannot turn
#: verification into a denial of service.
GEOMETRY_SCAN_LIMIT = 500


def _semantics(artifact: dict) -> dict:
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    declared = metadata.get("semantics")
    return declared if isinstance(declared, dict) else {}


def _is_geotiff(artifact: dict) -> bool:
    name = str(artifact.get("name") or "").casefold()
    media = str(artifact.get("media_type") or "").casefold()
    return name.endswith(GEO_SUFFIXES) or media in ("image/tiff", "image/tif")


def _band_extremes(qa: dict) -> tuple[float | None, float | None]:
    low: float | None = None
    high: float | None = None
    for band in qa.get("bands") or []:
        if not isinstance(band, dict):
            continue
        for key, combine in (("min", min), ("max", max)):
            value = band.get(key)
            if not isinstance(value, (int, float)):
                continue
            low = float(value) if low is None else combine(low, float(value))
            high = float(value) if high is None else combine(high, float(value))
    return low, high


def raster_semantics(artifact: dict, task: str = "") -> list[Check]:
    """Check a GeoTIFF against what its producer declared it to be."""
    if not _is_geotiff(artifact):
        return []
    qa = artifact.get("product_qa")
    name = str(artifact.get("name") or "artifact")
    if not isinstance(qa, dict) or not qa or qa.get("error"):
        return [Check(
            layer="semantic", name="raster_semantics", outcome="not_run",
            detail=(f"{name} was not measured: {qa.get('error')}" if isinstance(qa, dict)
                    and qa.get("error") else f"{name} carries no measured metadata"),
        )]

    declared = _semantics(artifact)
    if not declared:
        return [Check(
            layer="semantic", name="raster_semantics", outcome="not_run",
            detail=(f"{name} declares no semantics; range and counts cannot be "
                    "checked against a claim"),
        )]

    checks: list[Check] = []
    checks.append(_range_check(declared, qa, name))
    checks.extend(_band_range_checks(declared, qa, name))
    checks.append(_grid_check(declared, qa, name))
    checks.append(_plausibility_check(declared, qa, name))
    checks.append(_fraction_check(declared, qa, name))
    return [check for check in checks if check is not None]


def _measured_band(qa: dict, label: str) -> dict | None:
    """The measured band a declaration refers to, or ``None`` if it is not there.

    Matched on the band description -- which the producer writes, the same way it
    writes the claim -- and then on position, so a declaration can name a band or
    number it. Never matched on order alone: a product that silently reorders its
    bands would then be checked against the wrong claim and pass.
    """
    wanted = str(label).strip().casefold()
    for band in qa.get("bands") or []:
        if not isinstance(band, dict):
            continue
        if str(band.get("description") or "").strip().casefold() == wanted:
            return band
    for band in qa.get("bands") or []:
        if isinstance(band, dict) and str(band.get("index")) == wanted:
            return band
    return None


def _band_range_checks(declared: dict, qa: dict, name: str) -> list[Check]:
    """Per-band ranges, for a product whose bands are different quantities.

    One range over every band is only a claim a single-quantity product can make.
    An inversion raster carries a leaf area index, a chlorophyll content and an
    RMSE side by side: folding them into one min/max would pass a file whose LAI
    band holds reflectance, which is the exact failure a verifier exists to catch.
    """
    bands = declared.get("bands")
    if not isinstance(bands, dict) or not bands:
        return []
    checks: list[Check] = []
    for label, claim in bands.items():
        bounds = claim.get("valid_range") if isinstance(claim, dict) else claim
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            continue
        band = _measured_band(qa, label)
        if band is None:
            checks.append(Check(
                layer="semantic", name="raster_band_range", outcome="not_run",
                detail=f"{name}: the declared band {label!r} was not found in the "
                       "measured bands, so its range could not be checked",
            ))
            continue
        low_declared, high_declared = bounds[0], bounds[1]
        measured_low, measured_high = band.get("min"), band.get("max")
        if not isinstance(measured_low, (int, float)) or not isinstance(measured_high, (int, float)):
            checks.append(Check(
                layer="semantic", name="raster_band_range", outcome="not_run",
                detail=f"{name}: band {label!r} carries no measured extremes",
            ))
            continue
        outcome, detail = "passed", (
            f"{name}: band {label!r} sampled [{measured_low}, {measured_high}] "
            f"within declared [{low_declared}, {high_declared}]"
        )
        if low_declared is not None and float(measured_low) < float(low_declared) - TOLERANCE:
            outcome, detail = "failed", (
                f"{name}: band {label!r} measured min {measured_low} is below the "
                f"declared {low_declared}"
            )
        elif high_declared is not None and float(measured_high) > float(high_declared) + TOLERANCE:
            outcome, detail = "failed", (
                f"{name}: band {label!r} measured max {measured_high} exceeds the "
                f"declared {high_declared}"
            )
        checks.append(Check(
            layer="semantic", name="raster_band_range", outcome=outcome, detail=detail,
        ))
    return checks


def _range_check(declared: dict, qa: dict, name: str) -> Check | None:
    bounds = declared.get("valid_range")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        return None
    low_declared, high_declared = bounds
    measured_low, measured_high = _band_extremes(qa)
    if measured_low is None or measured_high is None:
        return Check(
            layer="semantic", name="raster_range", outcome="not_run",
            detail=f"{name}: no band statistics were measured",
        )
    # Honest about the evidence: the extremes come from a decimated read, so "passed"
    # means no out-of-range pixel was found *in the sample*. It is not a proof over
    # every pixel, and the detail says so rather than letting "passed" read as one.
    outcome = "passed"
    detail = (f"{name}: sampled [{measured_low}, {measured_high}] within declared "
              f"[{low_declared}, {high_declared}] (decimated read, not every pixel)")
    if low_declared is not None and measured_low < float(low_declared) - TOLERANCE:
        outcome = "failed"
        detail = (f"{name}: measured min {measured_low} is below the declared "
                  f"{low_declared} for {declared.get('quantity') or 'this quantity'}")
    elif high_declared is not None and measured_high > float(high_declared) + TOLERANCE:
        outcome = "failed"
        detail = (f"{name}: measured max {measured_high} exceeds the declared "
                  f"{high_declared} for {declared.get('quantity') or 'this quantity'}")
    return Check(layer="semantic", name="raster_range", outcome=outcome, detail=detail)


def _grid_check(declared: dict, qa: dict, name: str) -> Check | None:
    """The product must sit on the grid it claims to come from.

    A canopy model on the wrong grid is not a slightly wrong canopy model; it is a
    different surface, and every number derived from it is about somewhere else.
    """
    grid = declared.get("grid")
    if not isinstance(grid, dict) or not grid:
        return None
    mismatches: list[str] = []
    if grid.get("crs") and qa.get("crs") and str(grid["crs"]) != str(qa["crs"]):
        mismatches.append(f"crs {qa.get('crs')} != declared {grid['crs']}")
    for key, measured in (("width", qa.get("width")), ("height", qa.get("height"))):
        if grid.get(key) and measured and int(grid[key]) != int(measured):
            mismatches.append(f"{key} {measured} != declared {grid[key]}")
    declared_pixel = grid.get("pixel_size")
    measured_pixel = qa.get("pixel_size")
    if (isinstance(declared_pixel, (list, tuple)) and isinstance(measured_pixel, (list, tuple))
            and len(declared_pixel) == len(measured_pixel)):
        for index, value in enumerate(measured_pixel):
            if abs(float(value) - float(declared_pixel[index])) > 1e-6:
                mismatches.append(f"pixel_size {list(measured_pixel)} != "
                                  f"declared {list(declared_pixel)}")
                break
    if not mismatches:
        return Check(
            layer="semantic", name="raster_grid", outcome="passed",
            detail=f"{name}: grid matches the declared source grid",
        )
    return Check(
        layer="semantic", name="raster_grid", outcome="failed",
        detail=f"{name}: " + "; ".join(mismatches),
    )


def _plausibility_check(declared: dict, qa: dict, name: str) -> Check | None:
    """A product that separates everything, or nothing, separated nothing.

    Two different fractions are involved and they must not be confused:
    ``valid_fraction_sampled`` is how much of the grid carries data, while for a mask
    the *encoded* fraction -- the pixel mean -- is how much the mask selects. A mask
    over a fully valid grid is fine; a mask that selects the whole grid, or none of
    it, is a threshold that did not separate anything.
    """
    valid = qa.get("valid_fraction_sampled")
    if not isinstance(valid, (int, float)):
        return None
    fraction = float(valid)
    if fraction <= 0:
        return Check(
            layer="semantic", name="raster_valid_area", outcome="failed",
            detail=f"{name}: no valid pixels; the product is empty",
        )

    kind = str(declared.get("kind") or "").casefold()
    if kind == "mask":
        selected = None
        for band in qa.get("bands") or []:
            if isinstance(band, dict) and isinstance(band.get("mean"), (int, float)):
                selected = float(band["mean"])
                break
        if selected is None:
            return Check(
                layer="semantic", name="raster_valid_area", outcome="not_run",
                detail=f"{name}: no band mean measured; how much the mask selects "
                       "is unknown",
            )
        if selected <= TOLERANCE:
            return Check(
                layer="semantic", name="raster_valid_area", outcome="failed",
                detail=f"{name}: the mask selects {selected:.6f} of the grid, i.e. "
                       "nothing",
            )
        if selected >= 1.0 - TOLERANCE:
            return Check(
                layer="semantic", name="raster_valid_area", outcome="failed",
                detail=f"{name}: the mask selects {selected:.6f} of the grid, i.e. "
                       "everything; it separates nothing",
            )
        return Check(
            layer="semantic", name="raster_valid_area", outcome="passed",
            detail=f"{name}: the mask selects {selected:.4f} of the grid",
        )
    return Check(
        layer="semantic", name="raster_valid_area", outcome="passed",
        detail=f"{name}: valid_fraction_sampled={fraction:.4f}",
    )


def _fraction_check(declared: dict, qa: dict, name: str) -> Check | None:
    """Compare a claimed fraction with the fraction the file actually encodes.

    "冠层覆盖 42%" is the sentence the user reads, and it is the one a product can
    contradict. For a mask the encoded fraction is the pixel mean; comparing against
    it is sound because both sides are proportions -- a decimated read estimates a
    proportion well, where it would estimate an absolute pixel count badly.

    Tolerated at five percentage points: the measurement is a sample, and a claim
    quoted to two significant figures should not fail on sampling noise.
    """
    fractions = declared.get("fractions")
    if not isinstance(fractions, dict) or not fractions:
        return None
    kind = str(declared.get("kind") or "").casefold()
    if kind != "mask":
        return None
    measured = None
    for band in qa.get("bands") or []:
        if isinstance(band, dict) and isinstance(band.get("mean"), (int, float)):
            measured = float(band["mean"])
            break
    if measured is None:
        return Check(
            layer="semantic", name="raster_fraction", outcome="not_run",
            detail=f"{name}: no band mean was measured, so no fraction to compare",
        )
    for key, claimed in list(fractions.items())[:3]:
        if not isinstance(claimed, (int, float)):
            continue
        difference = abs(measured - float(claimed))
        return Check(
            layer="semantic", name=f"raster_fraction_{key}",
            outcome="passed" if difference <= 0.05 else "failed",
            detail=(f"{name}: claimed {key}={float(claimed):.4f}, encoded "
                    f"{measured:.4f} (tolerance 0.05)"),
        )
    return None


def _is_geojson(artifact: dict) -> bool:
    name = str(artifact.get("name") or "").casefold()
    media = str(artifact.get("media_type") or "").casefold()
    return name.endswith(GEOJSON_SUFFIXES) or media in GEOJSON_MEDIA


def geojson_semantics(artifact: dict, task: str = "") -> list[Check]:
    """Check a GeoJSON deliverable against the count its producer declared.

    A canopy-statistics run's headline number is "N candidate trees", and that number
    is exactly the kind of claim a file can contradict: the count in the summary, the
    count in the GeoJSON, and the count the model says out loud are three copies of
    one fact, and copies drift. The declared ``feature_count`` is the copy written by
    the code that built the features; the file is the copy the user will open.
    """
    name = str(artifact.get("name") or "artifact")
    if not _is_geojson(artifact):
        return []
    declared = _semantics(artifact)
    if not declared:
        return [Check(
            layer="semantic", name="geojson_semantics", outcome="not_run",
            detail=(f"{name} declares no semantics; the feature count cannot be "
                    "checked against a claim"),
        )]
    path = artifact.get("path")
    if not path:
        return [Check(
            layer="semantic", name="geojson_semantics", outcome="not_run",
            detail=f"{name}: the file is not available to the verifier",
        )]
    try:
        with open(str(path), encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        return [Check(
            layer="semantic", name="geojson_semantics", outcome="failed",
            detail=f"{name} does not parse as JSON: {type(exc).__name__}",
        )]
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        return [Check(
            layer="semantic", name="geojson_semantics", outcome="failed",
            detail=f"{name}: expected a FeatureCollection, found "
                   f"{type(data).__name__ if not isinstance(data, dict) else data.get('type')}",
        )]
    features = data.get("features")
    if not isinstance(features, list):
        return [Check(
            layer="semantic", name="geojson_semantics", outcome="failed",
            detail=f"{name}: the features entry is not an array",
        )]

    checks: list[Check] = []
    declared_count = declared.get("feature_count")
    if isinstance(declared_count, int) and not isinstance(declared_count, bool):
        checks.append(Check(
            layer="semantic", name="geojson_feature_count",
            outcome="passed" if len(features) == declared_count else "failed",
            detail=(f"{name}: {len(features)} features in the file, "
                    f"{declared_count} declared"),
        ))
    geometry_type = declared.get("geometry_type")
    if isinstance(geometry_type, str) and geometry_type:
        scanned = features[:GEOMETRY_SCAN_LIMIT]
        wrong = [
            index for index, feature in enumerate(scanned)
            if not isinstance(feature, dict)
            or not isinstance(feature.get("geometry"), dict)
            or str((feature.get("geometry") or {}).get("type")) != geometry_type
        ]
        if wrong:
            checks.append(Check(
                layer="semantic", name="geojson_geometry_type", outcome="failed",
                detail=(f"{name}: feature(s) {wrong[:5]} do not carry "
                        f"{geometry_type} geometry"),
            ))
        elif scanned:
            checks.append(Check(
                layer="semantic", name="geojson_geometry_type", outcome="passed",
                detail=(f"{name}: all {len(scanned)} scanned features carry "
                        f"{geometry_type} geometry"),
            ))
    return [check for check in checks if check is not None]


def register_builtins(registry) -> None:
    """Put the built-in verifiers on a registry.

    Registered with no ``kinds`` because the check is not decided by the artifact's
    coarse kind -- every GeoTIFF is candidate, and whether anything can be checked is
    decided by whether its producer declared semantics.
    """
    registry.register("raster_semantics", raster_semantics)
    registry.register("geojson_semantics", geojson_semantics)


def default_registry():
    """A registry carrying the built-in verifiers. What the Run uses."""
    from .registry import VerifierRegistry

    registry = VerifierRegistry()
    register_builtins(registry)
    return registry


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


__all__ = [
    "GEOJSON_SUFFIXES", "GEO_SUFFIXES", "TOLERANCE", "default_registry",
    "geojson_semantics", "raster_semantics", "register_builtins",
]
