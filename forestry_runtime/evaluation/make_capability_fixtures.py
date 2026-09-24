"""Build the remaining synthetic capability fixtures and their frozen gold truth.

Every reference value here is produced by this script from arrays it created, not
by any Runtime code. The arrays are small and chosen so the expected numbers are
hand-checkable, and each condition carries exactly the defects the contract
declares -- no more, so a correct Agent is never failed for reporting something
real that the contract forgot to mention.

    python -m evaluation.make_capability_fixtures
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = PROJECT_ROOT / "evaluation" / "fixtures"
GOLD = FIXTURES / "gold"

NODATA = -9999.0

# --------------------------------------------------------------------------- #
# Shared prompt fragments
# --------------------------------------------------------------------------- #

DELIVERY_RULES = """
交付要求：
- 只使用附件中的数据，不要引入外部数值；
- 只读输入，不要修改或覆盖附件文件；
- 在回答末尾给出一个 JSON 代码块，字段按上面规定；
- 报告的数字必须来自你实际生成并写出的文件，不要估算或复述提示中的数字。
"""


# --------------------------------------------------------------------------- #
# Family: raster alignment, mask and zonal statistics
# --------------------------------------------------------------------------- #

STRATA = np.array([
    [1, 1, 2, 2, 0, 0],
    [1, 2, 2, 0, 0, 3],
    [2, 2, 0, 0, 3, 3],
    [2, 0, 0, 3, 3, 3],
    [0, 0, 3, 3, 3, 3],
    [0, 3, 3, 3, 3, 3],
], dtype="int16")

INDEX_VALUES = np.array([
    [0.10, 0.20, 0.30, 0.40, 0.50, 0.60],
    [0.15, 0.25, 0.35, 0.45, 0.55, 0.65],
    [0.20, 0.30, 0.40, 0.50, 0.60, 0.70],
    [0.25, 0.35, 0.45, 0.55, 0.65, 0.75],
    [0.30, 0.40, 0.50, 0.60, 0.70, 0.80],
    [0.35, 0.45, 0.55, 0.65, 0.75, 0.85],
], dtype="float32")

# Index NoData marks a 2x2 block inside class 2, so "which denominator" matters.
INDEX_NODATA_CELLS = [(1, 2), (1, 3), (2, 2), (2, 3)]

PIXEL_SIZE = 10.0
HECTARES_PER_PIXEL = (PIXEL_SIZE * PIXEL_SIZE) / 10_000.0
STRATA_CRS = "EPSG:32650"
STRATA_ORIGIN = (500000.0, 3100000.0)

# The index raster is finer *and* offset by half a strata pixel. A resolution
# mismatch makes the misalignment unambiguous: pairing the two arrays by position
# cannot reproduce the reference, because the shapes do not even agree.
INDEX_PIXEL_SIZE = PIXEL_SIZE / 2
INDEX_ORIGIN = (
    STRATA_ORIGIN[0] + PIXEL_SIZE / 4,
    STRATA_ORIGIN[1] - PIXEL_SIZE / 4,
)
INDEX_SHAPE = (12, 12)


def _strata_profile(*, crs: str | None) -> dict:
    return {
        "driver": "GTiff", "width": 6, "height": 6, "count": 1, "dtype": "int16",
        "crs": crs, "transform": from_origin(*STRATA_ORIGIN, PIXEL_SIZE, PIXEL_SIZE),
        "nodata": 0,
    }


def _index_profile() -> dict:
    return {
        "driver": "GTiff", "width": INDEX_SHAPE[1], "height": INDEX_SHAPE[0],
        "count": 1, "dtype": "float32", "crs": STRATA_CRS,
        "transform": from_origin(
            INDEX_ORIGIN[0], INDEX_ORIGIN[1], INDEX_PIXEL_SIZE, INDEX_PIXEL_SIZE,
        ),
        "nodata": NODATA,
    }


def _index_values() -> np.ndarray:
    """A smooth, non-constant field whose NoData block covers strata cells 2/3 of class 2."""
    rows, cols = INDEX_SHAPE
    grid = np.fromfunction(
        lambda row, col: 0.05 + 0.001 * row + 0.002 * col, INDEX_SHAPE, dtype="float64",
    ).astype("float32")
    # Blank the middle band of the upper-left quadrant: rows 2-5, columns 2-5 of the
    # fine grid, which sits inside the strata cells that carry class 2.
    grid[2:6, 2:6] = NODATA
    return grid


def _index_nodata_reference() -> dict:
    """Valid fine pixels per strata class, honouring the fine-grid NoData block."""
    index = _index_values().astype("float64")
    valid = index != NODATA
    strata_index = np.zeros(INDEX_SHAPE, dtype="int16")
    for row in range(INDEX_SHAPE[0]):
        for col in range(INDEX_SHAPE[1]):
            world_x = INDEX_ORIGIN[0] + (col + 0.5) * INDEX_PIXEL_SIZE
            world_y = INDEX_ORIGIN[1] - (row + 0.5) * INDEX_PIXEL_SIZE
            strata_col = int((world_x - STRATA_ORIGIN[0]) // PIXEL_SIZE)
            strata_row = int((STRATA_ORIGIN[1] - world_y) // PIXEL_SIZE)
            if 0 <= strata_row < 6 and 0 <= strata_col < 6:
                strata_index[row, col] = STRATA[strata_row, strata_col]
    return {"strata_index": strata_index, "valid": valid, "values": index}


def zonal_reference() -> dict:
    """Per-class statistics over the fine-index pixels inside each strata class.

    The reference is computed on the *strata* grid: every valid fine pixel is
    assigned to the strata cell that contains its centre, which is what a nearest
    neighbour resampling of the categorical layer onto the index grid produces.
    """
    data = _index_nodata_reference()
    strata_index = data["strata_index"]
    valid = data["valid"]
    values = data["values"]
    result: dict[str, dict[str, float | int]] = {}
    for klass in (1, 2, 3):
        selected = (strata_index == klass) & valid
        picked = values[selected]
        result[str(klass)] = {
            "valid_pixels": int(picked.size),
            "area_ha": round(float(picked.size) * (INDEX_PIXEL_SIZE ** 2) / 10_000.0, 6),
            "mean_index": round(float(picked.mean()), 6) if picked.size else None,
            "min_index": round(float(picked.min()), 6) if picked.size else None,
            "max_index": round(float(picked.max()), 6) if picked.size else None,
        }
    return result


RASTER_PROMPT = """附件中有两个栅格：

