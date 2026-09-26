"""The frozen truth for the two retained GABench tasks.

Every value here was recomputed by this project's own code from the pinned
inputs (``recompute_gabench.py``); the tested agent's toolchain is never the
standard. Keeping the truth in a tracked module is deliberate: ``data/`` is
gitignored, so a truth that lived only beside the data would disappear with it
and could not be reviewed or diffed.

Both entries record *how* the value was reproduced, because for these two tasks
the method is the contestable part, not the arithmetic.
"""

from __future__ import annotations

GABENCH_COMMIT = "e8c64e883bbe45e2b94515c73941e7a0837320ae"

# ---------------------------------------------------------------------------
# ID 12 -- terrain ruggedness, raster
# ---------------------------------------------------------------------------
ID12 = {
    "kind": "gis_raster",
    # 31 MB of raster cannot live in the task file, so the frozen copy is kept
    # outside the public root and identified by digest. It is a byte copy of the
    # published dataset/ruggedness.tif, which sits inside the directory the agent
    # is shown -- see the leakage note below.
    "gold_raster": "data/gabench/private/gold/ruggedness-Elevation.tif",
    "gold_raster_sha256": "200bcfb4aeb2a4ef8522a6c4e68424ff4273192e249e61abd73451188bf886f7",
    "gold_raster_bytes": 31461518,
    "grid": {
        "crs": "EPSG:26911",
        "shape": [2494, 3062],
        "transform": [30.0, 0.0, 307049.43822125107, 0.0, -30.0, 3837890.3523405134],
        "dtype": "uint8",
        "nodata": None,
    },
    "value_range": {"min": 0, "max": 36},
    "valid_pixels": 7636628,
    "method": (
        "3x3 neighbourhood range (maximum minus minimum) of band 1 of Elevation.tif "
        "read as float64, edge pixels using the nearest available neighbour"
    ),
    "recomputation": {
        "mismatched_pixels": 0,
        "max_abs_difference": 0.0,
        "note": "0 of 7,636,628 pixels differ from the published reference raster.",
    },
    "pitfalls": [
        "255 is real elevation in this dataset and must not be read as nodata; the "
        "file declares no nodata and the reference treats 255 as a normal value.",
        "The published reference raster lives inside the directory the agent is "
        "shown. Only the files named in public_inputs may be copied into the agent "
        "workspace, and the frozen gold copy is kept outside it.",
    ],
}

# ---------------------------------------------------------------------------
# ID 9 -- deforestation rate, vector
# ---------------------------------------------------------------------------
ID09 = {
    "kind": "gis_vector",
    "gold_value": 0.4793110356552816,
    "gold_field": "percentage_deforestation",
    "gold_source": "published dataset/result/deforestation_rate.csv",
    "intermediates": {
        "buffer_area_m2": 179792795540.404,
        "deforested_clip_area_m2": 86176671033.82933,
    },
    "method": (
        "assign EPSG:4326 where a GeoJSON declares none, reproject both layers to "
        "EPSG:32723, buffer the roads by 5500 m, dissolve the buffers, intersect "
        "with the deforested polygon, and take clipped_area / buffer_area"
    ),
    "recomputation": {
        "buffer_area_m2": 179792795540.4048,
        "deforested_clip_area_m2": 86176671033.82924,
        "ratio": 0.47931103565527894,
        "ratio_absolute_error": 2.6645352591003757e-15,
        "roads_features": 27662,
        "deforested_features": 1,
        "deforested_geometry_valid_as_published": False,
        "repair_used": "buffer(0)",
    },
    "pitfalls": [
        "The published deforested polygon is topologically invalid and GEOS refuses "
        "to overlay it as published (TopologyException: side location conflict). The "
        "reference repaired it with a zero-width buffer, which reproduces the "
        "published value to 1e-15.",
        "shapely.make_valid() also repairs it but drops about 2.04 km^2 and gives "
        "0.47930037874086123, 1.07e-05 away. That route is a defensible reading of "
        "the task, so the frozen tolerance covers both; make_valid must not be used "
        "as the standard itself.",
    ],
}


def gold_entry(task_id: str) -> dict:
    if task_id == "gabench-12-terrain-ruggedness":
        return dict(ID12)
    if task_id == "gabench-09-deforestation-buffer":
        return dict(ID09)
    raise KeyError(f"no frozen GABench gold for {task_id}")
