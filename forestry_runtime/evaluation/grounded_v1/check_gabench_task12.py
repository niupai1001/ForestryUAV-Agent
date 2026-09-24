"""Independent recomputation check for GABench task 12 (terrain ruggedness).

The project's rule is that every retained GIS question must have a result that
we can recompute ourselves from the published inputs, without calling the
upstream agent's toolchain and without trusting its published output. This
script does exactly that for the one GABench candidate whose reference result is
fully determined, and prints the per-pixel agreement.

Reading the elevation raster: 255 must be treated as elevation, not as nodata --
the upstream file declares no nodata value, and treating 255 as a sentinel
silently removes a band of low ground.

Usage:
    python evaluation/grounded_v1/check_gabench_task12.py \
        --repo data/gabench/repo
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import generic_filter


def recompute(elevation: np.ndarray, window: int = 3) -> np.ndarray:
    """Neighbourhood maximum minus minimum, matching the published method."""
    size = (window, window)
    high = generic_filter(elevation.astype("float64"), np.max, size=size, mode="nearest")
    low = generic_filter(elevation.astype("float64"), np.min, size=size, mode="nearest")
    return high - low


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--window", type=int, default=3)
    args = parser.parse_args()
    dataset = args.repo / "dataset"
    elevation_path = dataset / "Elevation.tif"
    reference_path = dataset / "ruggedness.tif"

    with rasterio.open(elevation_path) as source:
        elevation = source.read(1)
        elevation_grid = {
            "crs": str(source.crs), "shape": [source.height, source.width],
            "res": [float(value) for value in source.res],
            "dtype": source.dtypes[0], "declared_nodata": source.nodata,
            "min": int(elevation.min()), "max": int(elevation.max()),
        }
    with rasterio.open(reference_path) as source:
        reference = source.read(1)
        reference_grid = {
            "crs": str(source.crs), "shape": [source.height, source.width],
            "res": [float(value) for value in source.res],
            "dtype": source.dtypes[0], "declared_nodata": source.nodata,
        }

    if elevation_grid["shape"] != reference_grid["shape"]:
        raise ValueError("Elevation and reference ruggedness grids differ")

    ours = recompute(elevation, args.window)
    reference_values = reference.astype("float64")
    difference = np.abs(ours - reference_values)
    # 255 elevation is real ground here; report what treating it as nodata would hide.
    sentinel = int((elevation == 255).sum())

    report = {
        "task_id": "gabench-12",
        "elevation": elevation_grid,
        "reference": reference_grid,
        "window": args.window,
        "elevation_255_pixels": sentinel,
        "pixels": int(elevation.size),
        "max_abs_difference": float(difference.max()),
        "mean_abs_difference": float(difference.mean()),
        "mismatched_pixels": int((difference > 0).sum()),
        "exact_match": bool(difference.max() == 0),
        "reference_range": [float(reference_values.min()), float(reference_values.max())],
        "recomputed_range": [float(ours.min()), float(ours.max())],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["exact_match"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
