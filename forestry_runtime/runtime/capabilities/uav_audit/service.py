from __future__ import annotations

import io
import json
import math
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image
import prosail
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.warp import reproject, transform, transform_geom
from rasterio.windows import Window
from scipy.spatial import cKDTree
from scipy.stats import qmc
from scipy import ndimage as ndi

from ...storage import AssetError
from ...tool_protocol import ToolPreconditionError
from ...workspace import SourcePathError, is_host_path
from ..uav_audit.audit import (
    GEOSPATIAL_SUFFIXES, IMAGE_SUFFIXES, geospatial_product_role,
)


def finite(value):
    return value if value is None or math.isfinite(value) else str(value)

def recursive_scope_requested(text: str) -> bool:
    normalized = str(text or '').casefold()
    return any(phrase in normalized for phrase in (
        '递归', '所有子目录', '全部子目录', '包括子目录', '包含子目录',
        '所有子文件夹', '全部子文件夹', '包括子文件夹', '包含子文件夹',
        'recursive', 'all subdirectories', 'include subdirectories',
    ))

class UavAuditCapability:
    def _resolve_uav_source(self, name, arguments):
        if name not in {
            'inspect_uav_source', 'inspect_uav_dataset',
            'inspect_uav_products',
        }:
            return arguments, None
        values = dict(arguments or {})
        if name == 'inspect_uav_source' and values.get('kind') != 'folder':
            return values, None
        if self.workspaces is None:
            return values, None

        source_id = values.pop('source_id', None)
        if source_id:
            grant = self.workspaces.get_grant(self.owner, self.chat_id, source_id)
            if grant['access'] != 'read':
                raise AssetError('UAV source must be a readable directory grant')
            resolved = self.workspaces.resolve_grant_directory(
                grant, str(values.get('folder_path') or '.')
            )
        else:
            folder_path = str(values.get('folder_path') or '')
            match = self.workspaces.match_granted_directory(
                self.owner, self.chat_id, folder_path
            )
            if match and match[0] == 'suggestion':
                _, grant, suggested = match
                raise SourcePathError(
                    f"Source directory was not found. Retry with source_id='{grant['id']}' "
                    f"and folder_path='{suggested}'.",
                    source_id=grant['id'], requested_path=folder_path,
                    suggested_path=suggested,
                )
            if match:
                _, grant, resolved = match
                source_id = grant['id']
            else:
                if not is_host_path(folder_path):
                    raise AssetError(
                        'Relative source directory was not found under current grants; '
                        'provide an authorized source_id or an explicit host path.'
                    )
                covered = self.workspaces.covering_read_grant(
                    self.owner, self.chat_id, folder_path
                )
                grant = covered[0] if covered else self.workspaces.authorize_latest_request(
                    self.owner, self.chat_id, folder_path, self.latest_user
                )
                resolved = self.workspaces.bridge.resolve(folder_path)['path']
                source_id = grant['id']
        values['folder_path'] = resolved
        if values.get('recursive') and not recursive_scope_requested(self.latest_user):
            raise ToolPreconditionError(
                'Recursive source inspection was not requested. Decide whether to retry '
                'with recursive=false or first inspect the child-directory structure.',
                code='recursive_scope_unconfirmed', source_id=source_id,
                folder_path=resolved, suggested_arguments={'recursive': False},
            )
        return values, source_id

    def _uav_upload_assets(self, asset_ids=None):
        selected = set(asset_ids or self.allowed)
        if not selected.issubset(self.allowed):
            raise AssetError(
                'source.asset_ids包含不属于当前聊天的文件'
            )
        assets = []
        for asset_id in sorted(selected):
            asset = self.store.get(asset_id, self.owner)
            if Path(asset['name']).suffix.lower() not in IMAGE_SUFFIXES | {'.zip'}:
                continue
            assets.append({
                **asset,
                'path': str(self.store.path(asset_id, self.owner)),
            })
        if not assets:
            raise AssetError('当前聊天没有可用的无人机影像或ZIP附件')
        return assets

    def inspect_uav_source(
        self, kind, folder_path=None, source_id=None, recursive=False,
        asset_ids=None,
    ):
        if kind == 'folder':
            return self.uav_audit.inspect_folder(
                folder_path, recursive
            )
        return self.uav_audit.inspect_uploads(
            self._uav_upload_assets(asset_ids or [])
        )

    def inspect_uav_dataset(
        self, folder_path=None, source_id=None, max_files=5000,
    ):
        return self.uav_audit.inventory_dataset(folder_path, max_files)

    @staticmethod
    def _sample_raster_product(path: Path, root: Path) -> dict:
        role = geospatial_product_role(path) or 'unclassified'
        issues = []
        warnings = []
        with rasterio.open(path) as dataset:
            crs = dataset.crs
            if crs is None:
                issues.append('missing_crs')
            elif not crs.is_projected:
                warnings.append('crs_is_not_projected')
            if dataset.transform.is_identity or not dataset.transform.a or not dataset.transform.e:
                issues.append('invalid_or_identity_geotransform')
            if role in {'dsm', 'dtm', 'chm', 'ndvi'} and dataset.count != 1:
                warnings.append(f'{role}_expected_single_band')
            if (
                role == 'orthomosaic' and dataset.count > 1
                and not any(dataset.descriptions)
            ):
                warnings.append('multiband_roles_missing_from_metadata')
            sample_height = min(dataset.height, 256)
            sample_width = min(dataset.width, 256)
            sample = dataset.read(
                out_shape=(dataset.count, sample_height, sample_width),
                masked=True, resampling=Resampling.nearest,
            )
            band_stats = []
            any_valid = False
            for index in range(dataset.count):
                values = sample[index].compressed()
                values = values[np.isfinite(values)]
                any_valid = any_valid or bool(values.size)
                band_stats.append({
                    'band': index + 1,
                    'description': dataset.descriptions[index],
                    'dtype': dataset.dtypes[index],
                    'nodata': finite(dataset.nodatavals[index]),
                    'sample_valid_fraction': float(values.size / sample[index].size),
                    'sample_min': finite(float(values.min())) if values.size else None,
                    'sample_max': finite(float(values.max())) if values.size else None,
                    'sample_mean': finite(float(values.mean())) if values.size else None,
                })
            if not any_valid:
                issues.append('sample_contains_no_valid_pixels')
            status = 'fail' if issues else ('warning' if warnings else 'pass_metadata')
            crs_units = (
                'degrees' if crs and crs.is_geographic
                else (crs.linear_units if crs else None)
            )
            return {
                'relative_path': path.relative_to(root).as_posix(),
                'role': role,
                'status': status,
                'driver': dataset.driver,
                'width': dataset.width,
                'height': dataset.height,
                'band_count': dataset.count,
                'crs': str(crs) if crs else None,
                'projected_crs': bool(crs and crs.is_projected),
                'crs_units': crs_units,
                'pixel_size': {
                    'x': abs(float(dataset.transform.a)),
                    'y': abs(float(dataset.transform.e)),
                    'units': crs_units,
                },
                'bounds': [finite(float(value)) for value in dataset.bounds],
                'transform': [finite(float(value)) for value in dataset.transform[:6]],
                'overviews': dataset.overviews(1) if dataset.count else [],
                'band_stats': band_stats,
                'issues': issues,
                'warnings': warnings,
                'sample_scope': 'nearest_resample_max_256x256_per_band',
            }

    def inspect_uav_products(
        self, folder_path=None, source_id=None, name_filter=None,
        max_products=32,
    ):
        root = self.uav_audit.mapper.input_path(folder_path)
        candidates = []
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() not in GEOSPATIAL_SUFFIXES:
                continue
            if name_filter and name_filter.casefold() not in path.name.casefold():
                continue
            candidates.append(path)
            if len(candidates) > max_products:
                raise AssetError(
                    f'匹配成果超过单次检查上限 {max_products}；请使用name_filter缩小范围'
                )
        if not candidates:
            raise AssetError('目录中没有匹配的 GeoTIFF 或 VRT 成果')

        products = []
        for path in candidates:
            try:
                products.append(self._sample_raster_product(path, root))
            except Exception as exc:
                products.append({
                    'relative_path': path.relative_to(root).as_posix(),
                    'role': geospatial_product_role(path) or 'unclassified',
                    'status': 'fail',
                    'issues': [f'unreadable_raster:{type(exc).__name__}'],
                    'warnings': [],
                })

        by_role: dict[str, list[dict]] = {}
        for product in products:
            by_role.setdefault(product['role'], []).append(product)
        missing = [role for role in ('dsm', 'dtm') if not by_role.get(role)]
        alignment_issues = []
        if not missing:
            dsm = by_role['dsm'][0]
            dtm = by_role['dtm'][0]
            for key in ('crs', 'width', 'height', 'transform'):
                if dsm.get(key) != dtm.get(key):
                    alignment_issues.append(f'dsm_dtm_{key}_mismatch')
        chm_suitability = {
            'suitable_from_metadata': not missing and not alignment_issues,
            'missing_roles': missing,
            'alignment_issues': alignment_issues,
            'required_unverified_evidence': (
                [] if missing else ['shared_vertical_reference_and_height_units']
            ),
            'meaning': (
                '栅格元数据对齐仍不足以证明可生成可靠CHM；必须确认DSM/DTM共同的'
                '高程单位、垂直基准和DTM地面分类质量。'
            ),
        }
        return {
            'report_type': 'uav_geospatial_product_qa',
            'report_version': 1,
            'observation_complete': True,
            'recommended_next_action': 'answer_from_this_report',
            'source': folder_path,
            'product_count': len(products),
            'products': products,
            'role_counts': {
                role: len(items) for role, items in sorted(by_role.items())
            },
            'chm_input_suitability': chm_suitability,
            'automated_qa_scope': 'metadata_and_bounded_pixel_sample',
            'visual_review_required': [
                'seamlines', 'holes', 'warped_crowns', 'double_features',
                'edge_artifacts', 'exposure_steps', 'spectral_misregistration',
            ],
            'accuracy_boundary': (
                '可读且有投影的GeoTIFF不证明测量级几何精度；需独立GCP/检查点残差。'
            ),
        }
