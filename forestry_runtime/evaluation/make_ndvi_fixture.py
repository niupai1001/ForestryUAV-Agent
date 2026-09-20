"""Generate the frozen forestry.ndvi fixture and its evaluator-only gold truth.

The arrays are chosen so every expected NDVI value is exact and hand-checkable:

    Red = [[1, 2], [0, 0]]      NIR = [[3, 2], [0, 1]]      scale=1, offset=0

    (1,3) -> (3-1)/(3+1) = 0.5
    (2,2) -> (2-2)/(2+2) = 0.0
    (0,0) -> denominator 0  -> invalid (zero denominator)
    (0,1) -> (1-0)/(1+0) = 1.0

So valid_pixel_count = 3, zero_denominator_pixel_count = 1, mean = 0.5,
and the mask marks index (row=1, col=0) as NaN.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin


RED = [[1, 2], [0, 0]]
NIR = [[3, 2], [0, 1]]
GREEN = [[1, 1], [1, 1]]
CRS = "EPSG:32650"
TRANSFORM = from_origin(500000, 3100000, 0.03, 0.03).to_gdal()
SCALE = 1.0
OFFSET = 0.0
ABS_TOL = 1e-6
REL_TOL = 1e-6

PROMPT = """请对附件 forest.tif 计算 NDVI 并交付结果栅格。

要求：
1. 使用波段元数据判断 Red 与 NIR 的角色，不要按波段序号猜测。
2. 输出为与输入同一网格、同一坐标系、同一仿射变换的单波段 float32 GeoTIFF。
3. 分母为零的像元必须标为无效（不写入 0）。
4. 最后用一个 JSON 代码块报告统计事实，字段固定为：
   {"valid_pixel_count": <整数>, "zero_denominator_pixel_count": <整数>,
    "mean": <数值>, "crs": "<字符串>", "output_shape": [<高>, <宽>]}
   数值必须来自你实际生成的文件，不要估算。
"""


def expected_ndvi() -> list[list[float | None]]:
    red = np.array(RED, dtype="float64")
    nir = np.array(NIR, dtype="float64")
    red = red * SCALE + OFFSET
    nir = nir * SCALE + OFFSET
    denominator = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        values = (nir - red) / denominator
    return [
        [
            None if not np.isfinite(value) else round(float(value), 12)
            for value in row
        ]
        for row in values
    ]


def _write_tiff(path: Path) -> None:
    red = np.array(RED, dtype="uint16")
    with rasterio.open(
        path, "w", driver="GTiff", width=2, height=2, count=3,
        dtype="uint16", crs=CRS, transform=from_origin(500000, 3100000, 0.03, 0.03),
    ) as dataset:
        dataset.write(red, 1)
        dataset.write(np.array(GREEN, dtype="uint16"), 2)
        dataset.write(np.array(NIR, dtype="uint16"), 3)
        dataset.set_band_description(1, "Red")
        dataset.set_band_description(2, "Green")
        dataset.set_band_description(3, "NIR")


def build(fixtures_root: Path, gold_root: Path) -> dict:
    case_dir = fixtures_root / "forestry_ndvi"
    case_dir.mkdir(parents=True, exist_ok=True)
    _write_tiff(case_dir / "forest.tif")
    (case_dir / "prompt.txt").write_text(PROMPT, encoding="utf-8")

    truth = expected_ndvi()
    gold = {
        "version": "forestry.ndvi-v1",
        "source": "forest.tif",
        "band_roles": {"red": 1, "nir": 3},
        "scale": SCALE,
        "offset": OFFSET,
        "crs": CRS,
        "transform": list(TRANSFORM),
        "shape": [2, 2],
        "red": RED,
        "nir": NIR,
        "expected_ndvi": truth,
        "numeric_tolerance": {"abs": ABS_TOL, "rel": REL_TOL},
        "expected_facts": {
            "valid_pixel_count": 3,
            "zero_denominator_pixel_count": 1,
            "mean": 0.5,
            "output_shape": [2, 2],
        },
    }
    gold_root.mkdir(parents=True, exist_ok=True)
    target = gold_root / "forestry_ndvi.json"
    target.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fixture": str(case_dir), "gold": str(target), "expected_ndvi": truth}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("evaluation/fixtures"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/fixtures/gold"))
    args = parser.parse_args()
    result = build(args.fixtures.resolve(), args.gold.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
