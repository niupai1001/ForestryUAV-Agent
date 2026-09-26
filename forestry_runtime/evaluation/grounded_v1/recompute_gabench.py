"""Independent recomputation of the two retained GABench tasks (ID 9, ID 12).

The ledger recorded ID 12 as "verified pixel by pixel" and ID 9 as "arithmetic
checked, geometry not independently recomputed" because geopandas was missing.
This script closes that gap with the project's own code: it never calls anything
from the GABench repository or from the runtime under test.

ID 12 -- 3x3 neighbourhood range on Elevation.tif, compared pixel by pixel with
         the published dataset/ruggedness.tif (tolerance 0).
ID 9  -- EPSG:4326 -> EPSG:32723 -> buffer 5500 m -> dissolve -> clip by the
         deforested area -> ratio, compared with the published
         dataset/result/deforestation_rate.csv.

Usage:
    python evaluation/grounded_v1/recompute_gabench.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import generic_filter

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "data/gabench/repo"
DATASET = REPO / "dataset"

# Published reference values (GABench pinned commit e8c64e88).
ID12_REFERENCE_RASTER = DATASET / "ruggedness.tif"
ID09_REFERENCE_CSV = DATASET / "result" / "deforestation_rate.csv"
ID09_BUFFER_AREA = 179792795540.404
ID09_DEFOREST_AREA = 86176671033.82933
ID09_RATIO = 0.4793110356552816


def recompute_id12() -> dict:
    source = DATASET / "Elevation.tif"
    with rasterio.open(source) as handle:
        elevation = handle.read(1).astype("float64")
        profile = handle.profile
        print(f"ID12 input: {source.name} shape={elevation.shape} dtype={profile['dtype']} "
              f"crs={profile['crs']} nodata={profile['nodata']}", flush=True)

    def local_range(values: np.ndarray) -> float:
        return float(np.nanmax(values) - np.nanmin(values))

    print("ID12: running 3x3 generic_filter over "
          f"{elevation.size} pixels (this takes a while) ...", flush=True)
    rugged = generic_filter(elevation, local_range, size=(3, 3))

    with rasterio.open(ID12_REFERENCE_RASTER) as handle:
        reference = handle.read(1).astype("float64")

    if rugged.shape != reference.shape:
        return {"ok": False, "reason": f"shape {rugged.shape} != {reference.shape}"}
    difference = np.abs(rugged - reference)
    mismatched = int((difference != 0).sum())
    return {
        "ok": mismatched == 0,
        "pixels": int(rugged.size),
        "mismatched_pixels": mismatched,
        "max_abs_difference": float(difference.max()),
        "recomputed_min": float(rugged.min()),
        "recomputed_max": float(rugged.max()),
        "recomputed_nan": int(np.isnan(rugged).sum()),
        "reference_raster": str(ID12_REFERENCE_RASTER.relative_to(ROOT)),
        "note": "255 is NOT nodata; the reference treats it as a normal elevation.",
    }


def recompute_id09() -> dict:
    import geopandas as gpd

    roads = gpd.read_file(DATASET / "roads.geojson")
    deforested = gpd.read_file(DATASET / "deforestedArea.geojson")
    print(f"ID09 inputs: roads={len(roads)} features crs={roads.crs}; "
          f"deforested={len(deforested)} features crs={deforested.crs}", flush=True)

    # The task statement: a GeoJSON without an explicit CRS is WGS84.
    for frame, name in ((roads, "roads"), (deforested, "deforestedArea")):
        if frame.crs is None:
            print(f"ID09: {name} has no CRS, assigning EPSG:4326 as the task states", flush=True)
            frame.set_crs("EPSG:4326", inplace=True)

    target = "EPSG:32723"
    roads = roads.to_crs(target)
    deforested = deforested.to_crs(target)

    print("ID09: buffering 5500 m ...", flush=True)
    buffered = roads.buffer(5500)
    print("ID09: dissolving ...", flush=True)
    dissolved = buffered.union_all() if hasattr(buffered, "union_all") else buffered.unary_union
    area_buffer = float(dissolved.area)

    print("ID09: clipping by the deforested polygon ...", flush=True)
    # The published deforested polygon is NOT topologically valid: overlaying it
    # as published raises "TopologyException: side location conflict", which is
    # why the ledger could only check the arithmetic. The reference toolchain
    # repaired it with a zero-width buffer -- that route reproduces the published
    # value to 1e-15, while shapely.make_valid() drops ~2.04 km^2 and lands
    # 2.2e-05 away. See gabench_id09_clip_diagnosis_v2.json.
    raw = deforested.geometry.iloc[0] if len(deforested) == 1 else (
        deforested.geometry.union_all() if hasattr(deforested.geometry, "union_all")
        else deforested.geometry.unary_union
    )
    raw_is_valid = bool(raw.is_valid)
    deforest_geometry = raw if raw_is_valid else raw.buffer(0)
    print(f"ID09: deforested geometry valid as published: {raw_is_valid}; "
          f"repair used: {'none' if raw_is_valid else 'buffer(0)'}", flush=True)

    clipped = dissolved.intersection(deforest_geometry)
    area_deforested = float(clipped.area)

    ratio = area_deforested / area_buffer
    return {
        "ok": bool(abs(ratio - ID09_RATIO) <= 1e-6 * max(1.0, abs(ID09_RATIO))),
        "roads_features": int(len(roads)),
        "deforested_features": int(len(deforested)),
        "deforested_geometry_valid_as_published": raw_is_valid,
        "repair_used": "none" if raw_is_valid else "buffer(0)",
        "recomputed_buffer_area_m2": area_buffer,
        "reference_buffer_area_m2": ID09_BUFFER_AREA,
        "buffer_area_relative_error": abs(area_buffer - ID09_BUFFER_AREA) / ID09_BUFFER_AREA,
        "recomputed_deforested_area_m2": area_deforested,
        "reference_deforested_area_m2": ID09_DEFOREST_AREA,
        "deforested_area_relative_error": abs(area_deforested - ID09_DEFOREST_AREA) / ID09_DEFOREST_AREA,
        "recomputed_ratio": ratio,
        "reference_ratio": ID09_RATIO,
        "ratio_absolute_error": abs(ratio - ID09_RATIO),
        "published_csv": ID09_REFERENCE_CSV.read_text(encoding="utf-8").strip(),
    }


def main() -> int:
    report: dict = {"repo_head": "e8c64e883bbe45e2b94515c73941e7a0837320ae"}
    print("=== ID 12 terrain ruggedness ===", flush=True)
    report["id12"] = recompute_id12()
    print(json.dumps(report["id12"], ensure_ascii=False, indent=2), flush=True)

    print("\n=== ID 9 deforestation buffer rate ===", flush=True)
    report["id09"] = recompute_id09()
    print(json.dumps(report["id09"], ensure_ascii=False, indent=2), flush=True)

    out = Path(__file__).with_name("gabench_recompute.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten: {out}", flush=True)

    ok = bool(report["id12"]["ok"] and report["id09"]["ok"])
    print("RESULT:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