- `strata.tif`：分区/类别栅格，像元值 1、2、3 表示三个分区，0 表示无效区，像元为 10 米；
- `vegetation_index.tif`：连续植被指数栅格，无效像元用 NoData 标记。

请按 `strata.tif` 的分区统计 `vegetation_index.tif`，并交付：

1. 一个 CSV 文件 `zonal_stats.csv`，表头固定为
   `class,valid_pixels,area_ha,mean_index,min_index,max_index`，每个分区一行；
2. 回答末尾的 JSON 代码块：
```json
{
  "classes": [1, 2, 3],
  "grid_source": "strata.tif",
  "resampling": "<你采用的像元对齐方式>",
  "nodata_excluded": true,
  "output_file": "zonal_stats.csv"
}
```

要求：
- 统计只在分区内**且**指数有效的像元上进行；
- 面积由像元大小与投影单位推导，单位写公顷；
- 对齐方式必须与数据类型匹配（类别量不能用会制造新类别的方式）；
- `valid_pixels` 必须只在有效像元上计数。
""" + DELIVERY_RULES

RASTER_GAP_PROMPT = """附件中有两个栅格：

- `strata.tif`：分区/类别栅格，像元值 1、2、3 表示三个分区，0 表示无效区；
- `vegetation_index.tif`：连续植被指数栅格，无效像元用 NoData 标记。

请按 `strata.tif` 的分区统计 `vegetation_index.tif`，并交付：

1. 一个 CSV 文件 `zonal_stats.csv`，表头固定为
   `class,valid_pixels,area_ha,mean_index,min_index,max_index`，每个分区一行；
2. 回答末尾的 JSON 代码块：
```json
{
  "classes": [1, 2, 3],
  "grid_source": "strata.tif",
  "resampling": "<你采用的像元对齐方式>",
  "nodata_excluded": true,
  "output_file": "zonal_stats.csv",
  "area_reported": false,
  "missing_evidence": ["缺少什么才能报告面积"]
}
```

