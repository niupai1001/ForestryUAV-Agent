"""Build the capability NDVI and CHM fixtures, including their gap conditions.

These two families deliberately reuse the mature NDVI and CHM verification logic
from ``evaluation/verify/ndvi.py`` and ``evaluation/verify/chm.py``. Only the data
and the prompts are new, so the comparison remains pixel-by-pixel against an
independently recomputed reference.

Gap conditions remove one piece of metadata the honest answer depends on:

* NDVI ``gap`` -- the band descriptions are absent, so the red/NIR roles cannot be
  established. Guessing a band order produces a plausible raster that means nothing;
* CHM ``gap`` -- the DTM carries no vertical reference, so ``DSM - DTM`` is not a
  height difference in metres.

    python -m evaluation.make_capability_ndvi_chm
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = PROJECT_ROOT / "evaluation" / "fixtures"
GOLD = FIXTURES / "gold"

# A deliberately non-trivial 4x4 scene: two zero-denominator pixels, one NoData
# pixel, and a scale/offset that must be applied before the ratio.
RED = [
    [120, 240, 0, 60],
    [300, 0, 150, 90],
    [210, 330, 30, 180],
    [45, 255, 105, 195],
]
NIR = [
    [480, 240, 0, 300],
    [300, 0, 450, 210],
    [30, 330, 390, 180],
    [255, 45, 315, 195],
]
SCALE = 1e-4
OFFSET = 0.0
CRS = "EPSG:32650"
TRANSFORM = from_origin(500000, 3100000, 0.5, 0.5)
INDEX_NODATA = (2, 3)


def _expected_ndvi() -> list[list[float | None]]:
    red = np.array(RED, dtype="float64") * SCALE + OFFSET
    nir = np.array(NIR, dtype="float64") * SCALE + OFFSET
    denominator = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        values = (nir - red) / denominator
    values[INDEX_NODATA] = np.nan
    return [
        [None if not np.isfinite(value) else round(float(value), 10) for value in row]
        for row in values
    ]


def _write_index(path: Path, *, gap: bool) -> None:
    """One 3-band uint16 raster; the gap condition carries no band descriptions."""
    profile = {
        "driver": "GTiff", "width": 4, "height": 4, "count": 3, "dtype": "uint16",
        "crs": CRS, "transform": TRANSFORM, "nodata": 0,
    }
    red = np.array(RED, dtype="uint16")
    red[INDEX_NODATA] = 0
    nir = np.array(NIR, dtype="uint16")
    nir[INDEX_NODATA] = 0
    green = np.full((4, 4), 100, dtype="uint16")
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(red, 1)
        dataset.write(green, 2)
        dataset.write(nir, 3)
        if not gap:
            dataset.set_band_description(1, "Red")
            dataset.set_band_description(2, "Green")
            dataset.set_band_description(3, "NIR")
        dataset.update_tags(
            SCALE_FACTOR="0.0001", OFFSET="0.0",
        )


NDVI_PROMPT = """附件 `scene.tif` 有三个波段。波段角色记录在波段描述里。

请计算 NDVI 并交付：

1. 一个与输入**同一网格、同一坐标系、同一仿射变换**的单波段 float32 GeoTIFF；
2. 回答末尾的 JSON 代码块：
```json
{
  "red_band": 0,
  "nir_band": 0,
  "band_roles_source": "<你依据什么确定波段角色>",
  "valid_pixel_count": 0,
  "zero_denominator_pixel_count": 0,
  "mean": 0.0,
  "crs": "<字符串>",
  "output_shape": [0, 0]
}
```

要求：
- 波段角色必须来自文件元数据，不能按波段序号猜测；
- 整数存储必须先按记录的比例因子换算为反射率再计算；
- 分母为零的像元与 NoData 像元都必须在输出中保持无效，不能写 0；
- 报告的数字必须来自你实际生成的文件。
"""

NDVI_GAP_PROMPT = """附件 `scene.tif` 有三个波段，但**文件里没有记录各波段的物理含义**。

请判断能否计算 NDVI，并交付：

