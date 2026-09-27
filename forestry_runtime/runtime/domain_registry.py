"""Lightweight metadata for optional domain tool groups.

PydanticAI owns tool search and deferred disclosure.  This module only maps
domain tools to a plugin and a short description; it has no search state.
"""
from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class DomainToolGroup:
    name: str
    summary: str
    tools: tuple[str, ...]
    plugin: str
    keywords: tuple[str, ...]


DOMAIN_TOOL_GROUPS = (
    DomainToolGroup(
        "geospatial-raster-inspection",
        "Inspect raster/image metadata, pixels, masks, CRS, bands, and bounded previews.",
        ("inspect_file", "inspect_raster", "inspect_raster_region", "preview_image"),
        "remote-sensing",
        (
            "栅格", "影像元数据", "波段", "坐标系", "crs", "geotiff", "raster",
            "空间结果", "叠加", "缩略图", "局部", "区域", "掩膜", "mask",
        ),
    ),
    DomainToolGroup(
        "archive-import",
        "Inspect and safely extract ZIP attachments into managed assets.",
        ("inspect_zip", "extract_zip"),
        "remote-sensing",
        ("压缩包", "解压", "zip", "archive"),
    ),
    DomainToolGroup(
        "vegetation-analysis",
        "Vegetation indices, candidate canopy segmentation, and separate PROSAIL forward, LUT, and inversion operations.",
        (
            "calculate_ndvi", "segment_canopy", "simulate_prosail",
            "build_prosail_lut", "invert_prosail",
        ),
        "remote-sensing",
        (
            "ndvi", "植被指数", "林冠", "prosail", "正演", "反射率",
            "lut", "查找表", "参数反演",
        ),
    ),
    DomainToolGroup(
        "uav-data-audit",
        "Inventory UAV datasets and inspect existing geospatial products without starting a photogrammetry backend.",
        (
            "inspect_uav_source", "inspect_uav_dataset",
            "inspect_uav_products",
        ),
        "remote-sensing",
        (
            "无人机影像", "无人机照片", "航片", "航测", "正射", "正摄影像",
            "拼接", "摄影测量", "orthomosaic", "orthophoto", "photogrammetry",
            "数据检查", "处理条件", "数据盘点", "参考板", "已有成果",
            "成果质量", "成果检查", "dsm", "dtm", "chm",
        ),
    ),
    DomainToolGroup(
        "forest-structure",
        "Build canopy height models and derive explainable upper-canopy tree candidates and stand summaries.",
        (
            "build_canopy_height_model", "delineate_tree_candidates",
            "summarize_forest_structure",
        ),
        "remote-sensing",
        (
            "林分结构", "单木", "树冠", "冠幅", "树高", "林分密度",
            "冠层高度", "chm", "dsm", "dtm", "tree crown",
            "tree height", "forest structure",
        ),
    ),
)


def plugin_enabled(name: str) -> bool:
    if name == "remote-sensing":
        return os.getenv("REMOTE_SENSING_PLUGINS_ENABLED", "false").lower() == "true"
    return False


def matching_domain_tools(text: str) -> set[str]:
    """Select relevant domain schemas before the model's first action."""
    normalized = str(text or "").casefold()
    selected: set[str] = set()
    for group in DOMAIN_TOOL_GROUPS:
        if plugin_enabled(group.plugin) and any(
            keyword.casefold() in normalized for keyword in group.keywords
        ):
            selected.update(group.tools)
    return selected
