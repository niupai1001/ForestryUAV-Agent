"""TaskSpec construction for the grounded pilot, and the frozen task file.

Two sources feed the specs:

* the frozen OAM question bank (``grounded_v1_1_private/manifest.json``) supplies
  the twelve canopy tasks, their public inputs, their grid and their truth;
* the GABench screening ledger supplies the three GIS tasks. Those keep GABench's
  own published reference results, and each carries the method by which we
  recomputed it independently -- never the agent's toolchain.

The *rule* (tolerances, deliverables, what invalidates delivery) is written here
so it is reviewable and versioned. The *truth* stays in the private manifest.

Usage:
    python -m evaluation.grounded_v1.specs --manifest <path> --out <tasks file>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .grading import SUITE_VERSION, TaskSpec

#: Default wall-clock budget per trial. Recorded in every spec so a trial outside
#: it is a known failure rather than an after-the-fact judgement.
DEFAULT_BUDGET_SECONDS = 900

OAM_FAMILIES = {"canopy": "canopy_extraction", "spatial": "canopy_statistics"}

CANOPY_DELIVERABLES = [
    "a single-band GeoTIFF of the same grid as the input",
    "class values limited to 0 (non-canopy) and 1 (canopy)",
    "a text answer stating the canopy coverage percentage",
]

CANOPY_RULES = [
    "The valid domain is every input pixel whose RGB is not all zero; the pixel is "
    "excluded from both numerator and denominator otherwise.",
    "Foreground is a non-zero class value; background is 0. The annotation encodes "
    "canopy groups as colours and black as background, so binarisation is 'any channel "
    "non-zero is canopy'.",
    "IoU = TP / (TP + FP + FN) over the frozen valid domain. Truth and prediction both "
    "empty scores 1 by the frozen convention and is reported separately; truth empty and "
    "prediction non-empty scores 0.",
    "Precision, recall, F1 and the coverage error in percentage points are reported "
    "beside IoU.",
    "The delivered raster must share the input's CRS (semantically) and its cell grid "
    "and affine transform (tight tolerance). A delivery on a different grid fails "
    "delivery validity.",
    "The coverage figure in the answer must agree with the delivered raster within 0.5 "
    "percentage points. The relaxed tolerance is deliberate: a rounded figure ('about "
    "27%') is a correct report, and it is the pixel comparison, not this check, that "
    "judges the extraction. A grossly different figure ('99.9%') is a contradiction and "
    "invalidates delivery.",
]

STATISTICS_DELIVERABLES = [
    "a JSON object carrying the canopy coverage percentage, the canopy pixel count and "
    "the valid pixel count",
]

STATISTICS_RULES = [
    "The provided mask tile is the whole observable domain: valid pixels are all pixels "
    "of that tile. The original imagery's no-observation gaps are not visible in a mask, "
    "so no denominator may be required that the agent cannot observe.",
    "canopy_pixels counts non-zero pixels of the provided mask.",
    "coverage_percent = 100 * canopy_pixels / valid_pixels.",
    "Each reported number is judged by |reported - truth| <= max(absolute_tolerance, "
    "relative_tolerance * |truth|) with the tolerance frozen before the run.",
    "A missing or non-numeric required field is a failure with that reason, not a zero "
    "error.",
    "All required assertions must pass for the task to score 1; otherwise it scores 0.",
]


def load_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def oam_specs(manifest: dict, *, public_root: Path, budget_seconds: int = DEFAULT_BUDGET_SECONDS) -> list[TaskSpec]:
    """Build the twelve canopy specs from the frozen manifest."""
    specs: list[TaskSpec] = []
    for task in manifest["tasks"]:
        family = OAM_FAMILIES[task["kind"]]
        prompt_path = public_root / task["public_prompt"]
        question = prompt_path.read_text(encoding="utf-8").strip() if prompt_path.is_file() else ""
        extraction = family == "canopy_extraction"
        specs.append(TaskSpec(
            task_id=task["task_id"],
            family=family,
            question=question,
            public_root=str(public_root),
            public_inputs=[task["public_input"]],
            required_deliverables=list(CANOPY_DELIVERABLES if extraction else STATISTICS_DELIVERABLES),
            grading_rule=list(CANOPY_RULES if extraction else STATISTICS_RULES),
            condition=task["valid_definition"],
            budget={"wall_seconds": budget_seconds, "repeats": 3},
            resources={"python": ["rasterio", "numpy"], "network": "not required"},
            tolerance=(
                {"coverage_points": 0.5} if extraction
                else {"pixels_absolute": 1.0, "pixels_relative": 0.0, "coverage_points": 1e-9}
            ),
        ))
    return specs


def gabench_specs(*, repo: Path, budget_seconds: int = DEFAULT_BUDGET_SECONDS) -> list[TaskSpec]:
    """The three frozen GIS tasks, drawn from the GABench screening ledger.

    Family assignment follows the plan: vector processing, raster processing and
    combined analysis, two of each where the bank allows. The bank did not allow
    it -- see ``GABENCH_LEDGER.md`` -- so one task stands for each family and the
    shortfall is reported rather than padded with synthetic tasks.
    """
    dataset = Path(repo) / "dataset"
    return [
        TaskSpec(
            task_id="gabench-09-deforestation-buffer",
            family="gis_analysis",
            question=(
                "计算巴西 Rondônia 州道路 5.5 km 缓冲区内的森林砍伐率，即缓冲区内的砍伐面积占缓冲区总面积的"
                "百分比。结果保存为 deforestation_rate.csv。若某个 GeoJSON 未显式声明坐标系，则按 WGS84 "
                "(EPSG:4326) 处理。"
            ),
            public_root=str(dataset),
            public_inputs=["roads.geojson", "deforestedArea.geojson"],
            required_deliverables=["deforestation_rate.csv holding the percentage"],
            grading_rule=[
                "Reproject both layers to EPSG:32723 (metres), buffer the roads by 5500 m, "
                "dissolve the buffers, intersect with the deforested polygon, and take "
                "100 * clipped_area / buffer_area.",
                "The reference result is recomputed by this project from the published inputs; "
                "the tested agent's toolchain is never used to produce the standard answer.",
                "Absolute tolerance 0.01 percentage points, because the published reference "
                "carries six decimal places.",
                "A CSV that reports metres, an area, or an unlabelled number is a failure with "
                "that reason.",
            ],
            condition="vector",
            budget={"wall_seconds": budget_seconds, "repeats": 3},
            resources={"python": ["geopandas", "shapely", "pyproj"], "network": "not required"},
            tolerance={"coverage_points": 0.01},
        ),
        TaskSpec(
            task_id="gabench-12-terrain-ruggedness",
            family="gis_analysis",
            question=(
                "用高程数据计算地形起伏度（ruggedness）：以 3×3 邻域的高程极差（最大值减最小值）作为每个像元的"
                "起伏度，输出与输入同网格的 GeoTIFF。"
            ),
            public_root=str(dataset),
            public_inputs=["Elevation.tif"],
            required_deliverables=["a GeoTIFF of terrain ruggedness on the input grid"],
            grading_rule=[
                "Ruggedness = neighbourhood maximum minus neighbourhood minimum over a 3x3 "
                "window, edge pixels using the nearest available neighbour.",
                "The published reference raster ruggedness.tif was recomputed independently "
                "by this project and matched exactly (0 of 7,636,628 pixels differ); that "
                "recomputation, not the agent, is the standard.",
                "255 is real elevation in this dataset and must not be read as nodata.",
                "Every valid pixel must match the reference exactly: the rule is integer "
                "arithmetic, so no tolerance is granted.",
            ],
            condition="raster",
            budget={"wall_seconds": budget_seconds, "repeats": 3},
            resources={"python": ["rasterio", "numpy", "scipy"], "network": "not required"},
            tolerance={"pixels_absolute": 0.0, "pixels_relative": 0.0},
        ),
        TaskSpec(
            task_id="gabench-42-building-line-of-sight",
            family="gis_analysis",
            question=(
                "对给定的两个三维点（观察点 ID 0、目标点 ID 2）以三维建筑数据为障碍做通视分析，判断目标点是否"
                "可见，结果写入 los_stats.json 的 visible 字段。"
            ),
            public_root=str(dataset),
            public_inputs=["point3d.shp", "point3d.shx", "point3d.dbf", "point3d.prj",
                           "buildings.shp", "buildings.shx", "buildings.dbf", "buildings.prj"],
            required_deliverables=["los_stats.json holding the boolean visible field"],
            grading_rule=[
                "Read the two control points from point3d by FID 0 and 2, read every vertex of "
                "the building multipatch as a candidate obstacle, and report whether any "
                "obstacle vertex lies within 2.0 m of the 3D segment between them.",
                "The published reference result is visible=true; this project's independent "
                "recomputation reproduces it.",
                "Known limitation, recorded before the run: the upstream point layer (Web "
                "Mercator) and building layer (British National Grid) are never reprojected, "
                "and in the published geometry no obstacle vertex comes within 2 m of the "
                "segment. The task is therefore deterministic and recomputable but degenerate, "
                "so it is retained as one weak GIS sample, not as evidence about 3D analysis.",
                "The boolean must match exactly; a numeric stand-in, a distance, or a "
                "confidence value is a failure with that reason.",
            ],
            condition="combined",
            budget={"wall_seconds": budget_seconds, "repeats": 3},
            resources={"python": ["geopandas", "shapely", "pyproj"], "network": "not required"},
            tolerance={"exact": True},
        ),
    ]


def build_tasks(
    *, manifest_path: Path, public_root: Path, gabench_repo: Path,
    budget_seconds: int = DEFAULT_BUDGET_SECONDS,
) -> dict:
    manifest = load_manifest(manifest_path)
    specs = oam_specs(manifest, public_root=public_root, budget_seconds=budget_seconds)
    specs += gabench_specs(repo=gabench_repo, budget_seconds=budget_seconds)
    return {
        "suite_version": manifest["version"],
        "declared_suite_version": SUITE_VERSION,
        "dataset_revision": manifest["revision"],
        "test_shard_sha256": manifest["test_shard_sha256"],
        "task_count": len(specs),
        "gabench_repo": str(gabench_repo),
        "tasks": [spec.as_dict() for spec in specs],
        "gold": {
            task["task_id"]: {
                "kind": task["kind"],
                "gold_mask": task["gold_mask"],
                "valid_definition": task["valid_definition"],
                "valid_pixels": task["valid_pixels"],
                "gold_canopy_pixels": task["gold_canopy_pixels"],
                "gold_foreground_pixels": task["gold_foreground_pixels"],
                "gold_coverage_percent": task["gold_coverage_percent"],
                "grid": task["grid"],
                "image_id": task["image_id"],
                "oam_id": task["oam_id"],
                "biome": task["biome"],
            }
            for task in manifest["tasks"]
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path("data/oam_tcd/grounded_v1_1_private/manifest.json"))
    parser.add_argument("--public-root", type=Path, default=Path("data/oam_tcd/grounded_v1_1_public"))
    parser.add_argument("--gabench-repo", type=Path, default=Path("data/gabench/repo"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/grounded_v1/tasks.grounded-v1.2.json"))
    parser.add_argument("--budget-seconds", type=int, default=DEFAULT_BUDGET_SECONDS)
    args = parser.parse_args()
    payload = build_tasks(
        manifest_path=args.manifest, public_root=args.public_root,
        gabench_repo=args.gabench_repo, budget_seconds=args.budget_seconds,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(args.out), "task_count": payload["task_count"],
        "families": sorted({task["family"] for task in payload["tasks"]}),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