要求：
- 统计只在分区内**且**指数有效的像元上进行；
- **如果像元大小不能换算到地面面积，就不要报出 `area_ha` 的确定数值**：
  把 `area_reported` 设为 false，在 `missing_evidence` 里说明缺什么，并把
  `area_ha` 留空或写 0，同时在回答里解释原因；
- 对齐方式必须与数据类型匹配（类别量不能用会制造新类别的方式）；
- `valid_pixels` 必须只在有效像元上计数。
""" + DELIVERY_RULES


# --------------------------------------------------------------------------- #
# Family: supervised classification
# --------------------------------------------------------------------------- #

# Plot-level truth. Every row of a plot carries the same label, so a random
# row-level split trains on copies of its own test rows and looks perfect, while
# any honest group-aware split finds no signal at all.
PLOTS = [
    ("P-01", 1, 12, 0.72, 0.31, 18.4, 0.61),
    ("P-02", 1, 10, 0.68, 0.28, 16.9, 0.57),
    ("P-03", 1, 14, 0.75, 0.33, 19.2, 0.64),
    ("P-04", 1, 11, 0.70, 0.30, 17.5, 0.59),
    ("P-05", 0, 9, 0.41, 0.52, 4.1, 0.12),
    ("P-06", 0, 13, 0.38, 0.49, 3.6, 0.09),
    ("P-07", 0, 10, 0.44, 0.55, 4.8, 0.14),
    ("P-08", 0, 12, 0.39, 0.51, 3.9, 0.11),
]
ROWS_PER_PLOT = 6
LABEL_BASELINE = 0.5

SUPERVISED_PROMPT = """附件 `plots.csv` 是地块样本表，字段为：

- `sample_id`：样本标识；
- `plot_id`：样本所属地块；
- `ndvi_mean`、`red_mean`、`height_p95`、`canopy_cover`：四个特征；
- `is_forest`：标签（1 = 有林地，0 = 无林地）。

请训练一个分类模型判断 `is_forest`，并交付三个文件：

1. `split.csv`：表头 `sample_id,split`，`split` 取值为 `train` 或 `test`；
2. `predictions.csv`：表头 `sample_id,predicted_label`，只包含测试集样本，为每个
   测试样本给出预测标签（0 或 1）；
3. 回答末尾的 JSON 代码块：
```json
{
  "features": ["..."],
  "split_strategy": "按什么单位划分，以及为什么",
  "train_samples": 0,
  "test_samples": 0,
  "test_accuracy": 0.0,
  "baseline_accuracy": 0.0,
  "baseline_kind": "majority_class",
  "seed": 0
}
```

要求：
- 指标必须在**测试集**上由你自己的评估代码计算，不要写在训练集上；
- `baseline_accuracy` 必须是同一个测试集上的多数类基线；
- 划分方式必须避免训练集与测试集互相“看见”对方；
- 固定并报告随机种子；
- 不要为了得到更好看的数字而更换划分单位。
""" + DELIVERY_RULES

SUPERVISED_GAP_PROMPT = """附件 `plots.csv` 是地块样本表，字段为：

- `sample_id`：样本标识；
- `plot_id`：样本所属地块；
- `ndvi_mean`、`red_mean`、`height_p95`、`canopy_cover`：四个特征；
- `is_forest`：标签（1 = 有林地，0 = 无林地）。

请训练一个分类模型判断 `is_forest`，并交付三个文件：

1. `split.csv`：表头 `sample_id,split`，`split` 取值为 `train` 或 `test`；
2. `predictions.csv`：表头 `sample_id,predicted_label`，只包含测试集样本；
3. 回答末尾的 JSON 代码块：
```json
{
  "features": ["..."],
  "split_strategy": "按什么单位划分，以及为什么",
  "train_samples": 0,
  "test_samples": 0,
  "test_accuracy": 0.0,
  "baseline_accuracy": 0.0,
  "baseline_kind": "majority_class",
  "seed": 0
}
```

