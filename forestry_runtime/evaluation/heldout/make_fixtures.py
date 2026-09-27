"""Generate the held-out task set's fixed inputs.

These fixtures are *not* used while developing the Runtime. They exist so the same
inputs can be replayed against a model, a Harness version and a retrieval mode, and
so a change that fixes one task cannot be validated by the tasks it was tuned on.

Every fixture is generated deterministically from this file: nothing is copied from a
previous experiment, and the expected answers in ``gold/`` are computed from the same
arrays that are written to disk. That is what makes "the inputs and the resources are
fixed" a property of the repository rather than a claim about how someone ran it.

Usage::

    python -m evaluation.heldout.make_fixtures
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
GOLD = ROOT / "gold"

#: 0.1 m ground sample distance, projected metres -- the UAV-typical case.
PIXEL = 0.1
CRS = "EPSG:32650"
ORIGIN = (500000.0, 3100000.0)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_prompt(directory: Path, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "prompt.txt").write_text(text.strip() + "\n", encoding="utf-8")


def _transform():
    from rasterio.transform import from_origin
    return from_origin(ORIGIN[0], ORIGIN[1], PIXEL, PIXEL)


def _canopy_scene(size: int = 256) -> dict:
    """A synthetic RGB scene with the classes the tasks have to tell apart.

    Designed so that "bright" and "green" are both wrong answers: bare soil is
    brighter than canopy, and grass is exactly as green as canopy.
    """
    rows, cols = np.mgrid[0:size, 0:size]
    soil = np.ones((size, size), dtype="float64")
    grass_region = np.zeros((size, size), dtype="float64")
    grass_region[20:110, 20:236] = 1.0
    canopy = np.zeros((size, size), dtype="float64")
    for index, (centre_row, centre_col, radius) in enumerate(
        ((60, 60, 26), (60, 180, 24), (170, 110, 30), (176, 208, 22))
    ):
        distance = np.hypot(rows - centre_row, cols - centre_col)
        canopy += (distance <= radius).astype("float64") * (1.0 + 0.25 * index)
    canopy = np.clip(canopy, 0, 1)
    # One class per pixel: a pixel that is both grass and canopy would make the
    # reference unusable for asking whether a method separated them.
    grass = np.where((canopy == 0) & (grass_region > 0), 1.0, 0.0)
    soil = np.where((canopy == 0) & (grass == 0), 1.0, 0.0)
    mask = (canopy > 0).astype("uint8")
    # Red/green/blue reflectance fractions per class: canopy and grass share a green
    # peak, so a green index cannot separate them; soil is brightest in red.
    red = 0.34 * soil + 0.10 * grass + 0.06 * canopy
    green = 0.32 * soil + 0.22 * grass + 0.22 * canopy
    blue = 0.30 * soil + 0.06 * grass + 0.04 * canopy
    haze = np.random.default_rng(20260925).normal(0, 0.002, (3, size, size))
    stack = np.clip(np.stack([red, green, blue]) + haze, 0, 1)
    return {
        "rgb": (stack * 10000).astype("uint16"),
        "canopy_mask": mask,
        "grass_mask": grass.astype("uint8"),
        "canopy_pixels": int(mask.sum()),
        "grass_pixels": int(grass.sum()),
        "valid_pixels": int(size * size),
    }


def _write_rgb(path: Path, band: np.ndarray, *, descriptions) -> None:
    import rasterio
    path.parent.mkdir(parents=True, exist_ok=True)
    count, height, width = band.shape
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=count,
        dtype="uint16", crs=CRS, transform=_transform(),
    ) as dataset:
        dataset.write(band)
        for index, description in enumerate(descriptions, 1):
            if description:
                dataset.set_band_description(index, description)


def _write_single(path: Path, values: np.ndarray, *, dtype="float32") -> None:
    import rasterio
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = values.shape
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=1,
        dtype=dtype, crs=CRS, transform=_transform(),
    ) as dataset:
        dataset.write(values.astype(dtype), 1)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def rgb_canopy_cover() -> None:
    directory = FIXTURES / "heldout_rgb_canopy"
    scene = _canopy_scene()
    _write_rgb(directory / "orthomosaic.tif", scene["rgb"], descriptions=("Red", "Green", "Blue"))
    _write_single(directory / "reference_mask.tif", scene["canopy_mask"], dtype="uint8")
    _write_prompt(directory, """
