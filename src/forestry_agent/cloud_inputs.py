"""Trusted, offline, bounded LAS/LAZ inspection; no point-cloud processing."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Literal

import laspy
import numpy as np
from pydantic import Field
from pyproj import CRS
from pyproj.exceptions import CRSError

from .contracts import Boundary, Failure
from .resources import native_path, version
from .store import now

METADATA_BYTES = 4 * 1024 * 1024
SAMPLE_BYTES = 1024 * 1024
SAMPLE_POINTS = 4096


class CloudDeclaration(Boundary):
    """Operator/publisher assertions remain separate from file observations."""

    source_note: str = Field(min_length=1, max_length=4000)
    horizontal_crs: str | None = Field(default=None, max_length=16000)
    vertical_ref: str | None = Field(default=None, max_length=1000)
    z_unit: Literal["m", "ft", "unknown"] = "unknown"
    z_semantics: Literal["elevation", "height_above_ground", "unknown"] = "unknown"
    classification_semantics: Literal["unknown", "las_standard", "publisher_annotations"] = "unknown"


class MetadataReader:
    def __init__(self, stream):
        self.stream, self.remaining = stream, METADATA_BYTES

    def read(self, size=-1):
        if size < 0 or size > self.remaining:
            raise Failure("INPUT_LIMIT", "点云头/扩展元数据超过有界检查预算", 422)
        raw = self.stream.read(size)
        self.remaining -= len(raw)
        return raw

    def seek(self, offset, whence=0):
        return self.stream.seek(offset, whence)

    def tell(self):
        return self.stream.tell()


def inspect_cloud(path: Path, declaration: CloudDeclaration | None = None, *, hash_file=False):
    path = path.resolve(strict=True)
    source = native_path(path)
    if not source.is_file():
        raise Failure("SCHEMA_INVALID", "点云检查要求单文件", 422)
    before = version(path)
    size = source.stat().st_size
    warnings = []
    try:
        with source.open("rb") as stream:
            header = laspy.LasHeader.read_from(MetadataReader(stream), read_evlrs=True)
            values = [*header.scales, *header.offsets, *header.mins, *header.maxs]
            if not all(math.isfinite(float(x)) for x in values) or any(header.scales <= 0):
                raise ValueError("坐标比例、偏移或边界无效")
            if any(header.mins > header.maxs) or header.offset_to_point_data > size:
                raise ValueError("点云边界或点数据位置无效")
            dimensions = list(header.point_format.dimension_names)
            count, record_size = int(header.point_count), int(header.point_format.size)
            sample = {"status": "not_sampled", "count": 0, "scope": "first_points_only"}
            if not header.are_points_compressed:
                if header.offset_to_point_data + count * record_size > size:
                    raise ValueError("文件长度不足以容纳声明的全部点记录")
                sample_count = min(count, SAMPLE_POINTS, SAMPLE_BYTES // record_size)
                if sample_count:
                    stream.seek(header.offset_to_point_data)
                    raw = stream.read(sample_count * record_size)
                    points = laspy.ScaleAwarePointRecord(
                        np.frombuffer(raw, dtype=header.point_format.dtype()),
                        header.point_format, header.scales, header.offsets,
                    )
                    xyz = np.column_stack((points.x, points.y, points.z))
                    if not np.isfinite(xyz).all():
                        raise ValueError("抽样点坐标不是有限值")
                    tolerance = header.scales * 1.01
                    if np.any(xyz < header.mins - tolerance) or np.any(xyz > header.maxs + tolerance):
                        raise ValueError("抽样点位于声明边界之外")
                    classes, frequencies = np.unique(points.classification, return_counts=True)
                    sample = {"status": "observed", "count": sample_count,
                              "scope": "first_points_only", "bounds": [xyz.min(0).tolist(), xyz.max(0).tolist()],
                              "classification_counts": dict(zip(map(str, classes), map(int, frequencies)))}
            else:
                warnings.append("LAZ仅检查头/元数据；尚未解压检查点或验证完整压缩流")
            try:
                crs = header.parse_crs()
            except (ValueError, RuntimeError) as error:
                crs = None
                warnings.append(f"文件CRS不能解析：{error}")
    except Failure:
        raise
    except Exception as error:
        raise Failure("CLOUD_INVALID", f"不能读取有效LAS/LAZ：{error}", 422) from error

    declared = declaration.model_dump() if declaration else None
    declared_crs = None
    if declaration and declaration.horizontal_crs:
        try:
            declared_crs = CRS.from_user_input(declaration.horizontal_crs)
        except (ValueError, CRSError) as error:
            raise Failure("SCHEMA_INVALID", "声明的水平CRS无效", 422) from error
    conflict = bool(crs and declared_crs and not crs.equals(declared_crs))
    if conflict:
        warnings.append("文件CRS与声明冲突；未自动改写或重投影")
    if crs is None:
        warnings.append("文件未提供可解析CRS；声明不能替代文件观察")
    extra = list(header.point_format.extra_dimension_names)
    if extra:
        warnings.append("额外维度可能含真值/实例标签；处理前须审查并隔离，不能自动用于预测")
    warnings.append("分类码和首批点样本不能证明完整ROI地面支持；未计算DTM/DSM/HAG或科学精度")
    digest = None
    if hash_file:
        hasher = hashlib.sha256()
        with source.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                hasher.update(block)
        digest = hasher.hexdigest()
    if version(path) != before:
        raise Failure("VERSION_CONFLICT", "检查过程中源文件已变化", 409)
    axes = [{"name": a.name, "unit": a.unit_name, "metres_per_unit": a.unit_conversion_factor}
            for a in crs.axis_info[:2]] if crs else []
    return {"schema_version": 1, "inspected_at": now(), "status": "inspected",
            "source": {"path": str(path), "bytes": size, "version": before, "sha256": digest},
            "reader": {"name": "laspy", "version": laspy.__version__,
                       "metadata_byte_limit": METADATA_BYTES, "sample_point_limit": SAMPLE_POINTS},
            "observed": {"las_version": str(header.version), "point_format": header.point_format.id,
                         "compressed": header.are_points_compressed, "point_count": count,
                         "scales": header.scales.tolist(), "offsets": header.offsets.tolist(),
                         "bounds": [header.mins.tolist(), header.maxs.tolist()], "dimensions": dimensions,
                         "extra_dimensions": extra, "horizontal_crs": crs.to_string() if crs else None,
                         "crs_wkt": crs.to_wkt() if crs else None, "xy_axes": axes,
                         "vertical_ref": None, "z_unit": "unknown", "z_semantics": "unknown",
                         "sample": sample},
            "declared": declared,
            "prerequisites": {"crs_conflict": conflict,
                              "file_projected_xy_metres": bool(crs and crs.is_projected and len(axes) == 2
                                                               and all(a["metres_per_unit"] == 1 for a in axes)),
                              "declared_elevation_metres": bool(declaration and declaration.z_unit == "m"
                                                                and declaration.z_semantics == "elevation"),
                              "declared_vertical_reference": bool(declaration and declaration.vertical_ref),
                              "ground_support": "not_assessed", "scientific_runtime": "not_assessed"},
            "warnings": warnings}
