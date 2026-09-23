"""影像枚举、EXIF/XMP 读取、航片分组和基本检查。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff", ".dng"}
XMP_ATTRIBUTE = re.compile(
    rb'(?:drone-dji|Camera):([A-Za-z0-9_-]+)="([^"]*)"'
)


def list_images(directory: Path, recursive: bool = False) -> list[Path]:
    """列出支持的影像；默认只读取目录顶层。"""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"影像目录不存在：{directory}")
    candidates: Iterable[Path]
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        path
        for path in candidates
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def read_xmp(path: Path) -> dict[str, str]:
    """读取影像内嵌的 DJI XMP 属性。"""
    raw = Path(path).read_bytes()
    return {
        key.decode("ascii"): value.decode("utf-8", errors="replace")
        for key, value in XMP_ATTRIBUTE.findall(raw)
    }


def read_exif(path: Path) -> dict[str, str | None]:
    """读取基本相机型号和拍摄时间。"""
    with Image.open(path) as image:
        exif = image.getexif()
        make = exif.get(271)
        model = exif.get(272)
        captured = None
        try:
            captured = exif.get_ifd(34665).get(36867)
        except Exception:
            pass
        captured = captured or exif.get(306)
    return {
        "make": str(make).strip().strip("\x00") if make else None,
        "model": str(model).strip().strip("\x00") if model else None,
        "captured_at": str(captured).strip() if captured else None,
    }


def group_by_capture(images: Iterable[Path]) -> dict[str, list[Path]]:
    """按 CaptureUUID 对同步产生的 RGB 与多光谱文件分组。"""
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in images:
        capture_uuid = read_xmp(path).get("CaptureUUID")
        if capture_uuid:
            groups[capture_uuid].append(path)
    return dict(groups)


def inspect_dataset(source_directory: Path, recursive: bool = False) -> dict[str, Any]:
    """返回可 JSON 序列化的航次基本检查结果。"""
    source_directory = Path(source_directory)
    images = list_images(source_directory, recursive=recursive)
    if not images:
        raise RuntimeError("目录中没有可读取的 JPG、TIF、TIFF 或 DNG。")

    makes: set[str] = set()
    models: set[str] = set()
    captured_times: list[str] = []
    band_counts: Counter[str] = Counter()
    capture_groups: dict[str, list[str]] = defaultdict(list)
    missing_uuid = 0

    for path in images:
        exif = read_exif(path)
        xmp = read_xmp(path)
        if exif["make"]:
            makes.add(str(exif["make"]))
        if exif["model"]:
            models.add(str(exif["model"]))
        if exif["captured_at"]:
            captured_times.append(str(exif["captured_at"]))
        if xmp.get("BandName"):
            band_counts[xmp["BandName"]] += 1
        if xmp.get("CaptureUUID"):
            capture_groups[xmp["CaptureUUID"]].append(path.name)
        else:
            missing_uuid += 1

    group_sizes = Counter(len(files) for files in capture_groups.values())
    mode = "multispectral" if len(band_counts) >= 2 else "rgb"
    return {
        "source_directory": str(source_directory.resolve()),
        "image_file_count": len(images),
        "capture_count": len(capture_groups),
        "images_without_capture_uuid": missing_uuid,
        "file_type_counts": dict(
            sorted(Counter(path.suffix.lower() for path in images).items())
        ),
        "capture_group_size_counts": {
            str(size): count for size, count in sorted(group_sizes.items())
        },
        "camera_makes": sorted(makes),
        "camera_models": sorted(models),
        "band_names": sorted(band_counts),
        "band_image_counts": dict(sorted(band_counts.items())),
        "processing_mode": mode,
        "capture_start": min(captured_times) if captured_times else None,
        "capture_end": max(captured_times) if captured_times else None,
    }


def validate_dataset(report: dict[str, Any]) -> list[str]:
    """返回阻止正射任务启动的数据问题；空列表表示可以继续。"""
    problems: list[str] = []
    if report.get("image_file_count", 0) <= 0:
        problems.append("没有影像文件")
    if report.get("processing_mode") == "multispectral":
        band_counts = report.get("band_image_counts", {})
        if len(set(band_counts.values())) > 1:
            problems.append("各多光谱波段影像数量不一致")
        capture_count = int(report.get("capture_count", 0))
        if capture_count and any(
            int(count) != capture_count for count in band_counts.values()
        ):
            problems.append("波段影像数量与 CaptureUUID 航片组数量不一致")
    return problems


def derive_project_name(
    source_directory: Path,
    report: dict[str, Any] | None = None,
) -> str:
    """根据拍摄时间、相机型号和目录名生成稳定项目名。"""
    report = report or inspect_dataset(source_directory)
    captured = str(report.get("capture_start") or "undated")
    try:
        captured = datetime.strptime(
            captured[:19], "%Y:%m:%d %H:%M:%S"
        ).strftime("%Y%m%d_%H%M%S")
    except ValueError:
        captured = "undated"

    models = report.get("camera_models") or ["camera"]
    model = re.sub(r"[^a-z0-9]+", "_", str(models[0]).lower()).strip("_")
    folder = re.sub(
        r"[^a-z0-9]+", "_", Path(source_directory).name.lower()
    ).strip("_")
    parts = ["uav", captured, model or "camera"]
    if folder:
        parts.append(folder[:24])
    return "_".join(parts)[:63].rstrip("_")