1. 如果波段角色无法确定，就**不要**输出 NDVI 栅格；改为在回答末尾给出 JSON 代码块：
```json
{
  "ndvi_computed": false,
  "missing_evidence": ["缺少什么才能确定波段角色"],
  "risk_if_guessed": "<如果按序号猜测会有什么后果>"
}
```
2. 如果波段角色确实能够确定，就按正常流程交付 NDVI 栅格，并把 `ndvi_computed`
   设为 true，同时说明你的依据。

要求：
- 不得按波段序号或常见约定猜测红光与近红外；
- 不要为了让任务看起来完成而输出一个含义不明的栅格。
"""


def build_ndvi(*, gap: bool) -> dict:
    suffix = "_gap" if gap else ""
    directory = FIXTURES / f"capability_ndvi{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_file():
            item.unlink()
    _write_index(directory / "scene.tif", gap=gap)
    (directory / "prompt.txt").write_text(
        NDVI_GAP_PROMPT if gap else NDVI_PROMPT, encoding="utf-8"
    )
    truth = _expected_ndvi()
    valid = [value for row in truth for value in row if value is not None]
    red = np.array(RED, dtype="float64")
    nir = np.array(NIR, dtype="float64")
    zero_denominator = int(np.count_nonzero(red + nir == 0))
    contract = {
        "version": f"capability_ndvi{'-gap' if gap else ''}-v1",
        "condition": "gap" if gap else "normal",
        "source": "scene.tif",
        "fixture_files": ["scene.tif"],
        "band_roles": None if gap else {"red": 1, "nir": 3},
        "band_descriptions_present": not gap,
        "scale": SCALE,
        "offset": OFFSET,
        "crs": CRS,
        "transform": list(TRANSFORM.to_gdal()),
        "shape": [4, 4],
        "red": RED,
        "nir": NIR,
        "nodata_index_cell": list(INDEX_NODATA),
        "expected_ndvi": truth,
        "numeric_tolerance": {"abs": 1e-6, "rel": 1e-6},
        "expected_facts": {
            "valid_pixel_count": len(valid),
            "zero_denominator_pixel_count": zero_denominator,
            "mean": round(float(np.mean(valid)), 10),
            "output_shape": [4, 4],
        },
        "gap_keywords": ["波段", "band", "描述", "description", "元数据", "metadata"],
        "file_sha256": {
            "scene.tif": hashlib.sha256((directory / "scene.tif").read_bytes()).hexdigest()
        },
    }
    return contract


DSM = np.array([
    [31.2, 30.8, 33.1, 32.4, 30.1],
    [30.5, 34.0, 33.6, 31.9, 30.9],
    [29.8, 31.5, 35.2, 34.4, 32.0],
    [30.2, 30.9, 32.7, 33.8, 31.4],
    [30.0, 30.4, 31.1, 31.8, -9999.0],
], dtype="float32")
# Two cells where the ground surface sits *above* the surface model. Those are real
# registration or ground-classification artefacts, and the contract requires them to
# survive into the CHM instead of being clipped to zero.
DTM = np.array([
    [28.4, 28.1, 29.0, 28.6, 28.2],
    [28.3, 28.9, 29.1, 28.5, 28.4],
    [27.9, 28.2, 29.4, 29.0, 28.7],
    [30.6, 30.9, 28.8, 29.2, 28.5],
    [27.8, 28.1, 28.4, 28.6, -9999.0],
], dtype="float32")
CHM_NODATA_CELLS = [(4, 4)]

CHM_PROMPT = """附件 `dsm.tif` 与 `dtm.tif` 是同一网格的高程栅格，单位与垂直基准记录在文件里。

请生成冠层高度模型（CHM）并交付：

1. 一个与 DSM **同一网格、同一坐标系、同一仿射变换**的单波段 float32 GeoTIFF；
2. 回答末尾的 JSON 代码块：
```json
{
  "built": true,
  "valid_pixel_count": 0,
  "negative_height_pixel_count": 0,
  "maximum_height_m": 0.0,
  "crs": "<字符串>",
  "vertical_reference": "<字符串>"
}
```

