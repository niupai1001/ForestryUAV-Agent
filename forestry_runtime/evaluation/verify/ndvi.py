"""Independent NDVI verification: pixels, grid/mask, and reported statistics.

The verifier recomputes the index from the frozen source arrays using the declared
formula and scale/offset, then compares the delivered raster pixel by pixel. It
never trusts a correlation coefficient, and it never reads the agent's own
statistics to decide whether the pixels are correct — the reported facts are
checked against numbers measured from the delivered file.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from .base import Verdict


PIXELS_VERIFIER = "ndvi-pixels-v1"
GRID_VERIFIER = "ndvi-grid-mask-v1"
FACTS_VERIFIER = "ndvi-reported-facts-v1"


def _load(raster: Path) -> tuple[np.ndarray, dict[str, Any]]:
    with rasterio.open(raster) as dataset:
        profile = {
            "crs": str(dataset.crs) if dataset.crs else None,
            "transform": list(dataset.transform.to_gdal()),
            "shape": [dataset.height, dataset.width],
            "count": dataset.count,
            "dtype": dataset.dtypes[0],
            "nodata": dataset.nodatavals[0],
        }
        return dataset.read(1, masked=True), profile


def _expected(contract: dict) -> np.ndarray:
    red = np.array(contract["red"], dtype="float64")
    nir = np.array(contract["nir"], dtype="float64")
    scale = float(contract.get("scale", 1.0))
    offset = float(contract.get("offset", 0.0))
    red = red * scale + offset
    nir = nir * scale + offset
    denominator = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        return (nir - red) / denominator


def _single_ndvi_artifact(artifacts: Path) -> list[Path]:
    candidates = sorted(
        path for path in artifacts.glob("asset_*")
        if path.is_file() and path.suffix.casefold() in {".tif", ".tiff"}
    )
    return candidates


def compare_ndvi_pixels(
    *, artifacts: Path, gold: Path, report: Path,
) -> Verdict:
    """Compare every valid delivered pixel with the independently recomputed value."""
    observations: dict[str, Any] = {"assertions": [], "mismatches": []}
    verdict = "unknown"
    detail = ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        rule = contract.get("numeric_tolerance", {})
        abs_tol = float(rule.get("abs", 1e-6))
        rel_tol = float(rule.get("rel", 1e-6))
        candidates = _single_ndvi_artifact(artifacts)
        observations["candidates"] = [path.name for path in candidates]
        if len(candidates) != 1:
            (report.parent / report.name).write_text(json.dumps({
                "error": "Expected exactly one delivered GeoTIFF", **observations,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            return Verdict(
                "fail" if candidates else "unknown", PIXELS_VERIFIER, [report.name],
                f"Found {len(candidates)} GeoTIFF artifacts; expected exactly one.",
            )
        observed, _ = _load(candidates[0])
        expected = _expected(contract)
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
                    finite_wanted = math.isfinite(float(wanted))
                    if not finite_wanted:
                        passed = got_value is None or not math.isfinite(got_value)
                    elif got_value is None or not math.isfinite(got_value):
                        passed = False
                    else:
                        limit = max(abs_tol, rel_tol * abs(float(wanted)))
                        passed = abs(got_value - float(wanted)) <= limit
                    assertion = {
                        "row": row, "column": column,
                        "actual": got_value,
                        "expected": float(wanted) if finite_wanted else None,
                        "passed": passed,
                    }
                    observations["assertions"].append(assertion)
                    if not passed:
                        observations["mismatches"].append(assertion)
        verdict = "fail" if observations["mismatches"] else "pass"
        detail = (
            f"Compared {len(observations['assertions'])} pixels against the "
            "recomputed (NIR-Red)/(NIR+Red) values."
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"NDVI pixel verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
    return Verdict(verdict, PIXELS_VERIFIER, [report.name], detail)


def compare_ndvi_grid_mask(
    *, artifacts: Path, gold: Path, report: Path,
) -> Verdict:
    """Require the delivered grid to be inherited and zero denominators to be invalid."""
    observations: dict[str, Any] = {"assertions": {}, "mismatches": []}
    verdict = "unknown"
    detail = ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        candidates = _single_ndvi_artifact(artifacts)
        if len(candidates) != 1:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({
                "error": "Expected exactly one delivered GeoTIFF",
                "candidates": [path.name for path in candidates],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            return Verdict(
                "fail" if candidates else "unknown", GRID_VERIFIER, [report.name],
                f"Found {len(candidates)} GeoTIFF artifacts; expected exactly one.",
            )
        observed, profile = _load(candidates[0])
        expected = _expected(contract)
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
        observations["expected"] = {
            "crs": contract["crs"], "transform": expected_transform,
            "shape": expected_shape,
        }
        if profile["shape"] == expected_shape:
            invalid = ~np.isfinite(expected)
            for row, column in zip(*np.nonzero(invalid)):
                got = observed[row, column]
                if not (np.ma.is_masked(got) or not math.isfinite(float(got))):
                    observations["mismatches"].append({
                        "row": int(row), "column": int(column),
                        "reason": "zero-denominator pixel was written as a value",
                        "actual": float(got),
                    })
            finite = np.isfinite(expected)
            for row, column in zip(*np.nonzero(finite)):
                got = observed[row, column]
                if np.ma.is_masked(got) or not math.isfinite(float(got)):
                    observations["mismatches"].append({
                        "row": int(row), "column": int(column),
                        "reason": "valid pixel was masked out",
                    })
            assertions["invalid_pixels_marked"] = not any(
                item["reason"].startswith("zero-denominator")
                for item in observations["mismatches"]
            )
            assertions["valid_pixels_present"] = not any(
                item["reason"].startswith("valid pixel")
                for item in observations["mismatches"]
            )
        observations["assertions"] = assertions
        verdict = "fail" if (observations["mismatches"] or not all(assertions.values())) else "pass"
        detail = "Grid inheritance and invalid-pixel masking were checked against the source grid."
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"NDVI grid verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
    return Verdict(verdict, GRID_VERIFIER, [report.name], detail)


def reported_facts_match_artifact(
    *, answer: Any, artifacts: Path, gold: Path, report: Path,
) -> Verdict:
    """Check the reported statistics against numbers measured from the delivered file."""
    observations: dict[str, Any] = {}
    verdict = "unknown"
    detail = ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        observed_claims = answer if isinstance(answer, dict) else None
        if isinstance(answer, str):
            import re

            blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.DOTALL)
            candidates = blocks or [answer.strip()]
            try:
                decoded = json.loads(candidates[-1])
                observed_claims = decoded if isinstance(decoded, dict) else None
            except (json.JSONDecodeError, TypeError):
                observed_claims = None
        observations["reported"] = observed_claims
        candidates = _single_ndvi_artifact(artifacts)
        if len(candidates) != 1:
            observations["error"] = "Expected exactly one delivered GeoTIFF"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
            return Verdict(
                "unknown", FACTS_VERIFIER, [report.name],
                "The delivered raster is missing, so reported statistics cannot be checked.",
            )
        measured, profile = _load(candidates[0])
        values = measured.compressed()
        values = values[np.isfinite(values)]
        expected = _expected(contract)
        rule = contract.get("numeric_tolerance", {})
        abs_tol = float(rule.get("abs", 1e-6))
        measured_facts = {
            "valid_pixel_count": int(values.size),
            "zero_denominator_pixel_count": int(np.count_nonzero(~np.isfinite(expected))),
            "mean": float(values.mean()) if values.size else None,
            "crs": profile["crs"],
            "output_shape": profile["shape"],
        }
        observations["measured"] = measured_facts
        checks: dict[str, bool] = {}
        if observed_claims is None:
            observations["checks"] = {}
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
            return Verdict(
                "unknown", FACTS_VERIFIER, [report.name],
                "No machine-readable facts block was found; blind human review is required.",
            )
        for key, wanted in measured_facts.items():
            got = observed_claims.get(key)
            if wanted is None:
                checks[key] = got is None
            elif isinstance(wanted, float):
                checks[key] = (
                    isinstance(got, (int, float)) and not isinstance(got, bool)
                    and abs(float(got) - wanted) <= max(abs_tol, 1e-6 * abs(wanted))
                )
            elif isinstance(wanted, list):
                checks[key] = list(got or []) == wanted
            else:
                checks[key] = got == wanted
        observations["checks"] = checks
        verdict = "pass" if all(checks.values()) else "fail"
        detail = "Reported statistics were compared with values measured from the delivered raster."
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"Reported-fact verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
    return Verdict(verdict, FACTS_VERIFIER, [report.name], detail)


__all__ = [
    "compare_ndvi_grid_mask", "compare_ndvi_pixels", "reported_facts_match_artifact",
]
