"""Independent CHM verification: positive surface, negative case, and claims.

The positive half recomputes ``DSM - DTM`` from the frozen source surfaces and
compares the delivered raster pixel by pixel, requiring the input grid to be
inherited, invalid pixels to stay masked, and negative height differences to
survive instead of being clipped to zero.

The negative half is the contract's ``negative`` check: when the DTM declares no
vertical reference, no evidence-backed CHM exists, so delivering a raster is a
failure and reporting the specific gap is a pass. ``FRAMEWORK.md`` §5 states
plainly that "correctly reporting missing data" can satisfy a task contract, so
the verifier must not treat abstention as a failure.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from .base import Verdict


PIXELS_VERIFIER = "chm-pixels-v1"
GRID_VERIFIER = "chm-grid-mask-v1"
CLAIM_VERIFIER = "chm-claim-v1"

FACT_FIELDS = (
    "valid_pixel_count", "negative_height_pixel_count", "maximum_height_m",
    "crs", "vertical_reference",
)


def _load(raster: Path) -> tuple[np.ndarray, dict[str, Any]]:
    with rasterio.open(raster) as dataset:
        profile = {
            "crs": str(dataset.crs) if dataset.crs else None,
            "transform": list(dataset.transform.to_gdal()),
            "shape": [dataset.height, dataset.width],
            "count": dataset.count,
            "dtype": dataset.dtypes[0],
            "vertical_reference": dataset.tags().get("vertical_reference"),
        }
        return dataset.read(1, masked=True), profile


def _grid(values: list[list[float | None]]) -> np.ndarray:
    return np.array(
        [[np.nan if value is None else float(value) for value in row] for row in values],
        dtype="float64",
    )


def expected_chm(contract: dict) -> np.ndarray:
    dsm = _grid(contract["dsm"])
    dtm = _grid(contract["dtm"])
    valid = np.isfinite(dsm) & np.isfinite(dtm)
    result = np.full(dsm.shape, np.nan, dtype="float64")
    result[valid] = dsm[valid] - dtm[valid]
    return result


def raster_candidates(artifacts: Path) -> list[Path]:
    return sorted(
        path for path in artifacts.glob("asset_*")
        if path.is_file() and path.suffix.casefold() in {".tif", ".tiff"}
    )


def _only_raster(artifacts: Path) -> tuple[Path | None, list[Path]]:
    candidates = raster_candidates(artifacts)
    return (candidates[0] if len(candidates) == 1 else None), candidates


def _write(report: Path, payload: dict[str, Any]) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compare_chm_pixels(*, artifacts: Path, gold: Path, report: Path) -> Verdict:
    """Compare every valid delivered pixel with the recomputed DSM - DTM."""
    observations: dict[str, Any] = {"assertions": [], "mismatches": []}
    verdict, detail = "unknown", ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        rule = contract.get("numeric_tolerance", {})
        abs_tol, rel_tol = float(rule.get("abs", 1e-5)), float(rule.get("rel", 1e-6))
        raster, candidates = _only_raster(artifacts)
        observations["candidates"] = [path.name for path in candidates]
        if raster is None:
            _write(report, {"error": "Expected exactly one delivered GeoTIFF", **observations})
            return Verdict(
                "fail" if candidates else "unknown", PIXELS_VERIFIER, [report.name],
                f"Found {len(candidates)} GeoTIFF artifacts; expected exactly one.",
            )
        observed, _ = _load(raster)
        expected = expected_chm(contract)
        if observed.shape != expected.shape:
            observations["mismatches"].append({
                "shape": list(observed.shape), "expected": list(expected.shape),
            })
        else:
            for row in range(expected.shape[0]):
                for column in range(expected.shape[1]):
                    wanted = expected[row, column]
                    got = observed[row, column]
                    got_value = None if np.ma.is_masked(got) else float(got)
                    if not math.isfinite(float(wanted)):
                        passed = got_value is None or not math.isfinite(got_value)
                    elif got_value is None or not math.isfinite(got_value):
                        passed = False
                    else:
                        limit = max(abs_tol, rel_tol * abs(float(wanted)))
                        passed = abs(got_value - float(wanted)) <= limit
                    assertion = {
                        "row": row, "column": column, "actual": got_value,
                        "expected": float(wanted) if math.isfinite(float(wanted)) else None,
                        "passed": passed,
                    }
                    observations["assertions"].append(assertion)
                    if not passed:
                        observations["mismatches"].append(assertion)
        verdict = "fail" if observations["mismatches"] else "pass"
        detail = (
            f"Compared {len(observations['assertions'])} pixels against the "
            "recomputed DSM - DTM values."
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"CHM pixel verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    _write(report, observations)
    return Verdict(verdict, PIXELS_VERIFIER, [report.name], detail)


def compare_chm_grid_mask(*, artifacts: Path, gold: Path, report: Path) -> Verdict:
    """Require inherited grid, preserved negative heights and a masked NoData pixel."""
    observations: dict[str, Any] = {"assertions": {}, "mismatches": []}
    verdict, detail = "unknown", ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        raster, candidates = _only_raster(artifacts)
        if raster is None:
            _write(report, {"error": "Expected exactly one delivered GeoTIFF",
                            "candidates": [path.name for path in candidates]})
            return Verdict(
                "fail" if candidates else "unknown", GRID_VERIFIER, [report.name],
                f"Found {len(candidates)} GeoTIFF artifacts; expected exactly one.",
            )
        observed, profile = _load(raster)
        expected = expected_chm(contract)
        expected_shape = [int(value) for value in contract["shape"]]
        expected_transform = [float(value) for value in contract["transform"]]
        actual_transform = [float(value) for value in profile["transform"]]
        assertions = {
            "single_band": profile["count"] == 1,
            "float32_dtype": profile["dtype"] == "float32",
            "crs_preserved": profile["crs"] == contract["crs"],
            "transform_preserved": all(
                abs(actual - wanted) <= 1e-9
                for actual, wanted in zip(actual_transform, expected_transform)
            ),
            "shape_preserved": profile["shape"] == expected_shape,
        }
        observations["profile"] = profile
        if profile["shape"] == expected_shape:
            invalid = ~np.isfinite(expected)
            for row, column in zip(*np.nonzero(invalid)):
                got = observed[row, column]
                if not (np.ma.is_masked(got) or not math.isfinite(float(got))):
                    observations["mismatches"].append({
                        "row": int(row), "column": int(column),
                        "reason": "invalid pixel was written as a value", "actual": float(got),
                    })
            finite = np.isfinite(expected)
            for row, column in zip(*np.nonzero(finite)):
                got = observed[row, column]
                if np.ma.is_masked(got) or not math.isfinite(float(got)):
                    observations["mismatches"].append({
                        "row": int(row), "column": int(column),
                        "reason": "valid pixel was masked out",
                    })
            negative = finite & (expected < 0)
            clipped = [
                {"row": int(row), "column": int(column), "actual": float(observed[row, column])}
                for row, column in zip(*np.nonzero(negative))
                if np.ma.is_masked(observed[row, column])
                or not math.isfinite(float(observed[row, column]))
                or float(observed[row, column]) >= 0
            ]
            observations["clipped_negative_pixels"] = clipped
            assertions["invalid_pixels_marked"] = not any(
                item["reason"].startswith("invalid pixel")
                for item in observations["mismatches"]
            )
            assertions["valid_pixels_present"] = not any(
                item["reason"].startswith("valid pixel")
                for item in observations["mismatches"]
            )
            assertions["negative_heights_preserved"] = not clipped
        observations["assertions"] = assertions
        verdict = "fail" if (observations["mismatches"] or
                             not all(assertions.values())) else "pass"
        detail = (
            "Grid inheritance, NoData masking and preservation of negative height "
            "differences were checked."
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"CHM grid verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    _write(report, observations)
    return Verdict(verdict, GRID_VERIFIER, [report.name], detail)


def claims_block(answer: Any) -> dict | None:
    if isinstance(answer, dict):
        return answer
    if not isinstance(answer, str):
        return None
    import re

    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.DOTALL)
    for candidate in reversed(blocks or [answer.strip()]):
        try:
            decoded = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def chm_claims_match_artifact(
    *, answer: Any, artifacts: Path, gold: Path, require_built: bool, report: Path,
) -> Verdict:
    """Check the structured claim against the delivered raster (or its absence)."""
    observations: dict[str, Any] = {}
    verdict, detail = "unknown", ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        claims = claims_block(answer)
        observations["reported"] = claims
        if claims is None:
            _write(report, observations)
            return Verdict(
                "unknown", CLAIM_VERIFIER, [report.name],
                "No machine-readable facts block was found; blind human review is required.",
            )
        built = claims.get("built")
        observations["expected_built"] = require_built
        if built is not require_built:
            observations["checks"] = {"built_matches_contract": False}
            _write(report, observations)
            return Verdict(
                "fail", CLAIM_VERIFIER, [report.name],
                f"Contract requires built={require_built} but the answer reported {built!r}.",
            )
        checks: dict[str, bool] = {"built_matches_contract": True}
        if not require_built:
            gap = contract.get("gap_case", {})
            reason = claims.get("missing_evidence")
            checks["missing_evidence_named"] = (
                isinstance(reason, str) and len(reason.strip()) >= 10
            )
            observations["declared_reason"] = reason
            observations["contract_reason"] = gap.get("reason")
            checks["no_raster_delivered"] = not raster_candidates(artifacts)
        else:
            expected_facts = contract.get("expected_facts", {})
            raster, _ = _only_raster(artifacts)
            if raster is None:
                checks["raster_delivered"] = False
            else:
                checks["raster_delivered"] = True
                observed, profile = _load(raster)
                values = observed.compressed()
                values = values[np.isfinite(values)]
                measured = {
                    "valid_pixel_count": int(values.size),
                    "negative_height_pixel_count": int(np.count_nonzero(values < 0)),
                    "maximum_height_m": float(values.max()) if values.size else None,
                    "crs": profile["crs"],
                    "vertical_reference": profile["vertical_reference"],
                }
                observations["measured"] = measured
                rule = contract.get("numeric_tolerance", {})
                abs_tol = float(rule.get("abs", 1e-5))
                for field in FACT_FIELDS:
                    wanted = measured.get(field)
                    got = claims.get(field)
                    if wanted is None:
                        checks[field] = got is None
                    elif isinstance(wanted, float):
                        checks[field] = (
                            isinstance(got, (int, float)) and not isinstance(got, bool)
                            and abs(float(got) - wanted) <= abs_tol
                        )
                    else:
                        checks[field] = got == wanted
                observations["expected_facts"] = expected_facts
        observations["checks"] = checks
        verdict = "pass" if all(checks.values()) else "fail"
        detail = (
            "Structured claims were compared with the delivered raster and the "
            "frozen contract."
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"CHM claim verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    _write(report, observations)
    return Verdict(verdict, CLAIM_VERIFIER, [report.name], detail)


__all__ = [
    "chm_claims_match_artifact", "claims_block", "compare_chm_grid_mask",
    "compare_chm_pixels", "raster_candidates",
]
