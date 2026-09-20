"""多波段正射影像的真彩色与假彩色预览工具。"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

from .odm import gdal_environment


def _preview_environment(
    odm_home: Path,
    proj_data_directory: Path | None,
) -> dict[str, str]:
    environment = gdal_environment(odm_home, proj_data_directory)
    environment["GDAL_PAM_ENABLED"] = "NO"
    return environment


def _sample_band_values(
    orthophoto: Path,
    band_index: int,
    odm_home: Path,
    proj_data_directory: Path | None,
    width: int = 192,
) -> list[float]:
    """把一个波段降采样为 XYZ，并返回有限数值。"""
    gdal_translate = (
        Path(odm_home) / "SuperBuild" / "install" / "bin" / "gdal_translate.exe"
    )
    result = subprocess.run(
        [
            str(gdal_translate),
            "-q",
            "-of",
            "XYZ",
            "-outsize",
            str(width),
            "0",
            "-r",
            "nearest",
            "-b",
            str(band_index),
            str(Path(orthophoto)),
            "/vsistdout/",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=_preview_environment(odm_home, proj_data_directory),
    )
    values: list[float] = []
    for line in result.stdout.splitlines():
        try:
            value = float(line.rsplit(maxsplit=1)[-1])
        except (IndexError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("不能计算空样本的分位数。")
    position = (len(sorted_values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _attach_display_scales(
    info: dict[str, Any],
    orthophoto: Path,
    odm_home: Path,
    proj_data_directory: Path | None,
) -> None:
    """按 Alpha 掩膜后的有效像元分位数写入显示尺度。"""
    bands = info.get("bands", [])
    alpha_index = next(
        (
            index
            for index, band in enumerate(bands, start=1)
            if str(band.get("colorInterpretation") or "").lower() == "alpha"
        ),
        None,
    )
    alpha_values = (
        _sample_band_values(
            orthophoto,
            alpha_index,
            odm_home,
            proj_data_directory,
        )
        if alpha_index is not None
        else None
    )
    for index, band in enumerate(bands, start=1):
        if index == alpha_index:
            continue
        values = _sample_band_values(
            orthophoto,
            index,
            odm_home,
            proj_data_directory,
        )
        if alpha_values is not None and len(alpha_values) == len(values):
            values = [
                value
                for value, alpha in zip(values, alpha_values)
                if alpha > 0
            ]
        nodata = band.get("noDataValue")
        if nodata is not None:
            values = [value for value in values if value != nodata]
        if not values:
            continue
        values.sort()
        low = _percentile(values, 0.02)
        high = _percentile(values, 0.98)
        if high > low:
            band["displayScale"] = [low, high]


def read_raster_statistics(
    orthophoto: Path,
    odm_home: Path,
    proj_data_directory: Path | None = None,
) -> dict[str, Any]:
    """读取波段描述和统计值。"""
    gdalinfo = (
        Path(odm_home) / "SuperBuild" / "install" / "bin" / "gdalinfo.exe"
    )
    result = subprocess.run(
        [str(gdalinfo), "-json", "-stats", str(Path(orthophoto))],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=_preview_environment(odm_home, proj_data_directory),
    )
    info = json.loads(result.stdout)
    _attach_display_scales(
        info,
        Path(orthophoto),
        odm_home,
        proj_data_directory,
    )
    return info


def display_scale(band: dict[str, Any]) -> tuple[float, float]:
    """根据波段统计值生成仅用于显示的拉伸范围。"""
    sampled = band.get("displayScale")
    if (
        isinstance(sampled, list)
        and len(sampled) == 2
        and float(sampled[1]) > float(sampled[0])
    ):
        return float(sampled[0]), float(sampled[1])
    metadata = band.get("metadata", {}).get("", {})

    def number(key: str, fallback: str) -> float | None:
        value = band.get(key)
        if value is None:
            value = metadata.get(fallback)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    minimum = number("minimum", "STATISTICS_MINIMUM")
    maximum = number("maximum", "STATISTICS_MAXIMUM")
    mean = number("mean", "STATISTICS_MEAN")
    stddev = number("stdDev", "STATISTICS_STDDEV")
    if minimum is None or maximum is None or maximum <= minimum:
        return 0.0, 1.0
    low, high = minimum, maximum
    if mean is not None and stddev is not None and stddev > 0:
        low = max(minimum, mean - 2.0 * stddev)
        high = min(maximum, mean + 2.5 * stddev)
    if maximum <= 2.0:
        low = max(0.0, low)
    return (minimum, maximum) if high <= low else (low, high)


def generate_preview(
    orthophoto: Path,
    output: Path,
    channels: tuple[str, str, str],
    odm_home: Path,
    width: int = 1600,
    raster_info: dict[str, Any] | None = None,
    proj_data_directory: Path | None = None,
) -> Path:
    """按波段名称生成一个 PNG 合成图。"""
    orthophoto = Path(orthophoto)
    output = Path(output)
    if not orthophoto.is_file():
        raise FileNotFoundError(f"正射影像不存在：{orthophoto}")
    if width < 256:
        raise ValueError("width 不能小于 256。")
    info = raster_info or read_raster_statistics(
        orthophoto,
        odm_home,
        proj_data_directory,
    )
    bands = info.get("bands", [])

    def band_name(band: dict[str, Any]) -> str:
        return str(
            band.get("description") or band.get("colorInterpretation") or ""
        ).strip()

    lookup = {
        band_name(band).lower(): (index, band)
        for index, band in enumerate(bands, start=1)
    }
    missing = [name for name in channels if name.lower() not in lookup]
    if missing:
        raise RuntimeError(
            f"缺少预览波段 {missing}；现有波段为 {sorted(lookup)}。"
        )

    alpha_index = next(
        (
            index
            for index, band in enumerate(bands, start=1)
            if str(band.get("colorInterpretation") or "").lower() == "alpha"
        ),
        None,
    )
    gdal_translate = (
        Path(odm_home) / "SuperBuild" / "install" / "bin" / "gdal_translate.exe"
    )
    command = [
        str(gdal_translate),
        "-of",
        "PNG",
        "-ot",
        "Byte",
        "-outsize",
        str(width),
        "0",
    ]
    for output_index, name in enumerate(channels, start=1):
        source_index, band = lookup[name.lower()]
        low, high = display_scale(band)
        command.extend(["-b", str(source_index)])
        command.extend(
            [
                f"-scale_{output_index}",
                str(low),
                str(high),
                "0",
                "255",
            ]
        )
    if alpha_index is not None:
        command.extend(["-b", str(alpha_index), "-scale_4", "0", "255", "0", "255"])
    output.parent.mkdir(parents=True, exist_ok=True)
    command.extend([str(orthophoto), str(output)])
    subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=_preview_environment(odm_home, proj_data_directory),
    )
    with Image.open(output) as image:
        image.verify()
    return output


def generate_standard_previews(
    orthophoto: Path,
    output_directory: Path,
    odm_home: Path,
    width: int = 1600,
    proj_data_directory: Path | None = None,
) -> dict[str, str]:
    """生成 RGB 真彩色；存在 NIR 时再生成假彩色预览。"""
    info = read_raster_statistics(orthophoto, odm_home, proj_data_directory)
    output_directory = Path(output_directory)
    true_color = generate_preview(
        orthophoto,
        output_directory / "orthophoto_true_color.png",
        ("Red", "Green", "Blue"),
        odm_home,
        width,
        raster_info=info,
        proj_data_directory=proj_data_directory,
    )
    previews = {"true_color": str(true_color)}
    names = {
        str(band.get("description") or band.get("colorInterpretation") or "")
        .strip()
        .lower()
        for band in info.get("bands", [])
    }
    if {"nir", "red", "green"}.issubset(names):
        false_color = generate_preview(
            orthophoto,
            output_directory / "orthophoto_false_color.png",
            ("NIR", "Red", "Green"),
            odm_home,
            width,
            raster_info=info,
            proj_data_directory=proj_data_directory,
        )
        previews["false_color"] = str(false_color)
    return previews
