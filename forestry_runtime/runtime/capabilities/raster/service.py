"""Raster inspection, archive, index, segmentation, and preview services."""

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

def finite(value):
    return value if value is None or math.isfinite(value) else str(value)

class RasterCapability:
    def _open_input(self, scope='auto', asset_id=None, path='', source_id=None, *, role='input'):
        """Resolve one unified input reference and remember how to name it again.

        Every input-consuming capability opens its file through this, so a workspace
        file, an attachment and a file under an authorized directory are all
        acceptable with the same arguments -- and the reference that resolved is
        reported back with the result, which is what lets the next tool be called by
        copying rather than by guessing a conversion.
        """
        resolved = self.resolve_input(
            scope=scope, asset_id=asset_id, path=path, source_id=source_id,
        )
        references = getattr(self, '_input_references', None)
        if references is None:
            references = self._input_references = {}
        references[role] = dict(resolved.reference)
        return resolved.asset_view(), resolved.local_path, resolved

    @staticmethod
    def validate_tiff(path):
        # 与有效像元扫描分离，避免生成预览时也扫描整幅掩膜。
        with path.open('rb') as stream:
            signature = stream.read(4)
        if signature not in (
            b'II*\x00',
            b'MM\x00*',
            b'II+\x00',
            b'MM\x00+',
        ):
            raise AssetError('File is not a TIFF despite its extension')

    @staticmethod
    def raster_validity(src):
        flags_by_band = [
            [flag.name for flag in flags]
            for flags in src.mask_flag_enums
        ]

        # 表达观察到的证据，不猜测掩膜是内部文件还是外部旁车文件。
        default_all_valid = all(
            flags == ['all_valid'] for flags in flags_by_band
        )

        total_count = src.width * src.height

        if default_all_valid:
            # 无约束时读取器默认全部有效，无需扫描整幅全255掩膜。
            valid_count = total_count
            source = 'all_valid_default'
            note = (
                '各波段掩膜标志均为all_valid，读取器默认全部像元有效。'
                '这不代表已核验影像背景、拼接边缘或样地范围，'
                '也不代表已检查实际像元中的NaN或Inf。'
            )
        else:
            source = 'dataset_mask'
            valid_count = 0
            window_size = 1024

            for row in range(0, src.height, window_size):
                for col in range(0, src.width, window_size):
                    window = Window(
                        col,
                        row,
                        min(window_size, src.width - col),
                        min(window_size, src.height - row),
                    )
                    mask = src.dataset_mask(window=window)
                    valid_count += int(np.count_nonzero(mask))

            note = (
                '按Rasterio数据集掩膜的非零像元统计；'
                '掩膜依据见mask_flags_by_band。'
                '数据集有效区域不等于林冠区域，也不保证所选波段共同有效。'
                '指数计算仍需检查所用波段各自掩膜、NaN/Inf和分母。'
            )

        return {
            'source': source,
            'mask_flags_by_band': flags_by_band,
            'valid_pixel_count': valid_count,
            'invalid_pixel_count': total_count - valid_count,
            'total_pixel_count': total_count,
            'valid_fraction': (
                valid_count / total_count if total_count else None
            ),
            'note': note,
        }

    def inspect_file(self, scope='auto', asset_id=None, path='', source_id=None):
        asset, path, _ = self._open_input(scope, asset_id, path, source_id)
        ext = Path(asset.get('name') or path.name).suffix.lower()

        if ext in ('.tif', '.tiff'):
            self.validate_tiff(path)

            with rasterio.open(path, driver='GTiff') as src:
                bands = [
                    {
                        'index': index,
                        'description': src.descriptions[index - 1],
                        'dtype': src.dtypes[index - 1],
                        'color_interpretation': (
                            src.colorinterp[index - 1].name
                        ),
                    }
                    for index in src.indexes
                ]
                validity = self.raster_validity(src)

                if src.crs:
                    units = (
                        'degrees'
                        if src.crs.is_geographic
                        else src.crs.linear_units
                    )
                    wkt = src.crs.to_wkt()
                else:
                    units = 'unknown'
                    wkt = ''

                crs_name = re.match(r'[^\[]+\["([^"]+)"', wkt)
                projected = bool(src.crs and src.crs.is_projected)
                footprint = (
                    abs(
                        src.transform.a * src.transform.e
                        - src.transform.b * src.transform.d
                    )
                    * src.width
                    * src.height
                )

                return {
                    **asset,
                    'driver': src.driver,
                    'width': src.width,
                    'height': src.height,
                    'band_count': src.count,
                    'bands': bands,
                    # 保留旧字段，兼容已有界面与测试。
                    'band_descriptions': list(src.descriptions),
                    'color_interpretation': [
                        value.name for value in src.colorinterp
                    ],
                    'data_types': list(src.dtypes),
                    'crs': str(src.crs) if src.crs else None,
                    'crs_name': crs_name.group(1) if crs_name else None,
                    'pixel_size': list(src.res),
                    'coordinate_units': units,
                    'grid_footprint_area': (
                        round(footprint, 10) if projected else None
                    ),
                    'grid_footprint_area_units': (
                        units + '^2' if projected else None
                    ),
                    'area_note': (
                        '仅为整个像元网格的投影平面面积，'
                        '不等于有效像元面积、样地面积或森林面积。'
                    ),
                    'transform': list(src.transform)[:6],
                    'bounds': list(src.bounds),
                    'nodata': finite(src.nodata),
                    'validity': validity,
                    'note': (
                        'bands.index为从1开始的波段编号，description为'
                        '文件原始描述。明确描述可作为波段含义的元数据证据，'
                        '缺失描述不能凭数量猜测。'
                        'nodata为null仅表示未设置NoData数值；'
                        '有效区域另见validity。'
                        '本工具只检查元数据；calculate_ndvi可执行NDVI计算，'
                        '当前仍未执行林冠分割。'
                    ),
                }

        if ext in ('.png', '.jpg', '.jpeg', '.webp'):
            with Image.open(path) as image:
                return {
                    **asset,
                    'width': image.width,
                    'height': image.height,
                    'mode': image.mode,
                    'format': image.format,
                    'note': (
                        '未检查外部world file；'
                        '不能由图片像素直接得到地面面积。'
                    ),
                }

        return {
            **asset,
            'note': (
                '文件已保存；本工具未解析此格式的内部结构。'
                'ZIP使用inspect_zip，文本使用通用fs_read。'
            ),
        }

    def zip_entries(self, path):
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 1000:
                raise AssetError('ZIP exceeds 1000 entries')

            total = 0
            names = set()
            for entry in entries:
                name = entry.filename.replace('\\', '/')
                parts = PurePosixPath(name).parts

                if (
                    name.startswith('/')
                    or '..' in parts
                    or ':' in name
                    or '\x00' in name
                ):
                    raise AssetError('Unsafe ZIP path')

                if (
                    stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.flag_bits & 1
                ):
                    raise AssetError(
                        'Encrypted entries and symbolic links '
                        'are not supported'
                    )

                if name in names:
                    raise AssetError('Duplicate ZIP path')
                names.add(name)

                total += entry.file_size
                if (
                    total > 536870912
                    or entry.file_size > max(entry.compress_size, 1) * 200
                ):
                    raise AssetError(
                        'ZIP exceeds expanded-size or compression-ratio limit'
                    )

            return entries, total

    def inspect_zip(self, scope='auto', asset_id=None, path='', source_id=None):
        _, path, _ = self._open_input(scope, asset_id, path, source_id)
        entries, total = self.zip_entries(path)
        return {
            'entry_count': len(entries),
            'expanded_bytes': total,
            'entries': [
                {
                    'name': entry.filename,
                    'size': entry.file_size,
                    'directory': entry.is_dir(),
                }
                for entry in entries[:200]
            ],
            'listing_truncated': len(entries) > 200,
        }

    def extract_zip(self, scope='auto', asset_id=None, path='', source_id=None):
        _, path, resolved = self._open_input(scope, asset_id, path, source_id)
        entries, _ = self.zip_entries(path)
        files = []

        with zipfile.ZipFile(path) as archive:
            for entry in entries:
                if entry.is_dir():
                    continue
                with archive.open(entry) as stream:
                    asset = self.register(
                        stream,
                        entry.filename,
                        parent=resolved.source_asset_id(),
                        limit=536870912,
                    )
                files.append({
                    **asset,
                    'archive_path': entry.filename,
                })

        return {
            'files': files,
            'note': (
                '各文件单独保存，archive_path保留包内目录关系；'
                '不执行包内程序。'
            ),
        }

    @staticmethod
    def _band_role_matches(description, expected):
        """Require metadata evidence for every selected spectral role."""
        if not description:
            return False
        label = re.sub(r'[^a-z0-9]+', '', description.lower())
        if expected == 'red':
            return 'red' in label and 'rededge' not in label and 'infrared' not in label
        return label == 'nir' or 'nearinfrared' in label

    @staticmethod
    def _required_bands(bands, required):
        missing = [role for role in required if bands.get(role) is None]
        if missing:
            raise AssetError(
                '缺少必需波段角色：' + ', '.join(missing)
            )
        selected = {role: bands[role] for role in required}
        if len(set(selected.values())) != len(selected):
            raise AssetError('不同波段角色必须使用不同的波段编号')
        return selected

    def inspect_raster_region(
        self, scope='auto', asset_id=None, path='', source_id=None,
        band=1, window=None, max_size=512, stretch_percentiles=None,
    ):
        """A bounded look at a raster region: statistics plus the picture of them.

        The numbers and the image come from the *same* sampled array, so the model is
        checking a spatial result against the values that describe it rather than
        against a separately rescaled picture. Bounded on purpose -- one band, one
        window, at most ``max_size`` pixels on a side -- because the point is a
        controlled observation, not a full-resolution artifact.
        """
        asset, path, resolved = self._open_input(scope, asset_id, path, source_id)
        if Path(asset.get('name') or path.name).suffix.lower() not in ('.tif', '.tiff'):
            raise AssetError('Region inspection input must be a TIFF')
        self.validate_tiff(path)
        low_pct, high_pct = (stretch_percentiles or [2, 98])[:2]
        if not 0 <= low_pct < high_pct <= 100:
            raise AssetError('stretch_percentiles must be an increasing pair within 0..100')
        size = max(64, min(1024, int(max_size)))

        with rasterio.open(path, driver='GTiff') as src:
            if not 1 <= int(band) <= src.count:
                raise ToolPreconditionError(
                    'The requested band does not exist in this raster.',
                    code='raster_band_missing', reason='inapplicable',
                    requested_band=int(band), band_count=src.count,
                    checked_scope={'input': dict(resolved.reference)},
                )
            if window:
                if len(window) != 4:
                    raise AssetError('window must be [col_off, row_off, width, height]')
                col_off, row_off, width, height = (int(value) for value in window)
                if width <= 0 or height <= 0:
                    raise AssetError('window width and height must be positive')
                if (col_off < 0 or row_off < 0
                        or col_off + width > src.width or row_off + height > src.height):
                    raise ToolPreconditionError(
                        'The requested window falls outside the raster extent.',
                        code='raster_window_out_of_bounds', reason='invalid_arguments',
                        requested_window=[col_off, row_off, width, height],
                        raster_size={'width': src.width, 'height': src.height},
                    )
                region = Window(col_off, row_off, width, height)
            else:
                col_off, row_off, width, height = 0, 0, src.width, src.height
                region = Window(0, 0, src.width, src.height)

            factor = min(1.0, float(size) / max(1, max(width, height)))
            out_width = max(1, round(width * factor))
            out_height = max(1, round(height * factor))
            data = src.read(
                int(band), window=region,
                out_shape=(out_height, out_width),
                masked=True, resampling=Resampling.average,
            )
            values = data.compressed()
            values = values[np.isfinite(values)]
            stats = {
                'band': int(band),
                'band_description': src.descriptions[int(band) - 1],
                'dtype': src.dtypes[int(band) - 1],
                'window': [col_off, row_off, width, height],
                'raster_size': {'width': src.width, 'height': src.height},
                'sampled_shape': {'width': out_width, 'height': out_height},
                'sampled_pixels': int(out_height * out_width),
                'valid_pixels': int(values.size),
                'valid_fraction': (
                    float(values.size) / float(out_height * out_width)
                    if out_height * out_width else None
                ),
                'min': float(values.min()) if values.size else None,
                'max': float(values.max()) if values.size else None,
                'mean': float(values.mean()) if values.size else None,
                'percentiles': (
                    {
                        f'p{int(p)}': float(np.percentile(values, p))
                        for p in (1, 5, 25, 50, 75, 95, 99)
                    } if values.size else {}
                ),
                'nodata': finite(src.nodata),
                'crs': str(src.crs) if src.crs else None,
                'note': (
                    'Statistics describe the sampled window of this band, not the whole '
                    'raster and not the meaning of its values.'
                ),
            }
            # Stretch from the sampled values rather than from the whole band: the
            # picture and the numbers above must describe the same pixels.
            if values.size:
                low, high = np.percentile(values, [low_pct, high_pct])
            else:
                low, high = 0.0, 1.0
            pixels = np.asarray(data.filled(float(low)), dtype='float64')
            pixels = np.nan_to_num((pixels - low) / max(float(high - low), 1e-9))
            grey = (np.clip(pixels, 0, 1) * 255).astype('uint8')
            image = Image.fromarray(grey, mode='L')

        output = io.BytesIO()
        image.save(output, format='PNG')
        output.seek(0)
        thumbnail = self.register(
            output,
            f'{Path(asset.get("name") or path.name).stem}_band{int(band)}_region.png',
            'image/png', resolved.source_asset_id(),
            metadata={
                'derived_from': dict(resolved.reference),
                'preview_method': 'sampled-window-stretch',
                'stretch_percentiles': [low_pct, high_pct],
            },
        )
        return {
            'thumbnail': thumbnail,
            'statistics': stats,
            'stretch': {
                'percentiles': [low_pct, high_pct],
                'low_value': float(low), 'high_value': float(high),
            },
            # The harness attaches these bytes to the model request when the model
            # accepts images; when it does not, the asset and its URL remain, so the
            # user can still look at exactly what the model described.
            'attach_image': {'asset_id': thumbnail['id'], 'media_type': 'image/png'},
            'note': (
                'This is a bounded look at one band and one window, stretched for '
                'display. It is evidence about spatial arrangement, not a measurement '
                'of accuracy and not a classification result.'
            ),
        }

    @staticmethod
    def _raster_roles(src):
        roles = {'red': None, 'nir': None, 'alpha': None}
        for index in src.indexes:
            description = src.descriptions[index - 1]
            label = re.sub(
                r'[^a-z0-9]+', '', (description or '').lower()
            )
            if label == 'red':
                roles['red'] = index
            elif label in ('nir', 'nearinfrared'):
                roles['nir'] = index
            if label == 'alpha' or src.colorinterp[index - 1].name == 'alpha':
                roles['alpha'] = index
        return roles

    def inspect_raster(self, scope='auto', asset_id=None, path='', source_id=None, band_indices=None):
        asset, path, _ = self._open_input(scope, asset_id, path, source_id)
        if Path(asset.get('name') or path.name).suffix.lower() not in ('.tif', '.tiff'):
            raise AssetError('Raster inspection input must be a TIFF')
        self.validate_tiff(path)

        with rasterio.open(path, driver='GTiff') as src:
            selected = list(band_indices or src.indexes)
            if not selected:
                raise AssetError('Select at least one raster band')
            if len(selected) > 16:
                raise AssetError(
                    'Raster has more than 16 bands; specify band_indices'
                )
            if len(set(selected)) != len(selected):
                raise AssetError('band_indices must not contain duplicates')
            if any(index < 1 or index > src.count for index in selected):
                raise AssetError(
                    f'Band index must be between 1 and {src.count}'
                )

            total = src.width * src.height
            sample_stride = max(1, math.ceil(total / 200000))
            accumulators = {
                index: {
                    'mask_invalid_pixel_count': 0,
                    'nonfinite_pixel_count': 0,
                    'finite_pixel_count': 0,
                    'zero_pixel_count': 0,
                    'negative_pixel_count': 0,
                    'positive_pixel_count': 0,
                    'minimum': math.inf,
                    'maximum': -math.inf,
                    'sum': 0.0,
                    'samples': [],
                }
                for index in selected
            }
            roles = self._raster_roles(src)
            pair_available = (
                roles['red'] in selected and roles['nir'] in selected
            )
            alpha_available = roles['alpha'] in selected
            pair = {
                'common_valid_pixel_count': 0,
                'invalid_pixel_count': 0,
                'both_zero_pixel_count': 0,
                'zero_denominator_pixel_count': 0,
                'zero_denominator_and_alpha_nonpositive_pixel_count': 0,
                'zero_denominator_and_alpha_positive_pixel_count': 0,
                'zero_denominator_and_alpha_invalid_pixel_count': 0,
                'alpha_nonpositive_and_nonzero_denominator_pixel_count': 0,
            }
            alpha = {
                'valid_pixel_count': 0,
                'invalid_pixel_count': 0,
                'positive_pixel_count': 0,
                'nonpositive_pixel_count': 0,
            }
            for _, window in src.block_windows(selected[0]):
                raw = src.read(selected, window=window)
                masks = src.read_masks(selected, window=window) != 0
                values_by_index = {}
                valid_by_index = {}

                for position, index in enumerate(selected):
                    values = (
                        raw[position].astype('float64')
                        * float(src.scales[index - 1])
                        + float(src.offsets[index - 1])
                    )
                    mask_valid = masks[position]
                    finite_values = np.isfinite(values)
                    valid = mask_valid & finite_values
                    observed = values[valid]
                    stats = accumulators[index]
                    stats['mask_invalid_pixel_count'] += int(
                        np.count_nonzero(~mask_valid)
                    )
                    stats['nonfinite_pixel_count'] += int(
                        np.count_nonzero(mask_valid & ~finite_values)
                    )
                    stats['finite_pixel_count'] += int(observed.size)
                    if observed.size:
                        stats['zero_pixel_count'] += int(
                            np.count_nonzero(observed == 0)
                        )
                        stats['negative_pixel_count'] += int(
                            np.count_nonzero(observed < 0)
                        )
                        stats['positive_pixel_count'] += int(
                            np.count_nonzero(observed > 0)
                        )
                        stats['minimum'] = min(
                            stats['minimum'], float(np.min(observed))
                        )
                        stats['maximum'] = max(
                            stats['maximum'], float(np.max(observed))
                        )
                        stats['sum'] += float(
                            np.sum(observed, dtype='float64')
                        )
                        sample = observed[::sample_stride]
                        if sample.size:
                            stats['samples'].append(sample.copy())
                    values_by_index[index] = values
                    valid_by_index[index] = valid

                alpha_defined = None
                alpha_positive = None
                alpha_nonpositive = None
                if alpha_available:
                    alpha_index = roles['alpha']
                    alpha_values = values_by_index[alpha_index]
                    alpha_defined = valid_by_index[alpha_index]
                    alpha_positive = alpha_defined & (alpha_values > 0)
                    alpha_nonpositive = alpha_defined & (alpha_values <= 0)
                    alpha['valid_pixel_count'] += int(
                        np.count_nonzero(alpha_defined)
                    )
                    alpha['invalid_pixel_count'] += int(
                        np.count_nonzero(~alpha_defined)
                    )
                    alpha['positive_pixel_count'] += int(
                        np.count_nonzero(alpha_positive)
                    )
                    alpha['nonpositive_pixel_count'] += int(
                        np.count_nonzero(alpha_nonpositive)
                    )

                if pair_available:
                    red_values = values_by_index[roles['red']]
                    nir_values = values_by_index[roles['nir']]
                    common = (
                        valid_by_index[roles['red']]
                        & valid_by_index[roles['nir']]
                    )
                    zero_denominator = common & ((red_values + nir_values) == 0)
                    nonzero_denominator = common & ~zero_denominator
                    pair['common_valid_pixel_count'] += int(
                        np.count_nonzero(common)
                    )
                    pair['invalid_pixel_count'] += int(
                        common.size - np.count_nonzero(common)
                    )
                    pair['both_zero_pixel_count'] += int(
                        np.count_nonzero(
                            common & (red_values == 0) & (nir_values == 0)
                        )
                    )
                    pair['zero_denominator_pixel_count'] += int(
                        np.count_nonzero(zero_denominator)
                    )
                    if alpha_available:
                        pair[
                            'zero_denominator_and_alpha_nonpositive_pixel_count'
                        ] += int(np.count_nonzero(
                            zero_denominator & alpha_nonpositive
                        ))
                        pair[
                            'zero_denominator_and_alpha_positive_pixel_count'
                        ] += int(np.count_nonzero(
                            zero_denominator & alpha_positive
                        ))
                        pair[
                            'zero_denominator_and_alpha_invalid_pixel_count'
                        ] += int(np.count_nonzero(
                            zero_denominator & ~alpha_defined
                        ))
                        pair[
                            'alpha_nonpositive_and_nonzero_denominator_pixel_count'
                        ] += int(np.count_nonzero(
                            nonzero_denominator & alpha_nonpositive
                        ))

            band_statistics = []
            for index in selected:
                stats = accumulators[index]
                finite_count = stats['finite_pixel_count']
                samples = (
                    np.concatenate(stats['samples'])
                    if stats['samples'] else np.array([], dtype='float64')
                )
                quantiles = (
                    np.percentile(samples, [1, 5, 25, 50, 75, 95, 99])
                    if samples.size else [math.nan] * 7
                )
                band_statistics.append({
                    'index': index,
                    'description': src.descriptions[index - 1],
                    'color_interpretation': src.colorinterp[index - 1].name,
                    'dtype': src.dtypes[index - 1],
                    'scale': float(src.scales[index - 1]),
                    'offset': float(src.offsets[index - 1]),
                    'total_pixel_count': total,
                    'mask_invalid_pixel_count': stats[
                        'mask_invalid_pixel_count'
                    ],
                    'nonfinite_pixel_count': stats[
                        'nonfinite_pixel_count'
                    ],
                    'finite_pixel_count': finite_count,
                    'zero_pixel_count': stats['zero_pixel_count'],
                    'negative_pixel_count': stats['negative_pixel_count'],
                    'positive_pixel_count': stats['positive_pixel_count'],
                    'minimum': finite(stats['minimum']) if finite_count else None,
                    'maximum': finite(stats['maximum']) if finite_count else None,
                    'mean': stats['sum'] / finite_count if finite_count else None,
                    'sample_quantiles': {
                        name: finite(float(value))
                        for name, value in zip(
                            ('p01', 'p05', 'p25', 'p50', 'p75', 'p95', 'p99'),
                            quantiles,
                        )
                    },
                    'quantile_sample_count': int(samples.size),
                })

            projected = bool(src.crs and src.crs.is_projected)
            dataset_tags = {str(key): str(value) for key, value in src.tags().items()}
            vertical_reference = next((
                dataset_tags[key] for key in (
                    'vertical_reference', 'vertical_datum', 'vert_datum',
                    'VERTICAL_DATUM', 'VERTICAL_REFERENCE',
                ) if dataset_tags.get(key)
            ), None)
            elevation_units = next((
                dataset_tags[key] for key in (
                    'vertical_units', 'elevation_units',
                    'ELEVATION_UNITS', 'VERTICAL_UNITS',
                ) if dataset_tags.get(key)
            ), None)
            pixel_area = (
                abs(
                    src.transform.a * src.transform.e
                    - src.transform.b * src.transform.d
                ) if projected else None
            )
            if pair_available:
                pair.update({
                    'red_band': roles['red'],
                    'nir_band': roles['nir'],
                    'common_valid_fraction': (
                        pair['common_valid_pixel_count'] / total if total else None
                    ),
                    'common_valid_area': (
                        pair['common_valid_pixel_count'] * pixel_area
                        if pixel_area is not None else None
                    ),
                })

            return {
                **asset,
                'driver': src.driver,
                'width': src.width,
                'height': src.height,
                'crs': str(src.crs) if src.crs else None,
                'pixel_area': pixel_area,
                'area_units': (
                    src.crs.linear_units + '^2' if projected else None
                ),
                'roles_from_metadata': roles,
                # The recorded geospatial metadata, verbatim.  Downstream tools
                # (the CHM builder in particular) require values that only exist
                # here -- a vertical datum, an elevation unit -- and a mismatch is
                # reported as a refusal.  Without this block the Agent can see that
                # its call was rejected but has no way to learn the value the
                # environment will accept, which turns a data-quality check into a
                # guessing game.
                'dataset_tags': dataset_tags,
                'vertical_reference': vertical_reference,
                'elevation_units': elevation_units,
                'band_statistics': band_statistics,
                'red_nir_pair': pair if pair_available else None,
                'alpha_analysis': (
                    {'band': roles['alpha'], **alpha}
                    if alpha_available else None
                ),
                'quantile_method': (
                    'deterministic stride sample of finite, mask-valid values; '
                    'counts, min, max and mean are exact windowed scans'
                ),
                'note': (
                    '本工具报告像元证据，不自动把Alpha或零值定义为有效区。'
                    'Red/NIR角色仅来自明确波段描述；Alpha可来自描述或'
                    'color_interpretation。是否采用Alpha作为分析掩膜应根据'
                    '重合统计决定。'
                ),
            }

    @staticmethod
    def _otsu_threshold(histogram, edges):
        counts = histogram.astype('float64')
        total = float(np.sum(counts))
        if total <= 0 or np.count_nonzero(counts) < 2:
            raise AssetError(
                'Otsu threshold requires at least two populated NDVI bins'
            )
        centers = (edges[:-1] + edges[1:]) / 2
        weight_low = np.cumsum(counts)
        weighted_low = np.cumsum(counts * centers)
        weight_high = total - weight_low
        valid = (weight_low > 0) & (weight_high > 0)
        between = np.full(counts.shape, -np.inf, dtype='float64')
        total_weighted = weighted_low[-1]
        between[valid] = (
            (total_weighted * weight_low[valid] - weighted_low[valid] * total)
            ** 2
            / (weight_low[valid] * weight_high[valid])
        )
        return float(centers[int(np.argmax(between))])

    def segment_canopy(self, scope='auto', asset_id=None, path='', source_id=None, threshold=None):
        asset, path, resolved = self._open_input(scope, asset_id, path, source_id)
        if Path(asset.get('name') or path.name).suffix.lower() not in ('.tif', '.tiff'):
            raise AssetError('Canopy segmentation input must be a TIFF')
        self.validate_tiff(path)
        temp_path = None
        try:
            with rasterio.open(path, driver='GTiff') as src:
                algorithm_tag = (src.tags().get('algorithm') or '').upper()
                description = (src.descriptions[0] or '').upper()
                if src.count != 1 or (
                    algorithm_tag != 'NDVI' and description != 'NDVI'
                ):
                    raise AssetError(
                        'segment_canopy requires a single-band NDVI asset '
                        'created by calculate_ndvi'
                    )

                histogram = np.zeros(512, dtype='int64')
                edges = np.linspace(-1, 1, 513)
                valid_count = 0
                outside_range_count = 0
                source_min = math.inf
                source_max = -math.inf
                for _, window in src.block_windows(1):
                    values = src.read(1, window=window).astype('float64')
                    valid = (
                        (src.read_masks(1, window=window) != 0)
                        & np.isfinite(values)
                    )
                    observed = values[valid]
                    valid_count += int(observed.size)
                    if observed.size:
                        source_min = min(source_min, float(np.min(observed)))
                        source_max = max(source_max, float(np.max(observed)))
                        within = observed[(observed >= -1) & (observed <= 1)]
                        histogram += np.histogram(
                            within, bins=edges
                        )[0].astype('int64')
                        outside_range_count += int(
                            observed.size - within.size
                        )
                if valid_count == 0:
                    raise ToolPreconditionError(
                        'NDVI raster contains no valid pixels; canopy segmentation cannot run.',
                        code='no_valid_ndvi_pixels', reason='inapplicable',
                        missing=[{'kind': 'valid_ndvi_pixels', 'input': dict(resolved.reference)}],
                        checked_scope={'input': dict(resolved.reference), 'bands': [1],
                                       'windows_scanned': 'all'},
                        valid_pixels=0, width=src.width, height=src.height,
                    )

                if threshold is None:
                    selected_threshold = self._otsu_threshold(histogram, edges)
                    threshold_source = 'otsu_512_bins_over_ndvi_-1_to_1'
                else:
                    selected_threshold = float(threshold)
                    threshold_source = 'explicit'

                profile = src.profile.copy()
                profile.update(
                    driver='GTiff', count=1, dtype='uint8', nodata=255,
                    compress='deflate', predictor=2, BIGTIFF='IF_SAFER',
                )
                profile.pop('photometric', None)
                profile.pop('interleave', None)
                handle = tempfile.NamedTemporaryFile(
                    suffix='.tif', dir=self.store.root, delete=False
                )
                temp_path = Path(handle.name)
                handle.close()

                candidate_count = 0
                with rasterio.open(temp_path, 'w', **profile) as dst:
                    dst.set_band_description(1, 'canopy_candidate_mask')
                    dst.update_tags(
                        algorithm='NDVI_THRESHOLD_CANOPY_CANDIDATE',
                        source_asset_id=resolved.source_asset_id() or '',
                        source_reference=json.dumps(resolved.reference, ensure_ascii=False),
                        threshold=str(selected_threshold),
                        threshold_source=threshold_source,
                        class_0='valid_non_candidate',
                        class_1='canopy_candidate',
                        nodata_class='255',
                    )
                    for _, window in src.block_windows(1):
                        values = src.read(1, window=window).astype('float64')
                        valid = (
                            (src.read_masks(1, window=window) != 0)
                            & np.isfinite(values)
                        )
                        result = np.full(values.shape, 255, dtype='uint8')
                        candidate = valid & (values >= selected_threshold)
                        result[valid] = 0
                        result[candidate] = 1
                        candidate_count += int(np.count_nonzero(candidate))
                        dst.write(result, 1, window=window)

                projected = bool(src.crs and src.crs.is_projected)
                pixel_area = (
                    abs(
                        src.transform.a * src.transform.e
                        - src.transform.b * src.transform.d
                    ) if projected else None
                )
                area_units = (
                    src.crs.linear_units + '^2' if projected else None
                )

            threshold_label = (
                f'{selected_threshold:.4f}'
                .replace('-', 'm')
                .replace('.', 'p')
            )
            output_name = (
                f'{Path(asset.get("name") or path.name).stem}_canopy_candidate_'
                f'{threshold_source.split("_")[0]}_t{threshold_label}.tif'
            )
            with temp_path.open('rb') as stream:
                output_asset = self.register(
                    stream, output_name, 'image/tiff', resolved.source_asset_id()
                )
            return {
                'mask': output_asset,
                'method': 'ndvi_threshold_baseline',
                'threshold': selected_threshold,
                'threshold_source': threshold_source,
                'classes': {
                    '0': 'valid_non_candidate',
                    '1': 'canopy_candidate',
                    '255': 'invalid_nodata',
                },
                'statistics': {
                    'valid_pixel_count': valid_count,
                    'candidate_pixel_count': candidate_count,
                    'non_candidate_pixel_count': valid_count - candidate_count,
                    'candidate_fraction_of_valid': candidate_count / valid_count,
                    'candidate_area': (
                        candidate_count * pixel_area
                        if pixel_area is not None else None
                    ),
                    'valid_area': (
                        valid_count * pixel_area
                        if pixel_area is not None else None
                    ),
                    'area_units': area_units,
                    'source_ndvi_minimum': source_min,
                    'source_ndvi_maximum': source_max,
                    'outside_expected_ndvi_range_pixel_count': (
                        outside_range_count
                    ),
                },
                'note': (
                    '这是用于打通工作流的NDVI阈值候选Mask。1表示光谱上满足阈值的'
                    '植被候选像元，不证明其为木本树冠；草地、灌丛和其他高NDVI地物'
                    '可能被包含。candidate_fraction_of_valid不能直接作为最终林冠覆盖率。'
                ),
            }
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def calculate_ndvi(self, scope='auto', asset_id=None, path='', source_id=None, bands=None):
        asset, path, resolved = self._open_input(scope, asset_id, path, source_id)
        if Path(asset.get('name') or path.name).suffix.lower() not in ('.tif', '.tiff'):
            raise AssetError('NDVI input must be a TIFF')
        selected = self._required_bands(bands, ('red', 'nir'))
        red_band = selected['red']
        nir_band = selected['nir']

        self.validate_tiff(path)
        temp_path = None
        try:
            with rasterio.open(path, driver='GTiff') as src:
                if red_band > src.count or nir_band > src.count:
                    raise AssetError(
                        f'Band index exceeds source band count ({src.count})'
                    )
                red_description = src.descriptions[red_band - 1]
                nir_description = src.descriptions[nir_band - 1]
                if not self._band_role_matches(red_description, 'red'):
                    raise AssetError(
                        f'Band {red_band} lacks metadata evidence for the Red role '
                        f'(description={red_description!r})'
                    )
                if not self._band_role_matches(nir_description, 'nir'):
                    raise AssetError(
                        f'Band {nir_band} lacks metadata evidence for the NIR role '
                        f'(description={nir_description!r})'
                    )

                red_scale = float(src.scales[red_band - 1])
                red_offset = float(src.offsets[red_band - 1])
                nir_scale = float(src.scales[nir_band - 1])
                nir_offset = float(src.offsets[nir_band - 1])
                profile = src.profile.copy()
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

                valid_count = 0
                masked_count = 0
                nonfinite_count = 0
                zero_denominator_count = 0
                out_of_range_count = 0
                value_sum = 0.0
                value_min = math.inf
                value_max = -math.inf

                with rasterio.open(temp_path, 'w', **profile) as dst:
                    dst.set_band_description(1, 'NDVI')
                    dst.update_tags(
                        algorithm='NDVI', formula='(NIR-Red)/(NIR+Red)',
                        source_asset_id=resolved.source_asset_id() or '',
                        source_reference=json.dumps(resolved.reference, ensure_ascii=False),
                        red_band=str(red_band), nir_band=str(nir_band),
                        red_scale=str(red_scale), red_offset=str(red_offset),
                        nir_scale=str(nir_scale), nir_offset=str(nir_offset),
                    )
                    for _, window in src.block_windows(red_band):
                        red_raw = src.read(red_band, window=window)
                        nir_raw = src.read(nir_band, window=window)
                        masks = (
                            (src.read_masks(red_band, window=window) != 0)
                            & (src.read_masks(nir_band, window=window) != 0)
                        )
                        red = red_raw.astype('float64') * red_scale + red_offset
                        nir = nir_raw.astype('float64') * nir_scale + nir_offset
                        finite_inputs = np.isfinite(red) & np.isfinite(nir)
                        denominator = nir + red
                        nonzero = denominator != 0
                        valid = masks & finite_inputs & nonzero
                        result = np.full(red.shape, np.nan, dtype='float32')
                        with np.errstate(divide='ignore', invalid='ignore'):
                            values = (nir[valid] - red[valid]) / denominator[valid]
                        finite_output = np.isfinite(values)
                        if not np.all(finite_output):
                            valid_positions = np.flatnonzero(valid)
                            valid.flat[valid_positions[~finite_output]] = False
                            values = values[finite_output]
                        result[valid] = values.astype('float32')
                        dst.write(result, 1, window=window)

                        count = int(values.size)
                        valid_count += count
                        masked_count += int(np.count_nonzero(~masks))
                        nonfinite_count += int(np.count_nonzero(masks & ~finite_inputs))
                        zero_denominator_count += int(
                            np.count_nonzero(masks & finite_inputs & ~nonzero)
                        )
                        if count:
                            value_sum += float(np.sum(values, dtype='float64'))
                            value_min = min(value_min, float(np.min(values)))
                            value_max = max(value_max, float(np.max(values)))
                            out_of_range_count += int(
                                np.count_nonzero((values < -1) | (values > 1))
                            )

                if valid_count == 0:
                    raise AssetError('No valid pixels remain for NDVI calculation')

            output_name = (
                f'{Path(asset.get("name") or path.name).stem}_ndvi_r{red_band}_n{nir_band}.tif'
            )
            with temp_path.open('rb') as stream:
                output_asset = self.register(
                    stream, output_name, 'image/tiff', resolved.source_asset_id()
                )
            return {
                'ndvi': output_asset,
                'formula': '(NIR - Red) / (NIR + Red)',
                'bands_used': {
                    'red': {'index': red_band, 'description': red_description,
                            'scale': red_scale, 'offset': red_offset},
                    'nir': {'index': nir_band, 'description': nir_description,
                            'scale': nir_scale, 'offset': nir_offset},
                },
                'statistics': {
                    'valid_pixel_count': valid_count,
                    'invalid_pixel_count': (
                        masked_count + nonfinite_count + zero_denominator_count
                    ),
                    'masked_pixel_count': masked_count,
                    'nonfinite_input_pixel_count': nonfinite_count,
                    'zero_denominator_pixel_count': zero_denominator_count,
                    'minimum': value_min,
                    'maximum': value_max,
                    'mean': value_sum / valid_count,
                    'outside_expected_range_pixel_count': out_of_range_count,
                },
                'note': (
                    '结果保留源影像网格、坐标系和仿射变换。有效像元要求Red与NIR'
                    '两波段掩膜均有效、输入有限且分母非零。NDVI不是林冠Mask，'
                    '也不能单独证明传感器定标或林冠分类有效。'
                ),
            }
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def preview_image(self, scope='auto', asset_id=None, path='', source_id=None):
        asset, path, resolved = self._open_input(scope, asset_id, path, source_id)
        ext = Path(asset.get('name') or path.name).suffix.lower()
        bands = None

        if ext in ('.tif', '.tiff'):
            self.validate_tiff(path)
            with rasterio.open(path, driver='GTiff') as src:
                factor = min(1, 1024 / max(src.width, src.height))
                height = max(1, round(src.height * factor))
                width = max(1, round(src.width * factor))
                bands = (
                    list(range(1, min(src.count, 3) + 1))
                    if src.count >= 3 else [1]
                )
                data = src.read(
                    bands,
                    out_shape=(len(bands), height, width),
                    masked=True,
                    resampling=Resampling.average,
                )

                rgb = []
                for band in data:
                    valid = band.compressed()
                    valid = valid[np.isfinite(valid)]
                    lo, hi = (
                        np.percentile(valid, [2, 98])
                        if valid.size else (0, 1)
                    )
                    pixels = np.asarray(band.filled(float(lo)), dtype=float)
                    pixels = np.nan_to_num(
                        (pixels - lo) / max(float(hi - lo), 1e-9)
                    )
                    rgb.append(
                        (np.clip(pixels, 0, 1) * 255).astype('uint8')
                    )

                image = Image.fromarray(
                    np.moveaxis(
                        np.stack(rgb if len(rgb) == 3 else rgb * 3),
                        0,
                        -1,
                    )
                )

        elif ext in ('.png', '.jpg', '.jpeg', '.webp'):
            with Image.open(path) as source:
                source.thumbnail((1024, 1024))
                image = source.convert('RGB')
        else:
            raise AssetError(
                'Preview supports TIFF, PNG, JPG and WEBP only'
            )

        output = io.BytesIO()
        image.save(output, format='PNG')
        output.seek(0)

        return {
            'preview': self.register(
                output,
                Path(asset.get('name') or path.name).stem + '_preview.png',
                'image/png',
                resolved.source_asset_id(),
                metadata={
                    'derived_from': dict(resolved.reference),
                    'preview_method': 'band-stretch',
                    'bands_used': bands,
                },
            ),
            'bands_used': bands,
            'note': '预览经过缩放/拉伸，不是可用于定量分析的原始数据。',
        }
