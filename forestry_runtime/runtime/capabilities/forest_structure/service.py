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

from ...kernel.protocol import ToolPreconditionError
from ...storage import AssetError
from ..uav_audit.audit import GEOSPATIAL_SUFFIXES, IMAGE_SUFFIXES

class ForestStructureCapability:
    @staticmethod
    def _structure_limit(width, height):
        limit = int(os.getenv('FOREST_STRUCTURE_MAX_PIXELS', '10000000'))
        pixels = int(width) * int(height)
        if pixels > limit:
            raise AssetError(
                f'林分结构工具单次最多处理{limit}像元，当前为{pixels}像元；'
                '请先在Workspace中裁剪明确的研究区或降低分析分辨率。'
            )
        return pixels

    @staticmethod
    def _require_projected_metre(src, label):
        if not src.crs or not src.crs.is_projected:
            raise AssetError(f'{label}必须具有投影坐标系')
        units = str(src.crs.linear_units or '').casefold()
        if units not in {'metre', 'meter', 'metres', 'meters'}:
            raise AssetError(f'{label}的水平单位必须是米，当前为{units or "unknown"}')
        if src.count != 1:
            raise AssetError(f'{label}必须是单波段GeoTIFF')
        if abs(src.transform.b) > 1e-12 or abs(src.transform.d) > 1e-12:
            raise AssetError(f'{label}当前不支持旋转或倾斜网格')

    @staticmethod
    def _vertical_reference(src, label):
        tags = {
            str(key).casefold(): str(value).strip()
            for key, value in src.tags().items()
        }
        reference = next((
            tags[key] for key in (
                'vertical_reference', 'vertical_datum', 'vert_datum',
            ) if tags.get(key)
        ), None)
        unit = next((
            tags[key] for key in ('vertical_units', 'elevation_units')
            if tags.get(key)
        ), None)
        if not reference:
            raise AssetError(f'{label}缺少可验证的vertical_reference元数据')
        if not unit or unit.casefold() not in {
            'm', 'metre', 'meter', 'metres', 'meters',
        }:
            raise AssetError(f'{label}缺少米制vertical_units元数据')
        return reference

    @staticmethod
    def _reference_arguments(reference, asset_id):
        """Turn either input form into the flat arguments the resolver accepts.

        ``model_dump()`` hands a nested reference to these methods as a plain
        mapping, while a direct call may pass the model itself; both mean the same
        thing and neither should have to know which one it is.
        """
        if reference is None:
            return {"asset_id": asset_id}
        if hasattr(reference, "as_arguments"):
            return reference.as_arguments()
        if isinstance(reference, dict):
            return {
                "scope": reference.get("scope") or "auto",
                "asset_id": reference.get("asset_id"),
                "path": reference.get("path") or "",
                "source_id": reference.get("source_id"),
            }
        raise AssetError(f"Unsupported input reference: {reference!r}")

    def build_canopy_height_model(
        self, dsm, dtm, dsm_asset_id, dtm_asset_id, vertical_reference,
    ):
        dsm_asset, dsm_path, dsm_resolved = self._open_input(
            role='dsm', **self._reference_arguments(dsm, dsm_asset_id),
        )
        dtm_asset, dtm_path, dtm_resolved = self._open_input(
            role='dtm', **self._reference_arguments(dtm, dtm_asset_id),
        )
        for asset, path, label in (
            (dsm_asset, dsm_path, 'DSM'), (dtm_asset, dtm_path, 'DTM')
        ):
            if Path(asset.get('name') or path.name).suffix.casefold() not in {'.tif', '.tiff'}:
                raise AssetError(f'{label}必须是GeoTIFF资产')
            self.validate_tiff(path)

        temp_path = None
        try:
            with rasterio.open(dsm_path, driver='GTiff') as dsm, rasterio.open(
                dtm_path, driver='GTiff'
            ) as dtm:
                self._require_projected_metre(dsm, 'DSM')
                self._require_projected_metre(dtm, 'DTM')
                dsm_reference = self._vertical_reference(dsm, 'DSM')
                dtm_reference = self._vertical_reference(dtm, 'DTM')
                if dsm_reference.casefold() != dtm_reference.casefold():
                    raise ToolPreconditionError(
                        'DSM与DTM的vertical_reference元数据不一致，'
                        '两者之间不存在已建立的高程差',
                        code='vertical_reference_conflict',
                        dsm_vertical_reference=dsm_reference,
                        dtm_vertical_reference=dtm_reference,
                        retryable=False,
                        guidance=(
                            '两张栅格的垂直基准不同，任何差值都不是以米为单位的高差。'
                            '不要重试：请报告该条件无法满足，并给出两份元数据。'
                        ),
                    )
                if vertical_reference.strip().casefold() != dsm_reference.casefold():
                    # The refusal names the value the metadata actually carries.  A
                    # caller that read the datum can retry in one step; a caller that
                    # guessed gets told what to read, instead of being left to guess
                    # again until its budget runs out.
                    raise ToolPreconditionError(
                        'vertical_reference必须与DSM和DTM元数据中的垂直基准一致',
                        code='vertical_reference_mismatch',
                        requested_vertical_reference=vertical_reference,
                        observed_vertical_reference=dsm_reference,
                        retryable=True,
                        suggested_tool='build_canopy_height_model',
                        suggested_arguments={
                            'dsm': dict(dsm_resolved.reference),
                            'dtm': dict(dtm_resolved.reference),
                            'vertical_reference': dsm_reference,
                        },
                        guidance=(
                            '用 suggested_arguments 重试即可；该值取自资产自身的元数据。'
                        ),
                    )
                total_pixels = self._structure_limit(dsm.width, dsm.height)
                self._structure_limit(dtm.width, dtm.height)

                dsm_raw = dsm.read(1).astype('float64')
                dsm_values = (
                    dsm_raw * float(dsm.scales[0]) + float(dsm.offsets[0])
                )
                dsm_valid = (dsm.read_masks(1) != 0) & np.isfinite(dsm_values)

                dtm_raw = dtm.read(1).astype('float64')
                dtm_source = (
                    dtm_raw * float(dtm.scales[0]) + float(dtm.offsets[0])
                )
                dtm_source_valid = (
                    (dtm.read_masks(1) != 0) & np.isfinite(dtm_source)
                )
                same_grid = (
                    dsm.crs == dtm.crs and dsm.transform == dtm.transform
                    and dsm.width == dtm.width and dsm.height == dtm.height
                )
                if same_grid:
                    dtm_values = dtm_source
                    dtm_valid = dtm_source_valid
                else:
                    dtm_values = np.full(
                        (dsm.height, dsm.width), np.nan, dtype='float64'
                    )
                    dtm_valid_u8 = np.zeros(
                        (dsm.height, dsm.width), dtype='uint8'
                    )
                    reproject(
                        source=np.where(dtm_source_valid, dtm_source, np.nan),
                        destination=dtm_values,
                        src_transform=dtm.transform, src_crs=dtm.crs,
                        dst_transform=dsm.transform, dst_crs=dsm.crs,
                        src_nodata=np.nan, dst_nodata=np.nan,
                        resampling=Resampling.bilinear,
                    )
                    reproject(
                        source=dtm_source_valid.astype('uint8'),
                        destination=dtm_valid_u8,
                        src_transform=dtm.transform, src_crs=dtm.crs,
                        dst_transform=dsm.transform, dst_crs=dsm.crs,
                        src_nodata=0, dst_nodata=0,
                        resampling=Resampling.nearest,
                    )
                    dtm_valid = (dtm_valid_u8 != 0) & np.isfinite(dtm_values)

                valid = dsm_valid & dtm_valid
                valid_count = int(np.count_nonzero(valid))
                if not valid_count:
                    raise AssetError('DSM与DTM没有重叠的有效像元')
                chm = np.full((dsm.height, dsm.width), np.nan, dtype='float32')
                chm[valid] = (dsm_values[valid] - dtm_values[valid]).astype(
                    'float32'
                )
                observed = chm[valid].astype('float64')
                negative_count = int(np.count_nonzero(observed < 0))
                profile = dsm.profile.copy()
                profile.update(
                    driver='GTiff', count=1, dtype='float32', nodata=np.nan,
                    compress='deflate', predictor=3, BIGTIFF='IF_SAFER',
                )
                profile.pop('photometric', None)
                profile.pop('interleave', None)
                handle = tempfile.NamedTemporaryFile(
                    suffix='.tif', dir=self.store.root, delete=False
                )
                temp_path = Path(handle.name)
                handle.close()
                with rasterio.open(temp_path, 'w', **profile) as dst:
                    dst.write(chm, 1)
                    dst.set_band_description(1, 'canopy_height_m')
                    dst.update_tags(
                        algorithm='CHM_DSM_MINUS_DTM',
                        dsm_asset_id=dsm_resolved.source_asset_id() or '',
                        dtm_asset_id=dtm_resolved.source_asset_id() or '',
                        dsm_reference=json.dumps(dsm_resolved.reference, ensure_ascii=False),
                        dtm_reference=json.dumps(dtm_resolved.reference, ensure_ascii=False),
                        vertical_reference=vertical_reference,
                        dtm_resampled=str(not same_grid).lower(),
                        negative_values_preserved='true',
                    )
                pixel_area = abs(
                    dsm.transform.a * dsm.transform.e
                    - dsm.transform.b * dsm.transform.d
                )

            output_name = f'{Path(dsm_asset.get("name") or dsm_path.name).stem}_chm.tif'
            with temp_path.open('rb') as stream:
                output = self.register(
                    stream, output_name, 'image/tiff', dsm_resolved.source_asset_id(),
                    metadata={
                        'algorithm': 'CHM_DSM_MINUS_DTM',
                        'dsm_reference': dict(dsm_resolved.reference),
                        'dtm_reference': dict(dtm_resolved.reference),
                        'vertical_reference': vertical_reference,
                    },
                )
            return {
                'chm': output,
                'method': 'dsm_minus_aligned_dtm',
                'vertical_reference': vertical_reference,
                'dtm_resampled_to_dsm_grid': not same_grid,
                'statistics': {
                    'total_pixel_count': total_pixels,
                    'valid_pixel_count': valid_count,
                    'missing_pixel_count': total_pixels - valid_count,
                    'negative_height_pixel_count': negative_count,
                    'negative_height_fraction_of_valid': negative_count / valid_count,
                    'minimum_m': float(np.min(observed)),
                    'maximum_m': float(np.max(observed)),
                    'mean_m': float(np.mean(observed)),
                    'valid_area_m2': valid_count * pixel_area,
                },
                'note': (
                    'CHM仅表示输入DSM与DTM的高程差。负值被保留并单独统计；'
                    'DTM存在及成功对齐不证明密林下地形或树高已经过外业验证。'
                ),
            }
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def delineate_tree_candidates(
        self, chm, chm_asset_id, minimum_height_m, smoothing_sigma_m,
        minimum_peak_distance_m, minimum_crown_area_m2,
        maximum_crown_area_m2=None, parameter_source='',
    ):
        from skimage.feature import peak_local_max
        from skimage.segmentation import watershed

        asset, path, chm_resolved = self._open_input(
            role='chm', **self._reference_arguments(chm, chm_asset_id),
        )
        if Path(asset.get('name') or path.name).suffix.casefold() not in {'.tif', '.tiff'}:
            raise AssetError('CHM必须是GeoTIFF资产')
        self.validate_tiff(path)
        temp_path = None
        try:
            with rasterio.open(path, driver='GTiff') as src:
                self._require_projected_metre(src, 'CHM')
                self._structure_limit(src.width, src.height)
                tags = src.tags()
                if tags.get('algorithm') != 'CHM_DSM_MINUS_DTM':
                    raise AssetError(
                        '候选单木分割只接受build_canopy_height_model生成的CHM资产'
                    )
                values = src.read(1).astype('float64')
                valid = (src.read_masks(1) != 0) & np.isfinite(values)
                resolution = math.sqrt(abs(
                    src.transform.a * src.transform.e
                    - src.transform.b * src.transform.d
                ))
                sigma_pixels = float(smoothing_sigma_m) / resolution
                peak_distance_pixels = max(
                    1, int(round(float(minimum_peak_distance_m) / resolution))
                )
                analysis_mask = valid & (values >= float(minimum_height_m))
                if not np.any(analysis_mask):
                    return {
                        'outcome': 'empty', 'code': 'no_pixels_above_height',
                        'control_verified': None, 'chm_reference': dict(chm_resolved.reference),
                        'valid_pixels': int(np.count_nonzero(valid)),
                        'pixels_above_height': 0,
                        'minimum_height_m': float(minimum_height_m),
                        'observed_max_m': float(np.max(values[valid])) if np.any(valid) else None,
                        'interpretation': 'No candidates under these inputs; tree absence is unverified.',
                    }
                weighted = np.where(valid, values, 0.0)
                weights = ndi.gaussian_filter(
                    valid.astype('float64'), sigma=sigma_pixels
                )
                smoothed = np.divide(
                    ndi.gaussian_filter(weighted, sigma=sigma_pixels),
                    weights,
                    out=np.zeros_like(weighted),
                    where=weights > 1e-9,
                )
                coordinates = peak_local_max(
                    smoothed, min_distance=peak_distance_pixels,
                    threshold_abs=float(minimum_height_m),
                    labels=analysis_mask.astype('uint8'),
                    exclude_border=False,
                )
                if not len(coordinates):
                    return {
                        'outcome': 'empty', 'code': 'no_local_peaks',
                        'control_verified': None, 'chm_reference': dict(chm_resolved.reference),
                        'pixels_above_height': int(np.count_nonzero(analysis_mask)),
                        'minimum_height_m': float(minimum_height_m),
                        'minimum_peak_distance_m': float(minimum_peak_distance_m),
                        'interpretation': 'Peak search returned no candidates; tree absence is unverified.',
                    }
                markers = np.zeros(values.shape, dtype='int32')
                markers[tuple(coordinates.T)] = np.arange(
                    1, len(coordinates) + 1, dtype='int32'
                )
                labels = watershed(-smoothed, markers, mask=analysis_mask)
                pixel_area = resolution ** 2
                counts = np.bincount(labels.ravel())
                keep = counts * pixel_area >= float(minimum_crown_area_m2)
                keep[0] = False
                if maximum_crown_area_m2 is not None:
                    keep &= counts * pixel_area <= float(maximum_crown_area_m2)
                labels[~keep[labels]] = 0
                old_ids = np.unique(labels)
                old_ids = old_ids[old_ids > 0]
                if not old_ids.size:
                    return {
                        'outcome': 'empty', 'code': 'all_candidates_filtered_by_area',
                        'control_verified': None, 'chm_reference': dict(chm_resolved.reference),
                        'peaks_found': int(len(coordinates)),
                        'minimum_crown_area_m2': float(minimum_crown_area_m2),
                        'maximum_crown_area_m2': maximum_crown_area_m2,
                        'interpretation': 'Area filters removed all candidates; tree absence is unverified.',
                    }
                mapping = np.zeros(int(labels.max()) + 1, dtype='uint32')
                mapping[old_ids] = np.arange(1, len(old_ids) + 1, dtype='uint32')
                labels = mapping[labels]
                candidate_count = int(labels.max())
                indexes = np.arange(1, candidate_count + 1)
                candidate_pixels = np.bincount(labels.ravel())[1:]
                maximum_positions = ndi.maximum_position(
                    np.where(valid, values, -np.inf), labels=labels,
                    index=indexes,
                )
                boundary_zone = valid & ndi.binary_dilation(~valid)
                crown_features = []
                point_features = []
                properties_by_id = {}
                for candidate_id, position in zip(indexes, maximum_positions):
                    row, column = (int(position[0]), int(position[1]))
                    region = labels == candidate_id
                    area = float(candidate_pixels[candidate_id - 1] * pixel_area)
                    edge = bool(
                        row == 0 or column == 0
                        or row == src.height - 1 or column == src.width - 1
                        or np.any(region & boundary_zone)
                        or np.any(region[0]) or np.any(region[-1])
                        or np.any(region[:, 0]) or np.any(region[:, -1])
                    )
                    x, y = rasterio.transform.xy(
                        src.transform, row, column, offset='center'
                    )
                    longitude, latitude = transform(
                        src.crs, 'EPSG:4326', [x], [y]
                    )
                    properties = {
                        'candidate_id': int(candidate_id),
                        'height_m': float(values[row, column]),
                        'crown_area_m2': area,
                        'equivalent_crown_diameter_m': float(
                            2 * math.sqrt(area / math.pi)
                        ),
                        'edge_or_nodata_truncated': edge,
                    }
                    properties_by_id[int(candidate_id)] = properties
                    point_features.append({
                        'type': 'Feature',
                        'geometry': {
                            'type': 'Point',
                            'coordinates': [longitude[0], latitude[0]],
                        },
                        'properties': properties,
                    })
                for geometry, value in shapes(
                    labels.astype('int32'), mask=labels > 0,
                    transform=src.transform,
                ):
                    candidate_id = int(value)
                    crown_features.append({
                        'type': 'Feature',
                        'geometry': transform_geom(
                            src.crs, 'EPSG:4326', geometry, precision=8
                        ),
                        'properties': properties_by_id[candidate_id],
                    })

                profile = src.profile.copy()
                profile.update(
                    driver='GTiff', count=1, dtype='uint32', nodata=0,
                    compress='deflate', predictor=2, BIGTIFF='IF_SAFER',
                )
                profile.pop('photometric', None)
                profile.pop('interleave', None)
                handle = tempfile.NamedTemporaryFile(
                    suffix='.tif', dir=self.store.root, delete=False
                )
                temp_path = Path(handle.name)
                handle.close()
                with rasterio.open(temp_path, 'w', **profile) as dst:
                    dst.write(labels.astype('uint32'), 1)
                    dst.set_band_description(1, 'upper_canopy_candidate_id')
                    dst.update_tags(
                        algorithm='CHM_LOCAL_MAXIMA_WATERSHED',
                        source_asset_id=chm_resolved.source_asset_id() or '',
                        source_reference=json.dumps(chm_resolved.reference, ensure_ascii=False),
                        minimum_height_m=str(minimum_height_m),
                        smoothing_sigma_m=str(smoothing_sigma_m),
                        minimum_peak_distance_m=str(minimum_peak_distance_m),
                        minimum_crown_area_m2=str(minimum_crown_area_m2),
                        maximum_crown_area_m2=str(maximum_crown_area_m2),
                        parameter_source=parameter_source,
                    )
                valid_count = int(np.count_nonzero(valid))
                labelled_count = int(np.count_nonzero(labels))

            stem = Path(asset.get('name') or path.name).stem
            with temp_path.open('rb') as stream:
                label_asset = self.register(
                    stream, f'{stem}_tree_candidates.tif', 'image/tiff',
                    chm_resolved.source_asset_id(),
                    metadata={
                        'algorithm': 'CHM_LOCAL_MAXIMA_WATERSHED',
                        'parameter_source': parameter_source,
                    },
                )
            crowns = self.register(
                io.BytesIO(json.dumps({
                    'type': 'FeatureCollection', 'features': crown_features,
                    'coordinate_reference_system': 'EPSG:4326',
                }, ensure_ascii=False).encode('utf-8')),
                f'{stem}_candidate_crowns.geojson', 'application/geo+json',
                label_asset['id'],
            )
            tops = self.register(
                io.BytesIO(json.dumps({
                    'type': 'FeatureCollection', 'features': point_features,
                    'coordinate_reference_system': 'EPSG:4326',
                }, ensure_ascii=False).encode('utf-8')),
                f'{stem}_candidate_tops.geojson', 'application/geo+json',
                label_asset['id'],
            )
            return {
                'labels': label_asset,
                'candidate_crowns': crowns,
                'candidate_tops': tops,
                'method': 'smoothed_chm_local_maxima_marker_watershed',
                'parameters': {
                    'minimum_height_m': minimum_height_m,
                    'smoothing_sigma_m': smoothing_sigma_m,
                    'minimum_peak_distance_m': minimum_peak_distance_m,
                    'minimum_crown_area_m2': minimum_crown_area_m2,
                    'maximum_crown_area_m2': maximum_crown_area_m2,
                    'parameter_source': parameter_source,
                },
                'statistics': {
                    'upper_canopy_candidate_count': candidate_count,
                    'valid_chm_pixel_count': valid_count,
                    'labelled_candidate_pixel_count': labelled_count,
                    'height_filtered_candidate_cover_fraction': (
                        labelled_count / valid_count
                    ),
                },
                'note': (
                    '输出是从当前CHM可见上层冠层得到的候选，不是林木总株数。'
                    'GeoJSON坐标为EPSG:4326；面积和高度保留自源投影米制网格。'
                ),
            }
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _distribution(values):
        values = np.asarray(values, dtype='float64')
        if not values.size:
            return None
        quantiles = np.percentile(values, [5, 25, 50, 75, 95])
        return {
            'count': int(values.size),
            'minimum': float(np.min(values)),
            'p05': float(quantiles[0]), 'p25': float(quantiles[1]),
            'median': float(quantiles[2]), 'p75': float(quantiles[3]),
            'p95': float(quantiles[4]), 'maximum': float(np.max(values)),
            'mean': float(np.mean(values)),
        }

    def summarize_forest_structure(self, chm, labels, chm_asset_id, labels_asset_id):
        chm_asset, chm_path, chm_resolved = self._open_input(
            role='chm', **self._reference_arguments(chm, chm_asset_id),
        )
        labels_asset, labels_path, labels_resolved = self._open_input(
            role='labels', **self._reference_arguments(labels, labels_asset_id),
        )
        with rasterio.open(chm_path, driver='GTiff') as chm, rasterio.open(
            labels_path, driver='GTiff'
        ) as label_src:
            self._require_projected_metre(chm, 'CHM')
            self._require_projected_metre(label_src, '候选标签')
            self._structure_limit(chm.width, chm.height)
            if not (
                chm.crs == label_src.crs and chm.transform == label_src.transform
                and chm.width == label_src.width and chm.height == label_src.height
            ):
                raise ToolPreconditionError(
                    'CHM与候选标签的坐标系、网格和范围必须完全一致',
                    code='raster_grid_mismatch', reason='inapplicable',
                    missing=[{'kind': 'aligned_grid',
                              'chm': dict(chm_resolved.reference),
                              'labels': dict(labels_resolved.reference)}],
                    mismatches=[field for field, same in {
                        'crs': chm.crs == label_src.crs,
                        'transform': chm.transform == label_src.transform,
                        'width': chm.width == label_src.width,
                        'height': chm.height == label_src.height,
                    }.items() if not same],
                    chm_grid={'crs': str(chm.crs), 'width': chm.width,
                              'height': chm.height, 'transform': tuple(chm.transform)},
                    labels_grid={'crs': str(label_src.crs), 'width': label_src.width,
                                 'height': label_src.height, 'transform': tuple(label_src.transform)},
                )
            if label_src.tags().get('algorithm') != 'CHM_LOCAL_MAXIMA_WATERSHED':
                raise AssetError('标签必须由delineate_tree_candidates生成')
            heights = chm.read(1).astype('float64')
            valid = (chm.read_masks(1) != 0) & np.isfinite(heights)
            labels = label_src.read(1).astype('uint32')
            labels[~valid] = 0
            candidate_ids = np.unique(labels)
            candidate_ids = candidate_ids[candidate_ids > 0]
            if not candidate_ids.size:
                raise AssetError('候选标签中没有候选冠层')
            pixel_area = abs(
                chm.transform.a * chm.transform.e
                - chm.transform.b * chm.transform.d
            )
            valid_pixels = int(np.count_nonzero(valid))
            candidate_pixels = int(np.count_nonzero(labels))
            valid_area_m2 = valid_pixels * pixel_area
            rows = []
            for candidate_id in candidate_ids:
                region = labels == candidate_id
                area = float(np.count_nonzero(region) * pixel_area)
                values = heights[region]
                rows.append({
                    'candidate_id': int(candidate_id),
                    'height_max_m': float(np.max(values)),
                    'height_mean_m': float(np.mean(values)),
                    'crown_area_m2': area,
                    'equivalent_crown_diameter_m': float(
                        2 * math.sqrt(area / math.pi)
                    ),
                })
            summary = {
                'upper_canopy_candidate_count': len(rows),
                'analysis_denominator': 'valid_chm_pixels',
                'valid_area_m2': valid_area_m2,
                'valid_area_ha': valid_area_m2 / 10000,
                'candidate_density_per_valid_ha': (
                    len(rows) / (valid_area_m2 / 10000)
                ),
                'height_filtered_candidate_cover_fraction': (
                    candidate_pixels / valid_pixels
                ),
                'candidate_height_max_m': self._distribution([
                    row['height_max_m'] for row in rows
                ]),
                'candidate_crown_area_m2': self._distribution([
                    row['crown_area_m2'] for row in rows
                ]),
                'equivalent_crown_diameter_m': self._distribution([
                    row['equivalent_crown_diameter_m'] for row in rows
                ]),
            }

        stem = Path(labels_asset.get('name') or labels_path.name).stem
        json_asset = self.register(
            io.BytesIO(json.dumps({
                'summary': summary,
                'candidates': rows,
                'limitations': [
                    '候选数不是林木总株数',
                    '分母是CHM有效像元范围，不是自动识别的林班或样地边界',
                    '无独立外业参考时不报告准确率',
                ],
                'sources': {
                    'chm': dict(chm_resolved.reference),
                    'labels': dict(labels_resolved.reference),
                },
            }, ensure_ascii=False, indent=2).encode('utf-8')),
            f'{stem}_stand_summary.json', 'application/json',
            labels_resolved.source_asset_id(),
        )
        csv_text = io.StringIO()
        csv_text.write(
            'candidate_id,height_max_m,height_mean_m,crown_area_m2,'
            'equivalent_crown_diameter_m\n'
        )
        for row in rows:
            csv_text.write(
                f'{row["candidate_id"]},{row["height_max_m"]},'
                f'{row["height_mean_m"]},{row["crown_area_m2"]},'
                f'{row["equivalent_crown_diameter_m"]}\n'
            )
        csv_asset = self.register(
            io.BytesIO(csv_text.getvalue().encode('utf-8-sig')),
            f'{stem}_candidates.csv', 'text/csv', labels_resolved.source_asset_id(),
        )
        return {
            'summary': summary,
            'summary_artifact': json_asset,
            'candidate_table': csv_asset,
            'note': (
                '所有统计都以当前CHM有效范围和候选标签为准。没有独立外业或人工标注时，'
                '这些结果只用于候选筛查与参数敏感性比较。'
            ),
        }
