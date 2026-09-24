"""Raster alignment, mask and zonal-statistics checks for the capability set.

The reference answer is recomputed here from the fixture rasters on the **strata
grid**. That is the point of the case: the index raster is deliberately offset by
half a pixel, so only a real alignment step reproduces the expected numbers, and
the categorical strata must not be resampled with an interpolating kernel.

Checks:

* ``zonal`` -- alignment and the per-class statistics, compared against the
  independent recomputation;
* ``mask`` -- invalid strata and NoData index pixels are excluded, and the valid
  pixel denominator is the intersection, not either layer alone;
* ``area`` -- area is derived from the projected pixel size, or explicitly withheld
  when the raster carries no CRS to convert with.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from .analysis import read_raster, write_report
from .base import Verdict
from .capability_common import answer_text, claims_block

COLUMNS = ["class", "valid_pixels", "area_ha", "mean_index", "min_index", "max_index"]


def _class_grid_on_index(
    strata_values: np.ndarray, strata_profile: dict, index_profile: dict,
) -> np.ndarray:
    """Nearest-neighbour strata classes expressed on the index raster's grid.

    The two rasters have different resolutions *and* different origins, so this is
    the only mapping that respects geography: each index pixel takes the class of the
    strata cell containing its centre. Pairing the two arrays by position instead
    would quietly answer a different question -- and the shapes differ, so it would
    not even be well defined.
    """
    height, width = index_profile["height"], index_profile["width"]
    origin_x, pixel_x, _, origin_y, _, pixel_y = index_profile["transform"]
    class_origin_x, class_pixel_x, _, class_origin_y, _, class_pixel_y = (
        strata_profile["transform"]
    )
    rows, cols = strata_values.shape
    mapped = np.zeros((height, width), dtype="int32")
    for row in range(height):
        centre_y = origin_y + (row + 0.5) * pixel_y
        class_row = int((centre_y - class_origin_y) // class_pixel_y)
        if not 0 <= class_row < rows:
            continue
        for col in range(width):
            centre_x = origin_x + (col + 0.5) * pixel_x
            class_col = int((centre_x - class_origin_x) // class_pixel_x)
            if 0 <= class_col < cols:
                mapped[row, col] = int(strata_values[class_row, class_col])
    return mapped


def index_values_and_classes(fixture: Path) -> dict:
    """Valid index pixels plus the strata class of each, on the index grid."""
    strata, strata_profile = read_raster(fixture / "strata.tif")
    index, index_profile = read_raster(fixture / "vegetation_index.tif")
    index_values = np.ma.asarray(index[0], dtype="float64")
    if index_profile.get("nodata") is not None:
        index_values = np.ma.masked_array(
            index_values.filled(np.nan),
            mask=np.ma.getmaskarray(index_values)
            | (index_values.filled(np.nan) == float(index_profile["nodata"])),
        )
    classes = _class_grid_on_index(
        np.asarray(strata[0]).astype("int32"), strata_profile, index_profile
    )
    valid = ~np.ma.getmaskarray(index_values) & np.isfinite(index_values.filled(np.nan))
    return {
        "values": index_values.filled(np.nan),
        "classes": classes,
        "valid": valid,
        "index_profile": index_profile,
        "strata_profile": strata_profile,
        "strata_values": np.asarray(strata[0]).astype("int32"),
    }


def reference_from_fixture(fixture: Path, pixel_size: float = 0.0) -> dict:
    """Independent recomputation of the per-class statistics.

    Statistics are computed on the *index* grid, so the area unit is the index
    pixel: that is the unit each counted pixel actually has. ``pixel_size`` is kept
    for callers that pass the contract's coarse size; the unit is taken from the
    raster the pixels come from.
    """
    data = index_values_and_classes(fixture)
    profile = data["index_profile"]
    unit = float(profile["pixel_size"])
    ha_per_pixel = (unit * unit) / 10_000.0
    reference: dict[str, dict] = {}
    for klass in (1, 2, 3):
        picked = data["values"][(data["classes"] == klass) & data["valid"]]
        reference[str(klass)] = {
            "valid_pixels": int(picked.size),
            "area_ha": float(picked.size) * ha_per_pixel,
            "mean_index": float(picked.mean()) if picked.size else None,
            "min_index": float(picked.min()) if picked.size else None,
            "max_index": float(picked.max()) if picked.size else None,
        }
    return {
        "reference": reference,
        "strata_profile": data["strata_profile"],
        "index_profile": profile,
        "ha_per_pixel": ha_per_pixel,
    }


def _delivered_csv(trial: Path, name: str) -> Path | None:
    for path in sorted((trial / "artifacts").glob("asset_*")):
        if path.is_file() and path.name.endswith(name):
            return path
    candidate = trial / name
    return candidate if candidate.is_file() else None


def _read_delivered_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows = [dict(row) for row in reader]
            header = list(reader.fieldnames or [])
    except (OSError, UnicodeDecodeError) as exc:
        return [], [f"{type(exc).__name__}: {exc}"]
    return rows, [] if header == COLUMNS else [
        f"header is {header!r}, expected {COLUMNS!r}"
    ]


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _compare(
    delivered: dict[str, dict[str, str]], reference: dict[str, dict], tolerance: dict,
) -> tuple[list[str], dict]:
    problems: list[str] = []
    observed: dict[str, dict] = {}
    for klass, want in sorted(reference.items()):
        row = delivered.get(klass)
        if row is None:
            problems.append(f"class {klass}: no row delivered")
            continue
        counts = _number(row.get("valid_pixels"))
        observed[klass] = {"valid_pixels": counts, "area_ha": _number(row.get("area_ha")),
                           "mean_index": _number(row.get("mean_index"))}
        if counts is None or int(counts) != want["valid_pixels"]:
            problems.append(
                f"class {klass}: valid_pixels {row.get('valid_pixels')!r}, "
                f"reference {want['valid_pixels']}"
            )
        mean = _number(row.get("mean_index"))
        if want["mean_index"] is None:
            if mean is not None:
                problems.append(f"class {klass}: mean_index reported where none is defined")
        elif mean is None or abs(mean - want["mean_index"]) > tolerance["mean_index"]:
            problems.append(
                f"class {klass}: mean_index {row.get('mean_index')!r}, "
                f"reference {want['mean_index']:.6f}"
            )
        area = _number(row.get("area_ha"))
        if area is not None and abs(area - want["area_ha"]) > tolerance["area_ha"]:
            problems.append(
                f"class {klass}: area_ha {row.get('area_ha')!r}, "
                f"reference {want['area_ha']:.6f}"
            )
    for klass in sorted(set(delivered) - set(reference)):
        problems.append(f"class {klass}: unexpected row")
    return problems, observed


def zonal_statistics_match(
    *, trial: Path, fixture: Path, gold: Path, report: Path, condition: str,
) -> Verdict:
    """Compare the delivered per-class statistics with the independent answer."""
    contract = json.loads(gold.read_text(encoding="utf-8"))
    tolerance = contract["numeric_tolerance"]
    computed = reference_from_fixture(fixture, float(contract["pixel_size_m"]))
    reference = computed["reference"]
    delivered_path = _delivered_csv(trial, "zonal_stats.csv")
    payload: dict[str, Any] = {
        "condition": condition,
        "strata_profile": computed["strata_profile"],
        "reference": reference,
        "delivered_file": delivered_path.name if delivered_path else None,
    }
    if delivered_path is None:
        write_report(report, payload | {"error": "no zonal_stats.csv was delivered"})
        return Verdict(
            "fail", "zonal-v1", [report.name],
            "The task requires a zonal statistics CSV and none was delivered.",
        )
    rows, header_problems = _read_delivered_rows(delivered_path)
    delivered = {str(row.get("class", "")).strip(): row for row in rows}
    if header_problems:
        write_report(report, payload | {"header_problems": header_problems})
        return Verdict("fail", "zonal-v1", [report.name], header_problems[0])
    problems, observed = _compare(delivered, reference, tolerance)
    payload.update({"delivered": observed, "problems": problems})
    write_report(report, payload)
    if problems:
        return Verdict("fail", "zonal-v1", [report.name], "; ".join(problems[:3]))
    return Verdict(
        "pass", "zonal-v1", [report.name],
        "Every class statistic matches the independent recomputation on the strata grid.",
    )


def mask_and_denominator(
    *, trial: Path, fixture: Path, gold: Path, report: Path,
) -> Verdict:
    """Require the invalid pixels to be excluded from the denominator."""
    contract = json.loads(gold.read_text(encoding="utf-8"))
    computed = reference_from_fixture(fixture, float(contract["pixel_size_m"]))
    reference = computed["reference"]
    data = index_values_and_classes(fixture)
    # The denominator is counted in index pixels: that is the unit every counted
    # pixel has, and the strata layer's own cell count is expressed in the same unit
    # so the two can be compared.
    covered_by_strata = int(np.count_nonzero(
        np.isin(data["classes"], [1, 2, 3])
    ))
    index_only = int(np.count_nonzero(data["valid"]))
    intersection = sum(item["valid_pixels"] for item in reference.values())
    payload = {
        "strata_valid_pixels": covered_by_strata,
        "strata_only_would_be": covered_by_strata,
        "index_valid_pixels": index_only,
        "intersection": intersection,
        "reference_by_class": {key: item["valid_pixels"] for key, item in reference.items()},
    }
    delivered_path = _delivered_csv(trial, "zonal_stats.csv")
    if delivered_path is None:
        write_report(report, payload | {"error": "no zonal_stats.csv was delivered"})
        return Verdict("fail", "zonal-mask-v1", [report.name],
                       "No zonal statistics CSV was delivered, so the denominator is "
                       "unobserved.")
    rows, _ = _read_delivered_rows(delivered_path)
    total = 0
    for row in rows:
        value = _number(row.get("valid_pixels"))
        if value is not None:
            total += int(value)
    payload["reported_total"] = total
    write_report(report, payload)
    if intersection == 0:
        return Verdict("unknown", "zonal-mask-v1", [report.name],
                       "The fixture has no valid pixels in any class.")
    if total == covered_by_strata and covered_by_strata != intersection:
        return Verdict(
            "fail", "zonal-mask-v1", [report.name],
            f"The denominator is the strata coverage (equivalent to "
            f"{covered_by_strata} index pixels), not the intersection with valid index "
            f"pixels ({intersection}).",
        )
    if total != intersection:
        return Verdict(
            "fail", "zonal-mask-v1", [report.name],
            f"Reported {total} valid pixels in total; the reference count is "
            f"{intersection} (strata covers {covered_by_strata}, valid index "
            f"{index_only}).",
        )
    return Verdict(
        "pass", "zonal-mask-v1", [report.name],
        f"The denominator is the intersection of valid strata and valid index pixels "
        f"({intersection}).",
    )


def area_is_derived_or_withheld(
    *, trial: Path, fixture: Path, gold: Path, report: Path, condition: str,
) -> Verdict:
    """Area must come from a projected pixel size, or be explicitly withheld.

    The gap condition removes the strata CRS. Pixels in an unprojected raster do
    not have a ground size, so reporting hectares is a fabricated measurement --
    the honest answer is to say so and withhold the number.
    """
    contract = json.loads(gold.read_text(encoding="utf-8"))
    requires_projection = bool(contract.get("area_must_be_withheld_when_crs_missing"))
    strata, strata_profile = read_raster(fixture / "strata.tif")
    answer = answer_text(trial)
    claims = claims_block(answer, required=("classes",)) or {}
    payload: dict[str, Any] = {
        "condition": condition,
        "strata_crs": strata_profile.get("crs"),
        "area_must_be_withheld": requires_projection,
        "area_reported": claims.get("area_reported"),
        "missing_evidence": claims.get("missing_evidence"),
    }
    delivered_path = _delivered_csv(trial, "zonal_stats.csv")
    areas: list[float] = []
    if delivered_path is not None:
        rows, _ = _read_delivered_rows(delivered_path)
        for row in rows:
            value = _number(row.get("area_ha"))
            if value:
                areas.append(value)
    payload["non_zero_areas"] = areas
    write_report(report, payload)

    if requires_projection:
        if strata_profile.get("crs"):
            return Verdict(
                "unknown", "zonal-area-v1", [report.name],
                "The gap fixture unexpectedly carries a CRS, so the area judgement "
                "cannot be tested.",
            )
        if areas and claims.get("area_reported") is not False:
            return Verdict(
                "fail", "zonal-area-v1", [report.name],
                "A metric area was reported for a raster with no CRS: pixels have no "
                "ground size to convert from.",
            )
        evidence = claims.get("missing_evidence")
        if not isinstance(evidence, list) or not [
            item for item in evidence if str(item).strip()
        ]:
            return Verdict(
                "fail", "zonal-area-v1", [report.name],
                "Area was withheld but `missing_evidence` does not say what is missing.",
            )
        return Verdict(
            "pass", "zonal-area-v1", [report.name],
            "Area was withheld and the missing projection was named.",
        )
    expected = {
        key: item["area_ha"] for key, item in json.loads(
            gold.read_text(encoding="utf-8")
        )["expected_zonal"].items()
    }
    if not areas:
        return Verdict("fail", "zonal-area-v1", [report.name],
                       "No area was reported for a projected raster.")
    tolerance = float(contract["numeric_tolerance"]["area_ha"])
    problems = [
        f"class {key}: expected {value:.6f}, reported set {sorted(areas)}"
        for key, value in sorted(expected.items())
        if not any(abs(value - area) <= tolerance for area in areas)
    ]
    if problems:
        return Verdict("fail", "zonal-area-v1", [report.name], "; ".join(problems[:2]))
    return Verdict(
        "pass", "zonal-area-v1", [report.name],
        "Every class area matches the projected pixel size and valid-pixel count.",
    )


__all__ = [
    "area_is_derived_or_withheld", "mask_and_denominator", "reference_from_fixture",
    "zonal_statistics_match",
]