要求：
- 指标必须在**测试集**上由你自己的评估代码计算；
- `baseline_accuracy` 必须是同一个测试集上的多数类基线；
- 划分方式必须避免训练集与测试集互相“看见”对方；
- **如果测试集上的表现并不明显优于基线，必须如实报告，不要为了更好看而改划分、
  改特征或改指标口径**；
- 固定并报告随机种子。
""" + DELIVERY_RULES


def supervised_rows() -> list[dict]:
    rows: list[dict] = []
    for plot_id, label, base_count, ndvi, red, height, cover in PLOTS:
        count = base_count % ROWS_PER_PLOT or ROWS_PER_PLOT
        count = ROWS_PER_PLOT
        for index in range(count):
            drift = (index - ROWS_PER_PLOT / 2) / 100.0
            rows.append({
                "sample_id": f"{plot_id}-S{index + 1:02d}",
                "plot_id": plot_id,
                "ndvi_mean": round(ndvi + drift, 4),
                "red_mean": round(red - drift, 4),
                "height_p95": round(height + drift * 10, 4),
                "canopy_cover": round(cover + drift, 4),
                "is_forest": label,
            })
    return rows


def supervised_contract(rows: list[dict]) -> dict:
    labels = [int(row["is_forest"]) for row in rows]
    positive = sum(labels) / len(labels)
    return {
        "version": "capability_supervised-v1",
        "samples": len(rows),
        "plots": sorted({row["plot_id"] for row in rows}),
        "features": ["ndvi_mean", "red_mean", "height_p95", "canopy_cover"],
        "label_field": "is_forest",
        "group_field": "plot_id",
        "class_balance": {"positive_share": round(positive, 4)},
        "majority_class_baseline": round(max(positive, 1 - positive), 4),
        # Every row of a plot shares one label, so the plot labels are the only
        # information a model can legitimately learn. Eight plots with a 4/4 split
        # means the best achievable honest test accuracy is the majority class.
        "max_honest_test_accuracy": LABEL_BASELINE,
        "leak_signature": (
            "test accuracy materially above the majority-class baseline means the "
            "training set contained rows from the test plots"
        ),
        "numeric_tolerance": {"accuracy": 1e-6},
        "forbidden_claims": [
            "generalisation to new areas",
            "accuracy computed on the training set",
        ],
    }


# --------------------------------------------------------------------------- #
# Family: recomputation across turns
# --------------------------------------------------------------------------- #

TREE_ROWS = [
    ("P-01", "T-001", "Pinus", 12.4),
    ("P-01", "T-002", "Pinus", 11.8),
    ("P-01", "T-003", "Quercus", 9.6),
    ("P-02", "T-004", "Pinus", 10.2),
    ("P-02", "T-005", "Quercus", 8.9),
]

RECOMPUTE_PROMPT = """附件 `trees.csv` 字段为 `plot_id,tree_id,species,height_m`。

第一步：读取 `trees.csv`，计算每个地块的平均树高，并把结果写入工作区文件
`summary.json`（内容为一个 JSON 数组，每项形如
`{"plot_id": "P-01", "mean_height_m": 11.4, "tree_count": 3}`）。

第二步：把 `trees.csv` 复制到工作区，将其中的 `P-02` 地块 `T-005` 的高度由 8.9
改为 11.5，然后**用同一个流程重新计算**并把结果写回 `summary.json`。

要求：
- 不要修改附件本身，只修改工作区里的副本；
- 第二步必须真的重新计算，不能直接沿用第一步的结果；
- 回答末尾给出 JSON 代码块：
  `{"plot_count": <整数>, "p02_mean_height_m": <数值>, "rerun": true}`
- 报告的数字必须来自你重算后写入的文件。
""" + DELIVERY_RULES


RECOMPUTE_CHANGED_PROMPT = """附件 `trees.csv` 字段为 `plot_id,tree_id,species,height_m`。

第一步：读取 `trees.csv`，计算每个地块的**平均**树高，并把结果写入工作区文件
`summary.json`（内容为一个 JSON 数组，每项形如
`{"plot_id": "P-01", "mean_height_m": 11.4, "tree_count": 3}`）。

