"""Generate the frozen forestry.chm fixture and its evaluator-only gold truth.

Two fixtures cover the contract's positive and negative halves:

* ``forestry_chm``  -- DSM and DTM share CRS, grid and a declared vertical
  reference, so a CHM can be built. The heights are exact integers so the
  expected ``DSM - DTM`` is hand-checkable, one pixel is deliberately negative
  (canopy below the terrain model) and one DSM pixel is NoData.
* ``forestry_chm_gap`` -- the same DSM beside a DTM whose vertical reference is
  absent, so no evidence-backed CHM exists and the task must report the gap
  instead of fabricating one.

The DSM and DTM carry ``vertical_reference``/``vertical_units`` tags because the
tool refuses to difference two surfaces whose vertical datum it cannot verify.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin


CRS = "EPSG:32650"
TRANSFORM = from_origin(500000, 4700000, 1.0, 1.0)
VERTICAL_REFERENCE = "synthetic_ellipsoid_datum"
VERTICAL_UNITS = "m"
FILL = -9999.0

# 4x4, metres above the same datum. dsm[2][2] is below the terrain model and
# dsm[0][0] is NoData, so the CHM must keep a negative value and mask one pixel.
DSM = [
    [None, 103.0, 104.0, 102.0],
    [103.5, 105.0, 106.0, 103.0],
    [102.0, 104.0, 97.0, 102.5],
    [101.0, 102.0, 102.5, 101.5],
]
DTM = [
    [100.0, 100.0, 100.0, 100.0],
    [100.0, 100.0, 100.0, 100.0],
    [100.0, 100.0, 100.0, 100.0],
    [100.0, 100.0, 100.0, 100.0],
]

PROMPT = """附件包含同一测区的 dsm.tif 与 dtm.tif。

要求：
1. 先检查两者的坐标系、网格与垂直基准元数据，判断是否具备构建冠层高度模型
   （CHM = DSM - DTM）的条件。
2. 条件具备时，生成单波段 float32 的 CHM，保留输入网格，并为每一处负高程差
   保留真实负值，不得截断为 0。
3. 条件不具备时，不要生成任何栅格，改为说明缺失的具体证据。
4. 最后用一个 JSON 代码块报告事实。生成成功时字段为：
   {"built": true, "valid_pixel_count": <整数>, "negative_height_pixel_count": <整数>,
    "maximum_height_m": <数值>, "crs": "<字符串>", "vertical_reference": "<字符串>"}
   未生成时字段为：
   {"built": false, "missing_evidence": "<缺失的具体证据>"}
   数值必须来自你实际生成的文件，不要估算。
"""


def _grid(values: list[list[float | None]]) -> np.ndarray:
    return np.array(
        [[np.nan if value is None else float(value) for value in row] for row in values],
        dtype="float64",
    )


def expected_chm() -> list[list[float | None]]:
    dsm = _grid(DSM)
    dtm = _grid(DTM)
    valid = np.isfinite(dsm) & np.isfinite(dtm)
    result = np.full(dsm.shape, np.nan, dtype="float64")
    result[valid] = dsm[valid] - dtm[valid]
    return [
        [None if not np.isfinite(value) else round(float(value), 10) for value in row]
        for row in result
    ]


def _write_surface(path: Path, values: np.ndarray, description: str,
                   *, with_vertical_reference: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
        count=1, dtype="float32", crs=CRS, transform=TRANSFORM, nodata=FILL,
    ) as dataset:
        dataset.write(values.astype("float32"), 1)
        dataset.set_band_description(1, description)
        if with_vertical_reference:
            dataset.update_tags(
                vertical_reference=VERTICAL_REFERENCE,
                vertical_units=VERTICAL_UNITS,
            )


def build(fixtures_root: Path, gold_root: Path) -> dict:
    dsm = _grid(DSM)
    dtm = _grid(DTM)
    truth = expected_chm()

    positive = fixtures_root / "forestry_chm"
    _write_surface(positive / "dsm.tif", dsm, "dsm")
    _write_surface(positive / "dtm.tif", dtm, "dtm")
    (positive / "prompt.txt").write_text(PROMPT, encoding="utf-8")

    gap = fixtures_root / "forestry_chm_gap"
    _write_surface(gap / "dsm.tif", dsm, "dsm")
    _write_surface(
        gap / "dtm.tif", dtm, "dtm", with_vertical_reference=False
    )
    (gap / "prompt.txt").write_text(PROMPT, encoding="utf-8")

    finite = [value for row in truth for value in row if value is not None]
    gold = {
        "version": "forestry.chm-v1",
        "source": {"dsm": "dsm.tif", "dtm": "dtm.tif"},
        "crs": CRS,
        "transform": list(TRANSFORM.to_gdal()),
        "shape": [len(DSM), len(DSM[0])],
        "vertical_reference": VERTICAL_REFERENCE,
        "vertical_units": VERTICAL_UNITS,
        "dsm": DSM,
        "dtm": DTM,
        "expected_chm": truth,
        "numeric_tolerance": {"abs": 1e-5, "rel": 1e-6},
        "expected_facts": {
            "built": True,
            "valid_pixel_count": len(finite),
            "negative_height_pixel_count": sum(1 for value in finite if value < 0),
            "maximum_height_m": max(finite),
            "crs": CRS,
            "vertical_reference": VERTICAL_REFERENCE,
        },
        "gap_case": {
            "fixture": "forestry_chm_gap",
            "must_not_build": True,
            "reason": "The DTM declares no vertical reference, so DSM-DTM is not verifiable.",
        },
    }
    gold_root.mkdir(parents=True, exist_ok=True)
    target = gold_root / "forestry_chm.json"
    target.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "positive_fixture": str(positive),
        "gap_fixture": str(gap),
        "gold": str(target),
        "expected_chm": truth,
        "expected_facts": gold["expected_facts"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("evaluation/fixtures"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/fixtures/gold"))
    args = parser.parse_args()
    print(json.dumps(build(args.fixtures.resolve(), args.gold.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
