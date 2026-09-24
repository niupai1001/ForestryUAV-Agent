"""Independent recomputation helpers for the capability task set.

Everything here computes its own answer from the fixture bytes with numpy and
rasterio. Nothing imports Runtime code, and nothing calls a function under test:
the expected value must not come from the system being measured.

Reference implementations here follow the published definition of each quantity, so
a disagreement with the Agent is a real disagreement about the data.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio


def read_raster(path: Path) -> tuple[np.ndarray, dict]:
    """Read a raster as a masked float array plus its geospatial profile.

    ``transform`` is the six-coefficient GDAL order
    ``(origin_x, pixel_x, rotation_x, origin_y, rotation_y, pixel_y)``, which is the
    order every verifier in this package relies on.
    """
    with rasterio.open(path) as dataset:
        bands = dataset.read(masked=True).astype("float64")
        profile = {
            "crs": dataset.crs.to_string() if dataset.crs else None,
            "transform": list(dataset.transform.to_gdal()),
            "width": dataset.width,
            "height": dataset.height,
            "count": dataset.count,
            "dtype": dataset.dtypes[0],
            "nodata": dataset.nodata,
            "bounds": list(dataset.bounds),
            "descriptions": list(dataset.descriptions),
            "pixel_size": abs(float(dataset.transform.a)),
        }
    return bands, profile


def grid_matches(left: dict, right: dict, *, tolerance: float = 1e-9) -> list[str]:
    """Compare two raster grids component by component."""
    problems: list[str] = []
    if left["crs"] != right["crs"]:
        problems.append(f"crs {left['crs']} != {right['crs']}")
    if (left["width"], left["height"]) != (right["width"], right["height"]):
        problems.append(
            f"shape {(left['width'], left['height'])} != "
            f"{(right['width'], right['height'])}"
        )
    for index, (a, b) in enumerate(zip(left["transform"], right["transform"])):
        if abs(float(a) - float(b)) > tolerance:
            problems.append(f"transform[{index}] {a} != {b}")
    return problems


def valid_values(array: np.ndarray) -> np.ndarray:
    """Finite values that are not masked."""
    data = np.ma.asarray(array)
    compressed = data.compressed()
    return compressed[np.isfinite(compressed)]


def ndvi_reference(red: np.ndarray, nir: np.ndarray) -> np.ma.MaskedArray:
    """The published NDVI definition with an explicit invalid denominator.

    ``(NIR - Red) / (NIR + Red)`` is undefined where the denominator is zero, and
    those pixels are invalid rather than infinite or zero.
    """
    red = np.ma.asarray(red, dtype="float64")
    nir = np.ma.asarray(nir, dtype="float64")
    denominator = nir + red
    mask = np.ma.getmaskarray(red) | np.ma.getmaskarray(nir) | (denominator == 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        index = (nir - red) / np.where(denominator == 0, np.nan, denominator)
    return np.ma.masked_array(index.filled(np.nan), mask=mask | ~np.isfinite(index.filled(np.nan)))


def chm_reference(dsm: np.ndarray, dtm: np.ndarray) -> np.ma.MaskedArray:
    """``DSM - DTM`` with negatives retained and both masks honoured."""
    dsm = np.ma.asarray(dsm, dtype="float64")
    dtm = np.ma.asarray(dtm, dtype="float64")
    mask = np.ma.getmaskarray(dsm) | np.ma.getmaskarray(dtm)
    return np.ma.masked_array(dsm.filled(np.nan) - dtm.filled(np.nan), mask=mask)


def describe(values: np.ndarray) -> dict:
    """Summary statistics over valid values only, with the denominator reported."""
    flat = np.asarray(values, dtype="float64").ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return {
            "valid_pixels": 0, "mean": None, "median": None,
            "min": None, "max": None, "std": None, "sum": None,
        }
    return {
        "valid_pixels": int(flat.size),
        "mean": float(flat.mean()),
        "median": float(np.median(flat)),
        "min": float(flat.min()),
        "max": float(flat.max()),
        "std": float(flat.std(ddof=1)) if flat.size > 1 else 0.0,
        "sum": float(flat.sum()),
    }


def pixel_agreement(
    observed: Path, expected: np.ma.MaskedArray, *, tolerance: float
) -> dict:
    """Compare every valid pixel of a delivered raster against the reference."""
    result: dict[str, Any] = {
        "tolerance": tolerance, "mismatches": [], "compared_pixels": 0,
    }
    try:
        bands, profile = read_raster(observed)
    except (OSError, rasterio.errors.RasterioIOError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    delivered = bands[0]
    valid = ~np.ma.getmaskarray(delivered) & np.isfinite(delivered.filled(np.nan))
    reference_valid = ~np.ma.getmaskarray(expected) & np.isfinite(
        expected.filled(np.nan)
    )
    if valid.shape != reference_valid.shape:
        result["error"] = (
            f"shape {valid.shape} does not match the reference {reference_valid.shape}"
        )
        return result
    comparable = valid & reference_valid
    only_delivered = int(np.count_nonzero(valid & ~reference_valid))
    only_reference = int(np.count_nonzero(reference_valid & ~valid))
    result.update({
        "compared_pixels": int(np.count_nonzero(comparable)),
        "delivered_only_pixels": only_delivered,
        "reference_only_pixels": only_reference,
        "delivered_valid_pixels": int(np.count_nonzero(valid)),
        "reference_valid_pixels": int(np.count_nonzero(reference_valid)),
        "profile": profile,
    })
    if comparable.any():
        difference = np.abs(
            delivered.filled(np.nan)[comparable] - expected.filled(np.nan)[comparable]
        )
        result["max_abs_difference"] = float(difference.max())
        result["mean_abs_difference"] = float(difference.mean())
        if result["max_abs_difference"] > tolerance:
            worst = np.argwhere(comparable & (
                np.abs(delivered.filled(np.nan) - expected.filled(np.nan)) > tolerance
            ))[:10]
            result["mismatches"] = [
                {
                    "row": int(row), "col": int(col),
                    "delivered": float(delivered.filled(np.nan)[row, col]),
                    "reference": float(expected.filled(np.nan)[row, col]),
                }
                for row, col in worst
            ]
    if only_delivered:
        result["mismatches"].append({
            "reason": f"{only_delivered} pixel(s) valid in the result but invalid in "
                      "the reference: the input mask was not inherited",
        })
    if only_reference:
        result["mismatches"].append({
            "reason": f"{only_reference} pixel(s) valid in the reference but missing "
                      "from the result",
        })
    return result


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


__all__ = [
    "chm_reference", "describe", "grid_matches", "ndvi_reference", "numeric",
    "pixel_agreement", "read_csv_rows", "read_raster", "valid_values",
    "write_report",
]
