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

from .gabench_gold import gold_entry
from .grading import GROUNDED_RULES_VERSION, SUITE_VERSION, TaskSpec, assert_portable

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
            public_root=Path(public_root).as_posix(),
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
    """The two frozen GIS tasks, drawn from the GABench screening ledger.

    ``gabench-42`` used to be the third and was removed at ``grounded-v1.2``: its
    two layers use incompatible CRSs and the flow never reprojects, 1237 of 5172
    building vertices are the -1.797e308 sentinel, and no vertex comes within the
    2 m threshold, so its ``visible=true`` follows from "no obstacle survived the
    filter" rather than from any line-of-sight computation. See
    ``GABENCH_LEDGER.md`` section 4.3. It is deleted here as well as from the
    frozen file, because leaving it would silently resurrect the task the next
    time this module regenerates the bank.
    """
    dataset = Path(repo) / "dataset"
    return [
        TaskSpec(
            task_id="gabench-09-deforestation-buffer",
            family="gis_analysis",
            question=(
                "计算巴西 Rondônia 州道路 5.5 km 缓冲区内的森林砍伐率，即缓冲区内的砍伐面积占缓冲区总面积的"
                "比例。结果保存为 deforestation_rate.csv 的 percentage_deforestation 列，"
                "**写成比值形式（约 0.479），不要写成百分数（不要写 47.9）**。"
                "若某个 GeoJSON 未显式声明坐标系，则按 WGS84 (EPSG:4326) 处理。"
            ),
            public_root=dataset.as_posix(),
            public_inputs=["roads.geojson", "deforestedArea.geojson"],
            required_deliverables=["deforestation_rate.csv holding the percentage"],
            grading_rule=[
                # The previous text said "100 * clipped_area / buffer_area", which
                # contradicts the published reference (0.4793110356552816 -- the bare
                # ratio). They cannot both hold, and a task whose stated rule and
                # stated answer disagree cannot score, so the rule is corrected to
                # the ratio the reference actually encodes.
                "Reproject both layers to EPSG:32723 (metres), buffer the roads by 5500 m, "
                "dissolve the buffers, intersect with the deforested polygon, and report "
                "clipped_area / buffer_area as a ratio.",
                "The reference result is recomputed by this project from the published inputs; "
                "the tested agent's toolchain is never used to produce the standard answer.",
                "Tolerance is relative 1e-4 with an absolute floor of 1e-9. The width is "
                "deliberate: the published deforested polygon is topologically invalid, and two "
                "defensible repairs of it (buffer(0), which the reference used, and "
                "shapely.make_valid) differ by 1.07e-05. A tighter tolerance would measure "
                "which repair the agent guessed rather than whether it solved the task.",
                "A CSV that reports metres, an area, or an unlabelled number is a failure with "
                "that reason. So is the same number written as a percentage (47.9): the frozen "
                "answer is the ratio.",
            ],
            condition="vector",
            budget={"wall_seconds": budget_seconds, "repeats": 3},
            resources={"python": ["geopandas", "shapely", "pyproj"], "network": "not required"},
            tolerance={"value_absolute": 1e-9, "value_relative": 1e-4},
            process_rubric={
                # Only steps no correct method can skip are frozen: overlapping
                # road buffers must be dissolved before any area is measured, or
                # shared ground is counted twice, and the clip must be an
                # intersection performed in a projected CRS. The buffer distance
                # and that CRS are the frozen parameters. How the layers are read
                # or written is deliberately left open.
                "method_markers": [
                    ["buffer"],
                    ["dissolve", "union_all", "unary_union"],
                    ["intersection", "intersect", "clip", "overlay"],
                ],
                "key_parameters": [
                    {"name": "buffer_distance_5500_m", "numeric": 5500},
                    {"name": "projected_crs_epsg_32723", "tokens": ["32723"]},
                ],
            },
        ),
        TaskSpec(
            task_id="gabench-12-terrain-ruggedness",
            family="gis_analysis",
            question=(
                "用高程数据计算地形起伏度（ruggedness）：以 3×3 邻域的高程极差（最大值减最小值）作为每个像元的"
                "起伏度，输出与输入同网格的 GeoTIFF。"
            ),
            public_root=dataset.as_posix(),
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
            process_rubric={
                # A neighbourhood operator and a range over it: the two things
                # "3x3 local elevation range" actually consists of. Equivalent
                # spellings of each are accepted, so a correct implementation is
                # not failed for naming its filter differently.
                "method_markers": [
                    ["generic_filter", "maximum_filter", "minimum_filter", "uniform_filter",
                     "neighborhood", "neighbourhood", "sliding_window", "rolling"],
                    ["range", "ptp", "maximum", "minimum", "max", "min", "amax", "amin"],
                ],
                "key_parameters": [
                    {"name": "neighbourhood_window_3x3",
                     "tokens": ["3x3", "3×3", "(3, 3)", "(3,3)", "3, 3", "size=3"]},
                ],
            },
        ),
    ]


def build_tasks(
    *, manifest_path: Path, public_root: Path, gabench_repo: Path,
    budget_seconds: int = DEFAULT_BUDGET_SECONDS,
) -> dict:
    manifest = load_manifest(manifest_path)
    specs = oam_specs(manifest, public_root=public_root, budget_seconds=budget_seconds)
    specs += gabench_specs(repo=gabench_repo, budget_seconds=budget_seconds)

    # The bank has to run on a machine that is not the one that froze it. An
    # absolute path here would work today and fail on every other checkout, and
    # the failure would look like a missing data file rather than a bad freeze.
    for spec in specs:
        assert_portable(spec.task_id, spec.public_root)
        assert_portable(spec.task_id, str(spec.input_path))
        for name in spec.public_inputs:
            assert_portable(spec.task_id, name)

    return {
        # Two different versions, and conflating them is how a bank silently
        # reverts. ``suite_version`` is the question bank (which task set is
        # being run); ``selection_version`` is the rule that drew the samples,
        # which stayed at grounded-v1.1 when gabench-42 was dropped. Reading the
        # suite version off the manifest would report v1.1 for a v1.2 bank.
        "suite_version": SUITE_VERSION,
        "selection_version": manifest["version"],
        "declared_suite_version": SUITE_VERSION,
        # Which grading rules this bank was frozen against. A report records its
        # rules version too, so a result can always be traced back to the rules
        # that produced it rather than to whichever rules are current.
        "rules_version": GROUNDED_RULES_VERSION,
        "dataset_revision": manifest["revision"],
        "test_shard_sha256": manifest["test_shard_sha256"],
        "task_count": len(specs),
        "gabench_repo": Path(gabench_repo).as_posix(),
        "tasks": [spec.as_dict() for spec in specs],
        "gold": {
            **{
                spec.task_id: gold_entry(spec.task_id)
                for spec in specs if spec.family == "gis_analysis"
            },
            **{
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