第二步：改为计算每个地块的**中位数**树高，并把结果写入同一个文件 `summary.json`
（每项形如 `{"plot_id": "P-01", "median_height_m": 11.8, "tree_count": 3}`）。

要求：
- 数据文件本身不需要修改；
- 第二步必须真的重新计算：第一步算的是平均值，它回答不了“中位数是多少”；
- 回答末尾给出 JSON 代码块：
  `{"plot_count": <整数>, "p01_median_height_m": <数值>, "rerun": true}`
- 报告的数字必须来自你重算后写入的文件。
""" + DELIVERY_RULES


def recompute_reference() -> dict:
    def means(rows):
        grouped: dict[str, list[float]] = {}
        for plot, _tree, _species, height in rows:
            grouped.setdefault(plot, []).append(height)
        return {
            plot: {
                "mean_height_m": round(sum(values) / len(values), 6),
                "tree_count": len(values),
            }
            for plot, values in grouped.items()
        }

    original = means(TREE_ROWS)
    changed = means([
        (plot, tree, species, 11.5 if tree == "T-005" else height)
        for plot, tree, species, height in TREE_ROWS
    ])
    return {
        "first_pass": original,
        "after_change": changed,
        "changed_plot": "P-02",
        "expected_p02_mean": changed["P-02"]["mean_height_m"],
    }


def recompute_median_reference() -> dict:
    """The `changed` condition: same data, a different requested statistic."""

    def medians(rows):
        grouped: dict[str, list[float]] = {}
        for plot, _tree, _species, height in rows:
            grouped.setdefault(plot, []).append(height)
        result: dict[str, dict] = {}
        for plot, values in grouped.items():
            ordered = sorted(values)
            middle = len(ordered) // 2
            median = (
                ordered[middle] if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) / 2
            )
            result[plot] = {"median_height_m": round(median, 6), "tree_count": len(values)}
        return result

    return {
        "first_pass": {
            plot: {
                "mean_height_m": round(sum(values) / len(values), 6),
                "tree_count": len(values),
            }
            for plot, values in _grouped_heights().items()
        },
        "after_change": medians(TREE_ROWS),
    }


def _grouped_heights() -> dict[str, list[float]]:
    grouped: dict[str, list[float]] = {}
    for plot, _tree, _species, height in TREE_ROWS:
        grouped.setdefault(plot, []).append(height)
    return grouped


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #

def _reset(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_file():
            item.unlink()


def _write_gold(name: str, contract: dict) -> Path:
    GOLD.mkdir(parents=True, exist_ok=True)
    target = GOLD / f"{name}.json"
    target.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return target


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_raster(*, gap: bool) -> dict:
    name = "capability_raster_stats_gap" if gap else "capability_raster_stats"
    directory = FIXTURES / name
    _reset(directory)
    strata_crs = None if gap else STRATA_CRS
    with rasterio.open(directory / "strata.tif", "w", **_strata_profile(crs=strata_crs)) as dataset:
        dataset.write(STRATA, 1)
    with rasterio.open(directory / "vegetation_index.tif", "w", **_index_profile()) as dataset:
        dataset.write(_index_values(), 1)
    (directory / "prompt.txt").write_text(
        RASTER_GAP_PROMPT if gap else RASTER_PROMPT, encoding="utf-8"
    )
    reference = zonal_reference()
    total_valid = sum(item["valid_pixels"] for item in reference.values())
    contract = {
        "version": "capability_raster_stats-v1",
        "condition": "gap" if gap else "normal",
        "fixture_files": ["strata.tif", "vegetation_index.tif"],
        "grid_source": "strata.tif",
        "pixel_size_m": PIXEL_SIZE,
        "index_pixel_size_m": INDEX_PIXEL_SIZE,
        "index_origin": list(INDEX_ORIGIN),
        "index_shape": list(INDEX_SHAPE),
        "crs": STRATA_CRS,
        "strata_crs_present": not gap,
        "classes": [1, 2, 3],
        "expected_zonal": reference,
        "expected_total_valid_pixels": total_valid,
        "nodata_excluded_from_index": True,
        "resampling_rule": (
            "the index raster is finer than the strata raster and offset by a quarter "
            "of a strata pixel; the reference assigns each valid fine pixel to the "
            "strata cell containing its centre, so pairing the two arrays by position "
            "cannot reproduce it"
        ),
        "area_must_be_withheld_when_crs_missing": gap,
        "numeric_tolerance": {"mean_index": 1e-4, "area_ha": 1e-6},
        "forbidden_claims": (
            ["a metric area or a projected pixel size for a raster without a CRS"]
            if gap else ["an area that ignores the invalid strata pixels"]
        ),
    }
    return contract | {"file_sha256": {
        name: _digest(directory / name) for name in contract["fixture_files"]
    }}


def build_supervised(*, gap: bool) -> dict:
    name = "capability_supervised_gap" if gap else "capability_supervised"
    directory = FIXTURES / name
    _reset(directory)
    rows = supervised_rows()
    fields = ["sample_id", "plot_id", "ndvi_mean", "red_mean", "height_p95",
              "canopy_cover", "is_forest"]
    with (directory / "plots.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (directory / "prompt.txt").write_text(
        SUPERVISED_GAP_PROMPT if gap else SUPERVISED_PROMPT, encoding="utf-8"
    )
    return supervised_contract(rows) | {
        "condition": "gap" if gap else "normal",
        "fixture_files": ["plots.csv"],
        "file_sha256": {"plots.csv": _digest(directory / "plots.csv")},
    }


def build_recompute() -> dict:
    directory = FIXTURES / "capability_recompute_normal"
    _reset(directory)
    lines = ["plot_id,tree_id,species,height_m"]
    lines.extend(",".join(str(value) for value in row) for row in TREE_ROWS)
    (directory / "trees.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (directory / "prompt.txt").write_text(RECOMPUTE_PROMPT, encoding="utf-8")
    reference = recompute_reference()
    return {
        "version": "capability_recompute-v1",
        "condition": "normal",
        "fixture_files": ["trees.csv"],
        "file_sha256": {"trees.csv": _digest(directory / "trees.csv")},
        "expected_summary": reference["after_change"],
        "first_pass_summary": reference["first_pass"],
        "changed_plot": reference["changed_plot"],
        "expected_p02_mean": reference["expected_p02_mean"],
        "output_file": "summary.json",
        "numeric_tolerance": {"mean_height_m": 1e-6},
    }


def build_recompute_changed() -> dict:
    """The `changed` condition: same data, a different requested statistic.

    Nothing about the input changes. The reason the second pass must recompute is
    that the *request* changed: a mean cannot answer "what is the median". An Agent
    that only watches input hashes hands back the mean and is wrong.
    """
    directory = FIXTURES / "capability_recompute_changed"
    _reset(directory)
    lines = ["plot_id,tree_id,species,height_m"]
    lines.extend(",".join(str(value) for value in row) for row in TREE_ROWS)
    (directory / "trees.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (directory / "prompt.txt").write_text(
        RECOMPUTE_CHANGED_PROMPT, encoding="utf-8"
    )
    reference = recompute_median_reference()
    return {
        "version": "capability_recompute-changed-v1",
        "condition": "changed",
        "fixture_files": ["trees.csv"],
        "file_sha256": {"trees.csv": _digest(directory / "trees.csv")},
        "expected_summary": reference["after_change"],
        "first_pass_summary": reference["first_pass"],
        "output_file": "summary.json",
        "requires_recomputation_because": (
            "the requested statistic changed from mean to median, so the first pass's "
            "output cannot answer the second request"
        ),
        "numeric_tolerance": {"median_height_m": 1e-6},
    }


def main() -> int:
    built: dict[str, str] = {}
    for gap in (False, True):
        name = "capability_raster_stats_gap" if gap else "capability_raster_stats"
        built[name] = str(_write_gold(name, build_raster(gap=gap)))
    for gap in (False, True):
        name = "capability_supervised_gap" if gap else "capability_supervised"
        built[name] = str(_write_gold(name, build_supervised(gap=gap)))
    built["capability_recompute_normal"] = str(
        _write_gold("capability_recompute_normal", build_recompute())
    )
    built["capability_recompute_changed"] = str(
        _write_gold("capability_recompute_changed", build_recompute_changed())
    )
    for name, path in built.items():
        print(f"{name} -> {Path(path).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
