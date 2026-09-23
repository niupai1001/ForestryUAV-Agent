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
from ..uav_audit.audit import GEOSPATIAL_SUFFIXES, IMAGE_SUFFIXES
from .tool import PROSAIL_PARAMETER_NAMES

class ProsailCapability:
    @staticmethod
    def _run_prosail(parameters, geometry, model):
        spectrum = prosail.run_prosail(
            parameters['n'], parameters['cab'], parameters['car'],
            parameters['cbrown'], parameters['cw'], parameters['cm'],
            parameters['lai'], parameters['lidfa'], parameters['hspot'],
            geometry['solar_zenith'], geometry['view_zenith'],
            geometry['relative_azimuth'], ant=parameters['ant'],
            alpha=parameters['alpha'],
            prospect_version=model['prospect_version'],
            typelidf=model['typelidf'], factor=model['factor'],
            rsoil=parameters['rsoil'], psoil=parameters['psoil'],
        )
        values = np.asarray(spectrum, dtype='float32')
        if values.shape != (2101,) or not np.all(np.isfinite(values)):
            raise AssetError('PROSAIL未返回400—2500 nm范围内的有效连续光谱')
        return values

    @staticmethod
    def _aggregate_prosail_bands(spectrum, sensor_bands):
        wavelengths = np.arange(400, 2501, dtype='float32')
        values = []
        for band in sensor_bands:
            selected = (
                (wavelengths >= band['lower_nm'])
                & (wavelengths <= band['upper_nm'])
            )
            if not np.any(selected):
                raise AssetError(f'波段{band["name"]!r}未覆盖PROSAIL波长采样点')
            values.append(float(np.mean(spectrum[selected])))
        return np.asarray(values, dtype='float32')

    def simulate_prosail(
        self, parameters, geometry, model, sensor_bands,
        parameter_source, geometry_source, sensor_response_source,
    ):
        spectrum = self._run_prosail(parameters, geometry, model)
        band_values = self._aggregate_prosail_bands(spectrum, sensor_bands)
        rows = ['wavelength_nm,reflectance']
        rows.extend(
            f'{wavelength},{float(reflectance):.9g}'
            for wavelength, reflectance in zip(range(400, 2501), spectrum)
        )
        stream = io.BytesIO(('\n'.join(rows) + '\n').encode('utf-8'))
        spectrum_asset = self.register(
            stream, 'prosail_spectrum.csv', 'text/csv'
        )
        return {
            'spectrum': spectrum_asset,
            'band_reflectance': {
                band['name']: float(value)
                for band, value in zip(sensor_bands, band_values)
            },
            'parameters': parameters,
            'geometry': geometry,
            'model': model,
            'sources': {
                'parameters': parameter_source,
                'geometry': geometry_source,
                'sensor_response': sensor_response_source,
            },
        }

    def build_prosail_lut(
        self, parameter_ranges, geometry, model, sensor_bands, lut_size,
        seed, sampling, parameter_source, geometry_source,
        sensor_response_source,
    ):
        bounds = np.asarray([
            [
                parameter_ranges[name]['minimum'],
                parameter_ranges[name]['maximum'],
            ]
            for name in PROSAIL_PARAMETER_NAMES
        ], dtype='float64')
        if sampling == 'latin_hypercube':
            unit_samples = qmc.LatinHypercube(
                d=len(PROSAIL_PARAMETER_NAMES), seed=seed
            ).random(n=lut_size)
        else:
            unit_samples = np.random.default_rng(seed).random(
                (lut_size, len(PROSAIL_PARAMETER_NAMES))
            )
        samples = (
            bounds[:, 0] + unit_samples * (bounds[:, 1] - bounds[:, 0])
        ).astype('float32')
        reflectance = np.empty(
            (lut_size, len(sensor_bands)), dtype='float32'
        )
        for row_index, row in enumerate(samples):
            parameters = dict(zip(PROSAIL_PARAMETER_NAMES, map(float, row)))
            spectrum = self._run_prosail(parameters, geometry, model)
            reflectance[row_index] = self._aggregate_prosail_bands(
                spectrum, sensor_bands
            )

        metadata = {
            'geometry': geometry,
            'model': model,
            'sensor_bands': sensor_bands,
            'parameter_ranges': parameter_ranges,
            'lut_size': lut_size,
            'seed': seed,
            'sampling': sampling,
            'sources': {
                'parameters': parameter_source,
                'geometry': geometry_source,
                'sensor_response': sensor_response_source,
            },
        }
        handle = tempfile.NamedTemporaryFile(
            suffix='.npz', dir=self.store.root, delete=False
        )
        temp_path = Path(handle.name)
        handle.close()
        try:
            arrays = {
                'reflectance': reflectance,
                'band_names': np.asarray(
                    [band['name'] for band in sensor_bands], dtype='U80'
                ),
                'parameter_names': np.asarray(
                    PROSAIL_PARAMETER_NAMES, dtype='U16'
                ),
                'metadata_json': np.asarray(
                    json.dumps(metadata, ensure_ascii=False)
                ),
            }
            arrays.update({
                f'parameter_{name}': samples[:, index]
                for index, name in enumerate(PROSAIL_PARAMETER_NAMES)
            })
            np.savez_compressed(temp_path, **arrays)
            with temp_path.open('rb') as stream:
                lut_asset = self.register(
                    stream, 'prosail_lut.npz', 'application/octet-stream'
                )
        finally:
            temp_path.unlink(missing_ok=True)
        return {
            'lut': lut_asset,
            'row_count': lut_size,
            'band_names': [band['name'] for band in sensor_bands],
            'parameter_names': list(PROSAIL_PARAMETER_NAMES),
            'configuration': metadata,
        }

    def invert_prosail(
        self, asset_id, lut_asset_id, band_mapping, target_parameters,
        alpha_band, neighbors, band_mapping_source,
    ):
        asset, raster_path = self.asset(asset_id)
        lut_asset, lut_path = self.asset(lut_asset_id)
        if Path(asset['name']).suffix.lower() not in ('.tif', '.tiff'):
            raise AssetError('PROSAIL反演输入必须是GeoTIFF反射率影像')
        if Path(lut_asset['name']).suffix.lower() != '.npz':
            raise AssetError('PROSAIL LUT必须是build_prosail_lut生成的NPZ资产')
        self.validate_tiff(raster_path)

        with np.load(lut_path, allow_pickle=False) as lut_file:
            required_keys = {'reflectance', 'band_names', 'parameter_names'}
            if not required_keys.issubset(lut_file.files):
                raise AssetError('LUT缺少反射率、波段名称或参数名称')
            reflectance = np.asarray(
                lut_file['reflectance'], dtype='float32'
            )
            lut_band_names = [str(value) for value in lut_file['band_names']]
            parameter_names = {
                str(value) for value in lut_file['parameter_names']
            }
            parameter_values = {}
            for name in target_parameters:
                key = f'parameter_{name}'
                if name not in parameter_names or key not in lut_file.files:
                    raise AssetError(f'LUT不包含待反演参数{name!r}')
                parameter_values[name] = np.asarray(
                    lut_file[key], dtype='float32'
                )

        if (
            reflectance.ndim != 2
            or reflectance.shape[0] < 1
            or reflectance.shape[1] != len(lut_band_names)
        ):
            raise AssetError('LUT反射率矩阵形状无效')
        if not np.all(np.isfinite(reflectance)):
            raise AssetError('LUT包含NaN或无穷值')
        if np.any((reflectance < 0) | (reflectance > 1)):
            raise AssetError('LUT反射率必须使用0到1范围的无量纲反射率比例')
        if neighbors > reflectance.shape[0]:
            raise AssetError('neighbors不能大于LUT记录数')
        for name, values in parameter_values.items():
            if values.shape != (reflectance.shape[0],):
                raise AssetError(f'LUT参数{name!r}的记录数不一致')

        lut_columns = {name: index for index, name in enumerate(lut_band_names)}
        unknown = [
            item['lut_band'] for item in band_mapping
            if item['lut_band'] not in lut_columns
        ]
        if unknown:
            raise AssetError('LUT中不存在波段：' + ', '.join(unknown))
        requested_raster_bands = [
            item['raster_band'] for item in band_mapping
        ]
        requested_lut_columns = [
            lut_columns[item['lut_band']] for item in band_mapping
        ]
        selected_reflectance = reflectance[:, requested_lut_columns]
        scale = np.std(selected_reflectance, axis=0).astype('float32')
        scale[scale < 1e-6] = 1.0
        tree = cKDTree(selected_reflectance / scale)

        temp_path = None
        try:
            with rasterio.open(raster_path, driver='GTiff') as src:
                all_bands = requested_raster_bands + (
                    [alpha_band] if alpha_band is not None else []
                )
                if max(all_bands) > src.count:
                    raise AssetError(
                        f'波段编号超出源影像波段数{src.count}'
                    )
                profile = src.profile.copy()
                output_names = [
                    f'prosail_{name}' for name in target_parameters
                ] + ['spectral_RMSE']
                profile.update(
                    driver='GTiff', count=len(output_names),
                    dtype='float32', nodata=np.nan, compress='deflate',
                    predictor=3, BIGTIFF='IF_SAFER',
                )
                profile.pop('photometric', None)
                profile.pop('interleave', None)
                handle = tempfile.NamedTemporaryFile(
                    suffix='.tif', dir=self.store.root, delete=False
                )
                temp_path = Path(handle.name)
                handle.close()
                statistics = {
                    name: {
                        'minimum': math.inf, 'maximum': -math.inf,
                        'sum': 0.0,
                    }
                    for name in output_names
                }
                valid_count = 0
                with rasterio.open(temp_path, 'w', **profile) as dst:
                    for band_index, name in enumerate(output_names, 1):
                        dst.set_band_description(band_index, name)
                        dst.set_band_unit(
                            band_index,
                            'reflectance_fraction'
                            if name == 'spectral_RMSE' else 'model_parameter',
                        )
                    dst.update_tags(
                        algorithm='PROSAIL_LUT_weighted_knn',
                        source_asset_id=asset_id,
                        lut_asset_id=lut_asset_id,
                        neighbors=str(neighbors),
                        band_mapping=json.dumps(
                            band_mapping, ensure_ascii=False,
                            separators=(',', ':'),
                        ),
                        target_parameters=','.join(target_parameters),
                        band_mapping_source=band_mapping_source,
                    )
                    for _, window in src.block_windows(
                        requested_raster_bands[0]
                    ):
                        observed = np.stack([
                            src.read(index, window=window).astype('float32')
                            * float(src.scales[index - 1])
                            + float(src.offsets[index - 1])
                            for index in requested_raster_bands
                        ], axis=-1)
                        valid = np.logical_and.reduce([
                            src.read_masks(index, window=window) != 0
                            for index in requested_raster_bands
                        ])
                        valid &= np.all(np.isfinite(observed), axis=-1)
                        valid &= np.all(
                            (observed >= 0) & (observed <= 1), axis=-1
                        )
                        if alpha_band is not None:
                            valid &= src.read(alpha_band, window=window) > 0

                        output = np.full(
                            (len(output_names), window.height, window.width),
                            np.nan, dtype='float32',
                        )
                        observations = observed[valid]
                        if observations.size:
                            distances, matches = tree.query(
                                observations / scale, k=neighbors,
                                workers=-1,
                            )
                            if neighbors == 1:
                                distances = distances[:, None]
                                matches = matches[:, None]
                            weights = 1.0 / np.maximum(
                                distances, 1e-6
                            ) ** 2
                            weights /= np.sum(weights, axis=1, keepdims=True)
                            modelled = np.sum(
                                selected_reflectance[matches]
                                * weights[:, :, None], axis=1,
                            )
                            estimated = [
                                np.sum(
                                    parameter_values[name][matches] * weights,
                                    axis=1,
                                )
                                for name in target_parameters
                            ]
                            rmse = np.sqrt(np.mean(
                                (observations - modelled) ** 2, axis=1
                            ))
                            values = estimated + [rmse]
                            for output_index, value in enumerate(values):
                                value = np.asarray(value, dtype='float32')
                                output[output_index][valid] = value
                                name = output_names[output_index]
                                statistics[name]['minimum'] = min(
                                    statistics[name]['minimum'],
                                    float(np.min(value)),
                                )
                                statistics[name]['maximum'] = max(
                                    statistics[name]['maximum'],
                                    float(np.max(value)),
                                )
                                statistics[name]['sum'] += float(
                                    np.sum(value, dtype='float64')
                                )
                            valid_count += observations.shape[0]
                        dst.write(output, window=window)
                if not valid_count:
                    raise AssetError('反射率影像中没有可用于PROSAIL反演的有效像元')
                pixel_count = src.width * src.height

            output_name = f'{Path(asset["name"]).stem}_prosail_inversion.tif'
            with temp_path.open('rb') as stream:
                result_asset = self.register(
                    stream, output_name, 'image/tiff', asset_id
                )
            return {
                'inversion': result_asset,
                'lut_asset_id': lut_asset_id,
                'output_bands': output_names,
                'output_units': {
                    **{name: 'model_parameter' for name in output_names[:-1]},
                    'spectral_RMSE': 'reflectance_fraction',
                },
                'valid_pixel_count': valid_count,
                'invalid_pixel_count': pixel_count - valid_count,
                'statistics': {
                    name: {
                        'minimum': values['minimum'],
                        'maximum': values['maximum'],
                        'mean': values['sum'] / valid_count,
                    }
                    for name, values in statistics.items()
                },
                'band_mapping': band_mapping,
                'band_mapping_source': band_mapping_source,
            }
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