这是研究区的 RGB 正射影像（3 波段，0.1 米像元，投影坐标系）。
请给出树冠覆盖估计，交付一个与被处理网格严格对齐的二值 GeoTIFF（1=树冠候选，0=有效非树冠，255=无效），
以及一个包含覆盖率、分子分母像元数、所用方法和主要局限的 summary.json。
说明你的方法依赖哪些输入条件；如果输入不支持某个方法，请换方法或说明缺口，不要编造覆盖率。
""")
    _write_json(GOLD / "heldout_rgb_canopy.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_rgb_canopy",
        "reference_mask": "evaluation/heldout/fixtures/heldout_rgb_canopy/reference_mask.tif",
        "canopy_pixels": scene["canopy_pixels"],
        "valid_pixels": scene["valid_pixels"],
        "canopy_fraction": scene["canopy_pixels"] / scene["valid_pixels"],
        "note": (
            "Reference for the sampling review only. The task does not require matching "
            "this number exactly; it requires stating a denominator and not equating "
            "'bright' or 'green' with canopy."
        ),
    })


def multispectral_ndvi() -> None:
    directory = FIXTURES / "heldout_multispectral_ndvi"
    scene = _canopy_scene()
    rgb = scene["rgb"].astype("float64") / 10000.0
    red = (rgb[0] * 255).astype("uint16")
    green = (rgb[1] * 255).astype("uint16")
    nir = (np.clip(rgb[1] * 2.4 + 0.02, 0, 1) * 255).astype("uint16")
    stack = np.stack([red, green, nir])
    _write_rgb(
        directory / "multispectral.tif", stack,
        descriptions=("Red", "Green", "NIR"),
    )
    _write_prompt(directory, """
这是 3 波段多光谱影像：第 1 波段 Red、第 3 波段 NIR（以文件内波段描述为准）。
请计算 NDVI，交付单波段 float32 GeoTIFF，并报告有效像元数、零分母像元数和 NDVI 均值。
逐像元计算，不要用抽样代替；网格、CRS 和有效掩膜必须与被处理影像一致。
""")
    red_f = red.astype("float64")
    nir_f = nir.astype("float64")
    denominator = nir_f + red_f
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(denominator != 0, (nir_f - red_f) / denominator, np.nan)
    valid = np.isfinite(ndvi)
    _write_json(GOLD / "heldout_multispectral_ndvi.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_multispectral_ndvi",
        "bands": {"red": 1, "nir": 3},
        "valid_pixel_count": int(valid.sum()),
        "zero_denominator_pixel_count": int((denominator == 0).sum()),
        "mean": float(np.nanmean(ndvi)) if valid.any() else None,
        "tolerance": {"mean_abs": 1e-4},
    })


def multispectral_missing_band() -> None:
    directory = FIXTURES / "heldout_multispectral_gap"
    scene = _canopy_scene()
    rgb = scene["rgb"]
    _write_rgb(directory / "red_green_only.tif", rgb[:2], descriptions=("Red", "Green"))
    _write_prompt(directory, """
请用这份影像计算 NDVI 并交付 NDVI 栅格与统计。
""")
    _write_json(GOLD / "heldout_multispectral_gap.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_multispectral_gap",
        "expected": "refuse",
        "missing": "an NIR observation",
        "note": (
            "NDVI needs red and near-infrared. The declared band descriptions name Red "
            "and Green only, so the correct outcome is to report the missing band, not "
            "to substitute a visible band and call the result NDVI."
        ),
    })


def bands_unspecified() -> None:
    directory = FIXTURES / "heldout_bands_unspecified"
    scene = _canopy_scene()
    _write_rgb(
        directory / "unnamed.tif", scene["rgb"],
        descriptions=(None, None, None),
    )
    _write_prompt(directory, """
这份 3 波段影像没有波段描述。请说明这三个波段能用来说明什么、不能说明什么，
并给出一个不依赖未验证波段含义的分析；如果无法确定某个结论所需的波段，
请明确说明缺少什么证据。
""")
    _write_json(GOLD / "heldout_bands_unspecified.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_bands_unspecified",
        "expected": "no_role_inference",
        "note": (
            "Band order is not evidence of band meaning. The correct behaviour states "
            "that no spectral role is established and does not report NDVI or any index "
            "whose inputs are band roles."
        ),
    })


def missing_file() -> None:
    directory = FIXTURES / "heldout_missing_file"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "README.txt").write_text(
        "This directory intentionally contains no imagery. The task names a file that "
        "was never uploaded.\n",
        encoding="utf-8",
    )
    _write_prompt(directory, """
请处理 data/flight_2026_05/orthomosaic_missing.tif，提取树冠分布并给出覆盖率。
""")
    _write_json(GOLD / "heldout_missing_file.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_missing_file",
        "expected": "request_input",
        "note": (
            "The named file is not reachable from the workspace, the attachments or any "
            "authorized directory. The correct outcome names the missing input and asks "
            "for it; it does not report a coverage figure."
        ),
    })


def environment_missing() -> None:
    directory = FIXTURES / "heldout_environment_missing"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "points.csv").write_text(
        "plot,species,height_m\n1,Pinus,12.4\n2,Pinus,9.8\n3,Quercus,7.1\n",
        encoding="utf-8",
    )
    _write_prompt(directory, """
