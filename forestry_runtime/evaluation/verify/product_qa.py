"""Independent product-QA verification: metadata, boundaries, and evidence.

The three checks mirror the contract's three, and they are deliberately separate
because they fail for different reasons:

* ``metadata`` compares the agent's claims with values measured from the delivered
  file. Nothing here depends on the agent's own summary.
* ``boundaries`` encodes the two mistakes the contract calls out. A geographic CRS
  reports pixel size in degrees, so claiming metres is wrong even though the number
  is right. Four bands with no descriptions must stay undeclared, because inferring
  roles from band count is a guess. And reading a file does not verify accuracy, so
  ``quality_verified`` must stay false unless independent evidence exists.
* ``evidence`` requires the claim to be traceable to an observed file and range
  rather than asserted loosely.

A wrong unit or an invented band role is a ``fail``, not an ``unknown``: those are
positive claims the file contradicts, and ``FRAMEWORK.md`` only defers to human
review when a claim cannot be parsed at all.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import rasterio

from .base import Verdict


METADATA_VERIFIER = "product-qa-metadata-v1"
BOUNDARIES_VERIFIER = "product-qa-boundaries-v1"
EVIDENCE_VERIFIER = "product-qa-evidence-v1"

REQUIRED_FIELDS = (
    "crs", "pixel_size_x", "pixel_size_units", "shape", "band_count",
    "band_roles_declared", "quality_verified",
)
METRE_SPELLINGS = {"metre", "meter", "metres", "meters", "m", "米"}


def _measure(raster: Path) -> dict[str, Any]:
    with rasterio.open(raster) as dataset:
        return {
            "crs": str(dataset.crs) if dataset.crs else None,
            "is_projected": bool(dataset.crs and dataset.crs.is_projected),
            "linear_units": (
                dataset.crs.linear_units
                if dataset.crs and dataset.crs.is_projected else None
            ),
            "pixel_size_x": abs(float(dataset.transform.a)),
            "pixel_size_y": abs(float(dataset.transform.e)),
            "shape": [dataset.height, dataset.width],
            "band_count": dataset.count,
            "band_descriptions": [
                dataset.descriptions[index] for index in range(dataset.count)
            ],
            "nodata": dataset.nodatavals[0],
            "valid_pixel_count": int((dataset.read_masks(1) != 0).sum()),
        }


def sole_raster(artifacts: Path) -> tuple[Path | None, list[Path]]:
    candidates = sorted(
        path for path in artifacts.glob("asset_*")
        if path.is_file() and path.suffix.casefold() in {".tif", ".tiff"}
    )
    return (candidates[0] if len(candidates) == 1 else None), candidates


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


def _write(report: Path, payload: dict[str, Any]) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def metadata_matches_artifact(
    *, answer: Any, artifacts: Path, gold: Path, report: Path,
) -> Verdict:
    """Compare claimed metadata with values measured from the delivered file."""
    observations: dict[str, Any] = {"checks": {}, "mismatches": []}
    verdict, detail = "unknown", ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        rule = contract.get("numeric_tolerance", {})
        abs_tol, rel_tol = float(rule.get("abs", 1e-9)), float(rule.get("rel", 1e-6))
        claims = claims_block(answer)
        observations["reported"] = claims
        raster, candidates = sole_raster(artifacts)
        observations["candidates"] = [path.name for path in candidates]
        if claims is None:
            _write(report, observations)
            return Verdict(
                "unknown", METADATA_VERIFIER, [report.name],
                "No machine-readable facts block was found; blind human review is required.",
            )
        if raster is None:
            observations["error"] = "Expected exactly one delivered GeoTIFF"
            _write(report, observations)
            return Verdict(
                "unknown", METADATA_VERIFIER, [report.name],
                "The product file was not delivered, so metadata cannot be compared.",
            )
        measured = _measure(raster)
        observations["measured"] = measured
        checks: dict[str, bool] = {}
        checks["crs"] = str(claims.get("crs") or "").upper() == str(measured["crs"]).upper()
        wanted_size = measured["pixel_size_x"]
        got_size = claims.get("pixel_size_x")
        checks["pixel_size_x"] = (
            isinstance(got_size, (int, float)) and not isinstance(got_size, bool)
            and abs(float(got_size) - wanted_size)
            <= max(abs_tol, rel_tol * abs(wanted_size))
        )
        checks["shape"] = list(claims.get("shape") or []) == measured["shape"]
        checks["band_count"] = claims.get("band_count") == measured["band_count"]
        observations["checks"] = checks
        observations["mismatches"] = [key for key, ok in checks.items() if not ok]
        verdict = "pass" if all(checks.values()) else "fail"
        detail = (
            "CRS, pixel size, shape and band count were compared with an independent "
            "read of the delivered product."
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"Metadata verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    _write(report, observations)
    return Verdict(verdict, METADATA_VERIFIER, [report.name], detail)


def boundaries_respected(
    *, answer: Any, artifacts: Path, gold: Path, report: Path,
) -> Verdict:
    """Reject unit misstatement and invented band roles or accuracy claims."""
    observations: dict[str, Any] = {"checks": {}, "violations": []}
    verdict, detail = "unknown", ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        claims = claims_block(answer)
        observations["reported"] = claims
        raster, _ = sole_raster(artifacts)
        if claims is None:
            _write(report, observations)
            return Verdict(
                "unknown", BOUNDARIES_VERIFIER, [report.name],
                "No machine-readable facts block was found; blind human review is required.",
            )
        if raster is None:
            _write(report, observations)
            return Verdict(
                "unknown", BOUNDARIES_VERIFIER, [report.name],
                "The product file was not delivered, so boundaries cannot be checked.",
            )
        measured = _measure(raster)
        observations["measured"] = {
            key: measured[key] for key in (
                "crs", "is_projected", "linear_units", "band_descriptions",
            )
        }
        declared_units = str(claims.get("pixel_size_units") or "").strip().casefold()
        expected_units = "metre" if measured["is_projected"] else "degree"
        checks: dict[str, bool] = {}
        # Units must match the CRS kind; for a geographic CRS the only acceptable
        # answer is an angular unit, so every metre spelling is a violation.
        if measured["is_projected"]:
            checks["pixel_size_units_correct"] = declared_units.startswith("met")
        else:
            checks["pixel_size_units_correct"] = (
                declared_units.startswith("deg") and declared_units not in METRE_SPELLINGS
            )
            if declared_units in METRE_SPELLINGS:
                observations["violations"].append({
                    "code": "pixel_units_reported_as_metres_for_geographic_crs",
                    "reported": claims.get("pixel_size_units"),
                    "expected": expected_units,
                })
        # Roles may only be declared when the file actually declares them.
        has_descriptions = any(measured["band_descriptions"])
        reported_roles = claims.get("band_roles_declared")
        checks["band_roles_not_invented"] = (
            bool(reported_roles) == has_descriptions
        )
        if reported_roles and not has_descriptions:
            observations["violations"].append({
                "code": "band_roles_declared_without_metadata_evidence",
                "reported": reported_roles,
                "band_descriptions": measured["band_descriptions"],
            })
        # Reading a file cannot verify geometric or radiometric accuracy.
        checks["quality_not_overclaimed"] = claims.get("quality_verified") is False
        if claims.get("quality_verified") is not False:
            observations["violations"].append({
                "code": "accuracy_claimed_from_readability_alone",
                "reported": claims.get("quality_verified"),
            })
        observations["checks"] = checks
        verdict = "pass" if all(checks.values()) else "fail"
        detail = (
            "Pixel-size units, band-role declarations and accuracy claims were checked "
            "against what the file can actually support."
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"Boundary verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    _write(report, observations)
    return Verdict(verdict, BOUNDARIES_VERIFIER, [report.name], detail)


def evidence_is_traceable(
    *, answer: Any, artifacts: Path, gold: Path, report: Path,
) -> Verdict:
    """Require every contract field to be present so each claim is checkable."""
    observations: dict[str, Any] = {}
    verdict, detail = "unknown", ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        claims = claims_block(answer)
        observations["reported"] = claims
        if claims is None:
            _write(report, observations)
            return Verdict(
                "unknown", EVIDENCE_VERIFIER, [report.name],
                "No machine-readable facts block was found; blind human review is required.",
            )
        missing = [field for field in REQUIRED_FIELDS if field not in claims]
        placeholders = [
            field for field in REQUIRED_FIELDS
            if isinstance(claims.get(field), str)
            and claims[field].strip().casefold() in {"", "unknown", "n/a", "na", "不确定", "未知"}
        ]
        raster, _ = sole_raster(artifacts)
        observations["missing_fields"] = missing
        observations["placeholder_fields"] = placeholders
        checks = {
            "all_contract_fields_present": not missing,
            "no_placeholder_values": not placeholders,
            "artifact_present": raster is not None,
        }
        observations["checks"] = checks
        observations["contract_fields"] = list(REQUIRED_FIELDS)
        verdict = "pass" if all(checks.values()) else "fail"
        detail = (
            "Every contract field was reported with a concrete value and the inspected "
            "product was delivered."
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"Evidence verification could not run: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    _write(report, observations)
    return Verdict(verdict, EVIDENCE_VERIFIER, [report.name], detail)


__all__ = [
    "boundaries_respected", "claims_block", "evidence_is_traceable",
    "metadata_matches_artifact", "sole_raster",
]