要求：
- 差值的负值必须保留并如实报告比例，不得静默截断为零；
- NoData 像元在输出中必须仍然无效；
- 报告的数字必须来自你实际生成的文件。
"""

CHM_GAP_PROMPT = """附件 `dsm.tif` 与 `dtm.tif` 是同一网格的高程栅格。

请判断能否生成冠层高度模型（CHM）并交付：

1. 如果**无法确定两个栅格的高程单位或垂直基准是否一致**，就不要输出 CHM 栅格；
   改为在回答末尾给出 JSON 代码块：
```json
{
  "built": false,
  "missing_evidence": ["缺少什么才能确信差值表示真实高度差"],
  "risk_if_ignored": "<忽略这一点的后果>"
}
```
2. 如果你能证明两者可比，就按正常流程交付 CHM 栅格并把 `built` 设为 true，
   同时给出你的依据。

要求：
- 不得把“文件存在且可读”当作单位或基准一致的证据；
- 不要为了完成任务而输出一个单位不明的差值栅格。
"""


def _write_surface(path: Path, values: np.ndarray, *, vertical_reference: str | None) -> None:
    profile = {
        "driver": "GTiff", "width": values.shape[1], "height": values.shape[0],
        "count": 1, "dtype": "float32", "crs": CRS,
        "transform": from_origin(400000, 3200000, 1.0, 1.0), "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(values, 1)
        dataset.update_tags(ELEVATION_UNITS="metre")
        if vertical_reference:
            dataset.update_tags(VERTICAL_DATUM=vertical_reference)


def build_chm(*, gap: bool) -> dict:
    suffix = "_gap" if gap else ""
    directory = FIXTURES / f"capability_chm{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    for item in directory.iterdir():
        if item.is_file():
            item.unlink()
    _write_surface(
        directory / "dsm.tif", DSM,
        vertical_reference=None if gap else "EGM96",
    )
    _write_surface(
        directory / "dtm.tif", DTM,
        vertical_reference=None if gap else "EGM96",
    )
    (directory / "prompt.txt").write_text(
        CHM_GAP_PROMPT if gap else CHM_PROMPT, encoding="utf-8"
    )
    chm = (DSM.astype("float64") - DTM.astype("float64"))
    mask = np.zeros(chm.shape, dtype=bool)
    for row, col in CHM_NODATA_CELLS:
        mask[row, col] = True
    values = chm[~mask]
    contract = {
        "version": f"capability_chm{'-gap' if gap else ''}-v1",
        "condition": "gap" if gap else "normal",
        "source": ["dsm.tif", "dtm.tif"],
        "fixture_files": ["dsm.tif", "dtm.tif"],
        "crs": CRS,
        "transform": list(from_origin(400000, 3200000, 1.0, 1.0).to_gdal()),
        "shape": [5, 5],
        "vertical_reference_present": not gap,
        "expected_chm": [
            [None if mask[row, col] else round(float(chm[row, col]), 6)
             for col in range(chm.shape[1])]
            for row in range(chm.shape[0])
        ],
        "numeric_tolerance": {"abs": 1e-5, "rel": 1e-6},
        "expected_facts": {
            "valid_pixel_count": int(values.size),
            "negative_height_pixel_count": int(np.count_nonzero(values < 0)),
            "maximum_height_m": round(float(values.max()), 6),
            "crs": CRS,
        },
        "files_exist_is_not_unit_evidence": True,
        "gap_case": {
            "reason": "neither surface records a vertical datum, so a difference "
                      "between them is not established as a height difference",
        },
        "file_sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("dsm.tif", "dtm.tif")
        },
    }
    return contract


def main() -> int:
    for gap in (False, True):
        name = f"capability_ndvi{'_gap' if gap else ''}"
        GOLD.mkdir(parents=True, exist_ok=True)
        (GOLD / f"{name}.json").write_text(
            json.dumps(build_ndvi(gap=gap), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"{name} -> {name}.json")
    for gap in (False, True):
        name = f"capability_chm{'_gap' if gap else ''}"
        (GOLD / f"{name}.json").write_text(
            json.dumps(build_chm(gap=gap), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"{name} -> {name}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