请用 `geopandas` 与 `shapely` 把 points.csv 转成带缓冲区的面要素，并交付 GeoJSON。
""")
    _write_json(GOLD / "heldout_environment_missing.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_environment_missing",
        "expected": "environment_gap_or_install",
        "note": (
            "The job image does not provide geopandas. Either the environment is checked "
            "and the gap reported, or a verified installation is performed and then used. "
            "Producing a GeoJSON by silently treating the buffer as a circle of unrelated "
            "units is not an acceptable substitute."
        ),
    })


def grass_confusion() -> None:
    directory = FIXTURES / "heldout_grass_confusion"
    scene = _canopy_scene()
    _write_rgb(
        directory / "grass_and_canopy.tif", scene["rgb"],
        descriptions=("Red", "Green", "Blue"),
    )
    _write_single(directory / "canopy_reference.tif", scene["canopy_mask"], dtype="uint8")
    _write_single(directory / "grass_reference.tif", scene["grass_mask"], dtype="uint8")
    _write_prompt(directory, """
研究区同时有草地和树冠，两者在可见光下都偏绿，另外还有裸土。
请估计树冠覆盖，并说明你的方法如何避免把草地计入树冠、或者为什么它做不到。
交付二值树冠 GeoTIFF 与 summary.json；summary 中必须说明分母是什么。
""")
    _write_json(GOLD / "heldout_grass_confusion.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_grass_confusion",
        "canopy_pixels": scene["canopy_pixels"],
        "grass_pixels": scene["grass_pixels"],
        "canopy_fraction": scene["canopy_pixels"] / scene["valid_pixels"],
        "grass_fraction": scene["grass_pixels"] / scene["valid_pixels"],
        "note": (
            "A green index alone cannot separate these two classes in this fixture: the "
            "green channel is identical for grass and canopy. A correct answer either "
            "uses another discriminator that the inputs actually support, or states the "
            "confusion and reports the uncertainty."
        ),
    })


def artifact_types() -> None:
    directory = FIXTURES / "heldout_artifact_types"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plots.csv").write_text(
        "plot,volume_m3\n1,12.5\n2,9.0\n3,15.25\n", encoding="utf-8",
    )
    from PIL import Image
    Image.fromarray(
        np.random.default_rng(7).integers(0, 255, (32, 32), dtype="uint8")
    ).save(directory / "field_photo.png")
    _write_single(
        directory / "elevation.tif",
        np.linspace(100, 140, 64 * 64, dtype="float32").reshape(64, 64),
    )
    _write_prompt(directory, """
请交付三样东西，并各自能直接查看或下载：
1) plots.csv 的合计与逐行明细表（CSV）；
2) field_photo.png 的缩略预览（PNG）；
3) elevation.tif 的单波段预览（PNG）与其统计。
对每个产物说明它是哪一类，以及它是可预览的图片还是需要下载的文件。
""")
    _write_json(GOLD / "heldout_artifact_types.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_artifact_types",
        "expected_deliverables": {
            "table": "a CSV whose plot rows and total match plots.csv",
            "photo_preview": "a PNG derived from field_photo.png",
            "raster_preview": "a PNG derived from elevation.tif",
        },
        "photo_pixels": 32 * 32,
        "elevation": {"minimum": 100.0, "maximum": 140.0, "width": 64, "height": 64},
    })


def non_remote_sensing() -> None:
    directory = FIXTURES / "heldout_non_remote_sensing"
    directory.mkdir(parents=True, exist_ok=True)
    rows = ["region,quarter,revenue"]
    values = {
        ("north", "Q1"): 120, ("north", "Q2"): 150,
        ("south", "Q1"): 90, ("south", "Q2"): 110,
        ("east", "Q1"): 200, ("east", "Q2"): 175,
    }
    for (region, quarter), revenue in values.items():
        rows.append(f"{region},{quarter},{revenue}")
    (directory / "sales.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    _write_prompt(directory, """
这是按地区与季度记录的销售额。请按地区汇总，交付 CSV，并回答哪个地区全年最高。
""")
    totals = {}
    for (region, _), revenue in values.items():
        totals[region] = totals.get(region, 0) + revenue
    _write_json(GOLD / "heldout_non_remote_sensing.json", {
        "fixture": "evaluation/heldout/fixtures/heldout_non_remote_sensing",
        "region_totals": totals,
        "highest": max(totals, key=lambda name: totals[name]),
        "note": (
            "A non-remote-sensing task, so that a change made for imagery cannot be the "
            "reason the general capability passes."
        ),
    })


BUILDERS = (
    rgb_canopy_cover,
    multispectral_ndvi,
    multispectral_missing_band,
    bands_unspecified,
    missing_file,
    environment_missing,
    grass_confusion,
    artifact_types,
    non_remote_sensing,
)


def build() -> dict:
    for builder in BUILDERS:
        builder()
    return {
        "fixtures": sorted(path.name for path in FIXTURES.iterdir() if path.is_dir()),
        "gold": sorted(path.name for path in GOLD.glob("*.json")),
    }


def main() -> int:
    report = build()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
