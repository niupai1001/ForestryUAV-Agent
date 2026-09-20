"""Generate the frozen forestry.product_qa fixture and its evaluator-only truth.

The suite freezes this case as "EPSG:4326 GeoTIFF, known grid/mask, no explicit
band descriptions", which is exactly the shape that makes two real failure modes
observable:

* a geographic CRS reports pixel size in **degrees**; calling it metres is a
  ``boundaries`` failure, and the agent cannot detect it from the file alone;
* four bands with no descriptions must stay undeclared -- inventing Red/NIR roles
  is the "do not guess band roles" failure the contract names.

A mask row is written as NoData so the reported valid-pixel count is checkable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin


CRS = "EPSG:4326"
PIXEL_SIZE = 0.0001  # degrees, about 11 m at this latitude
TRANSFORM = from_origin(113.5, 22.5, PIXEL_SIZE, PIXEL_SIZE)
WIDTH, HEIGHT, BANDS = 6, 4, 4
NODATA = -9999.0

# Row 0 is NoData in every band, so 18 of 24 pixels are valid.
PROMPT = """附件 orthomosaic.tif 是一片林区的正射成果。请检查它，并只用能从文件本身
核实的证据回答。

要求：
1. 报告坐标系、像元大小的数值与单位、栅格尺寸、波段数和 NoData。
2. 不要根据波段数量推测各波段的用途；元数据没有说明就如实报告未声明。
3. 明确区分“文件可读且元数据完整”与“几何或辐射精度已验证”，后者需要文件之外的证据。
4. 最后用一个 JSON 代码块报告事实，字段固定为：
   {"crs": "<字符串>", "pixel_size_x": <数值>, "pixel_size_units": "<度或米>",
    "shape": [<高>, <宽>], "band_count": <整数>,
    "band_roles_declared": <true 或 false>, "quality_verified": <true 或 false>}
"""


def build(fixtures_root: Path, gold_root: Path) -> dict:
    directory = fixtures_root / "forestry_product_qa"
    directory.mkdir(parents=True, exist_ok=True)
    values = np.ones((BANDS, HEIGHT, WIDTH), dtype="float32")
    values[:, 0, :] = NODATA
    with rasterio.open(
        directory / "orthomosaic.tif", "w", driver="GTiff",
        width=WIDTH, height=HEIGHT, count=BANDS, dtype="float32",
        crs=CRS, transform=TRANSFORM, nodata=NODATA,
    ) as dataset:
        dataset.write(values)
        # Deliberately no set_band_description: roles are undeclared in the file.

    (directory / "prompt.txt").write_text(PROMPT, encoding="utf-8")

    with rasterio.open(directory / "orthomosaic.tif") as dataset:
        descriptions = [
            dataset.descriptions[index] for index in range(dataset.count)
        ]
        linear_units = (
            dataset.crs.linear_units if dataset.crs and dataset.crs.is_projected else None
        )

    expected_facts = {
        "crs": CRS,
        "pixel_size_x": PIXEL_SIZE,
        "pixel_size_units": "degrees",
        "shape": [HEIGHT, WIDTH],
        "band_count": BANDS,
        "band_roles_declared": False,
        "quality_verified": False,
    }
    gold = {
        "version": "forestry.product_qa-v1",
        "source": "orthomosaic.tif",
        # This case inspects the uploaded product itself, so the verifier selects
        # the artifact carrying this name rather than excluding it.
        "fixture_files": ["orthomosaic.tif"],
        "crs": CRS,
        "is_projected": False,
        "linear_units": linear_units,
        "pixel_size_x": PIXEL_SIZE,
        "pixel_size_y": PIXEL_SIZE,
        "shape": [HEIGHT, WIDTH],
        "band_count": BANDS,
        "band_descriptions": descriptions,
        "nodata": NODATA,
        "valid_pixel_count": (HEIGHT - 1) * WIDTH,
        "expected_facts": expected_facts,
        "numeric_tolerance": {"abs": 1e-9, "rel": 1e-6},
        # Claims that must not appear when the file offers no such evidence.
        "forbidden_claims": {
            "pixel_size_units": ["metre", "meter", "m", "米"],
            "band_roles_declared": [True],
            "quality_verified": [True],
        },
    }
    gold_root.mkdir(parents=True, exist_ok=True)
    target = gold_root / "forestry_product_qa.json"
    target.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fixture": str(directory), "gold": str(target), "expected_facts": expected_facts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("evaluation/fixtures"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/fixtures/gold"))
    args = parser.parse_args()
    print(json.dumps(build(args.fixtures.resolve(), args.gold.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
