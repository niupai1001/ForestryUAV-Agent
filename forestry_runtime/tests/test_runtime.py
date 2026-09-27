import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import uuid

import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import from_origin

from runtime.storage import Store, AssetError
from runtime.capabilities.domain_runtime import RemoteSensingTools
from runtime.capabilities.uav_audit.audit import InputPathMapper, UavInspectionService


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_storage_scope_and_size(self):
        a = self.store.put(io.BytesIO(b'hello'), '../../hello.txt', 'alice')
        self.assertEqual(a['name'], 'hello.txt')
        self.assertEqual(a['id'], self.store.put(io.BytesIO(b'hello'), 'hello.txt', 'alice')['id'])
        with self.assertRaises(AssetError):
            self.store.get(a['id'], 'bob')
        self.assertFalse(RemoteSensingTools(self.store, 'alice', []).execute('inspect_file', {'asset_id': a['id']})['ok'])
        with self.assertRaises(AssetError):
            self.store.put(io.BytesIO(b'12345'), 'big.txt', 'alice', limit=4)
        self.assertEqual(len(self.store.list('alice')), 1)

    def test_raster_and_preview(self):
        path = Path(self.temp.name) / 'input.tif'
        with rasterio.open(path, 'w', driver='GTiff', width=48, height=32, count=3, dtype='uint16',
                           crs='EPSG:32650', transform=from_origin(500000, 3100000, .03, .03), nodata=0) as ds:
            ds.write(np.arange(3*32*48, dtype='uint16').reshape(3, 32, 48))
        with path.open('rb') as stream:
            a = self.store.put(stream, 'forest.tif', 'alice')
        box = RemoteSensingTools(self.store, 'alice', [a['id']])
        result = box.execute('inspect_file', {'asset_id': a['id']})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['crs'], 'EPSG:32650')
        self.assertAlmostEqual(result['data']['pixel_size'][0], .03)
        self.assertEqual(result['data']['grid_footprint_area'], 1.3824)
        self.assertIn('50N', result['data']['crs_name'])
        self.assertEqual(result['data']['band_descriptions'], [None, None, None])
        preview = box.execute('preview_image', {'asset_id': a['id']})
        self.assertTrue(preview['ok'], preview)
        with Image.open(self.store.path(preview['data']['preview']['id'], 'alice')) as image:
            self.assertEqual(image.size, (48, 32))
        bad = self.store.put(io.BytesIO(b'<VRTDataset/>'), 'fake.tif', 'alice')
        self.assertFalse(RemoteSensingTools(self.store, 'alice', [bad['id']]).execute('inspect_file', {'asset_id': bad['id']})['ok'])

    def test_calculate_ndvi_writes_georeferenced_float_raster(self):
        path = Path(self.temp.name) / 'multispectral.tif'
        red = np.array([[1, 2], [0, 0]], dtype='uint16')
        nir = np.array([[3, 2], [0, 1]], dtype='uint16')
        with rasterio.open(
            path, 'w', driver='GTiff', width=2, height=2, count=3,
            dtype='uint16', crs='EPSG:32650',
            transform=from_origin(500000, 3100000, .03, .03),
        ) as ds:
            ds.write(red, 1)
            ds.write(np.ones_like(red), 2)
            ds.write(nir, 3)
            ds.set_band_description(1, 'Red')
            ds.set_band_description(2, 'Green')
            ds.set_band_description(3, 'NIR')
        with path.open('rb') as stream:
            asset = self.store.put(stream, 'forest.tif', 'alice')
        box = RemoteSensingTools(self.store, 'alice', [asset['id']])
        result = box.execute('calculate_ndvi', {
            'asset_id': asset['id'], 'bands': {'red': 1, 'nir': 3},
        })
        self.assertTrue(result['ok'], result)
        stats = result['data']['statistics']
        self.assertEqual(stats['valid_pixel_count'], 3)
        self.assertEqual(stats['zero_denominator_pixel_count'], 1)
        self.assertAlmostEqual(stats['mean'], .5)
        output = result['data']['ndvi']
        self.assertEqual(output['parent_id'], asset['id'])
        with rasterio.open(self.store.path(output['id'], 'alice')) as ds:
            self.assertEqual(ds.count, 1)
            self.assertEqual(ds.dtypes[0], 'float32')
            self.assertEqual(str(ds.crs), 'EPSG:32650')
            values = ds.read(1)
            self.assertAlmostEqual(float(values[0, 0]), .5)
            self.assertTrue(np.isnan(values[1, 0]))
        swapped = box.execute('calculate_ndvi', {
            'asset_id': asset['id'], 'bands': {'red': 3, 'nir': 1},
        })
        self.assertFalse(swapped['ok'])

        unlabeled_path = Path(self.temp.name) / 'unlabeled.tif'
        with rasterio.open(
            unlabeled_path, 'w', driver='GTiff', width=2, height=2, count=2,
            dtype='uint16', crs='EPSG:32650',
            transform=from_origin(500000, 3100000, .03, .03),
        ) as ds:
            ds.write(red, 1)
            ds.write(nir, 2)
        with unlabeled_path.open('rb') as stream:
            unlabeled = self.store.put(stream, 'unlabeled.tif', 'alice')
        unlabeled_result = RemoteSensingTools(
            self.store, 'alice', [unlabeled['id']]
        ).execute('calculate_ndvi', {
            'asset_id': unlabeled['id'], 'bands': {'red': 1, 'nir': 2},
        })
        self.assertFalse(unlabeled_result['ok'])
        self.assertIn('metadata evidence', unlabeled_result['error'])

        segmented = box.execute('segment_canopy', {
            'asset_id': output['id'], 'threshold': 0.4,
        })
        self.assertTrue(segmented['ok'], segmented)
        self.assertEqual(
            segmented['data']['statistics']['candidate_pixel_count'], 2
        )
        mask_asset = segmented['data']['mask']
        self.assertEqual(mask_asset['parent_id'], output['id'])
        with rasterio.open(self.store.path(mask_asset['id'], 'alice')) as ds:
            self.assertEqual(ds.dtypes[0], 'uint8')
            self.assertEqual(ds.nodata, 255)
            self.assertEqual(set(np.unique(ds.read(1))), {0, 1, 255})
        automatic = box.execute('segment_canopy', {
            'asset_id': output['id'],
        })
        self.assertTrue(automatic['ok'], automatic)
        self.assertTrue(
            automatic['data']['threshold_source'].startswith('otsu_')
        )
        self.assertEqual(
            automatic['data']['statistics']['candidate_pixel_count'], 2
        )

    def test_prosail_forward_lut_and_inversion_are_separate_tools(self):
        path = Path(self.temp.name) / 'mavic3m.tif'
        shape = (8, 10)
        bands = [
            np.full(shape, .04, dtype='float32'),
            np.full(shape, .07, dtype='float32'),
            np.full(shape, .38, dtype='float32'),
            np.full(shape, .20, dtype='float32'),
            np.full(shape, 255, dtype='float32'),
        ]
        bands[-1][0, 0] = 0
        with rasterio.open(
            path, 'w', driver='GTiff', width=10, height=8, count=5,
            dtype='float32', crs='EPSG:32650',
            transform=from_origin(500000, 3100000, .03, .03),
        ) as ds:
            for index, values in enumerate(bands, 1):
                ds.write(values, index)
            for index, description in enumerate(
                ('Red', 'Green', 'NIR', 'RedEdge'), 1
            ):
                ds.set_band_description(index, description)
        with path.open('rb') as stream:
            asset = self.store.put(stream, 'mavic3m.tif', 'alice')
        box = RemoteSensingTools(self.store, 'alice', [asset['id']])
        geometry = {
            'solar_zenith': 30.0,
            'view_zenith': 0.0,
            'relative_azimuth': 0.0,
        }
        model = {
            'prospect_version': 'D', 'typelidf': 2, 'factor': 'SDR',
        }
        sensor_bands = [
            {'name': 'Red', 'lower_nm': 634, 'upper_nm': 666},
            {'name': 'Green', 'lower_nm': 544, 'upper_nm': 576},
            {'name': 'NIR', 'lower_nm': 834, 'upper_nm': 886},
            {'name': 'RedEdge', 'lower_nm': 714, 'upper_nm': 746},
        ]
        parameters = {
            'n': 1.5, 'cab': 40.0, 'car': 10.0, 'cbrown': 0.05,
            'cw': 0.015, 'cm': 0.008, 'lai': 3.0, 'lidfa': 50.0,
            'hspot': 0.1, 'ant': 0.05, 'alpha': 40.0,
            'rsoil': 1.0, 'psoil': 0.5,
        }
        sources = {
            'parameter_source': '测试显式参数',
            'geometry_source': '测试显式观测几何',
            'sensor_response_source': '测试显式矩形波段范围',
        }
        simulated = box.execute('simulate_prosail', {
            'parameters': parameters,
            'geometry': geometry,
            'model': model,
            'sensor_bands': sensor_bands,
            **sources,
        })
        self.assertTrue(simulated['ok'], simulated)
        self.assertEqual(
            set(simulated['data']['band_reflectance']),
            {'Red', 'Green', 'NIR', 'RedEdge'},
        )

        ranges = {
            name: {'minimum': low, 'maximum': high}
            for name, (low, high) in {
                'n': (1.4, 1.6), 'cab': (35.0, 45.0),
                'car': (8.0, 12.0), 'cbrown': (0.0, 0.1),
                'cw': (0.01, 0.02), 'cm': (0.005, 0.01),
                'lai': (2.0, 4.0), 'lidfa': (40.0, 60.0),
                'hspot': (0.05, 0.15), 'ant': (0.0, 0.0),
                'alpha': (40.0, 40.0), 'rsoil': (0.8, 1.2),
                'psoil': (0.3, 0.7),
            }.items()
        }
        built = box.execute('build_prosail_lut', {
            'parameter_ranges': ranges,
            'geometry': geometry,
            'model': model,
            'sensor_bands': sensor_bands,
            'lut_size': 24,
            'seed': 7,
            'sampling': 'latin_hypercube',
            **sources,
        })
        self.assertTrue(built['ok'], built)
        self.assertEqual(built['data']['row_count'], 24)

        invalid_lut_bytes = io.BytesIO()
        np.savez_compressed(
            invalid_lut_bytes,
            reflectance=np.full((2, 4), 1.2, dtype='float32'),
            band_names=np.asarray(['Red', 'Green', 'NIR', 'RedEdge']),
            parameter_names=np.asarray(['lai', 'cab']),
            parameter_lai=np.asarray([2.0, 3.0], dtype='float32'),
            parameter_cab=np.asarray([35.0, 45.0], dtype='float32'),
        )
        invalid_lut_bytes.seek(0)
        invalid_lut = box.register(
            invalid_lut_bytes, 'invalid_lut.npz', 'application/octet-stream'
        )
        rejected_lut = box.execute('invert_prosail', {
            'asset_id': asset['id'],
            'lut_asset_id': invalid_lut['id'],
            'band_mapping': [
                {'lut_band': 'Red', 'raster_band': 1},
                {'lut_band': 'Green', 'raster_band': 2},
                {'lut_band': 'NIR', 'raster_band': 3},
                {'lut_band': 'RedEdge', 'raster_band': 4},
            ],
            'target_parameters': ['lai', 'cab'],
            'alpha_band': 5,
            'neighbors': 1,
            'band_mapping_source': '测试影像波段描述',
        })
        self.assertFalse(rejected_lut['ok'])
        self.assertIn('0到1', rejected_lut['error'])

        result = box.execute('invert_prosail', {
            'asset_id': asset['id'],
            'lut_asset_id': built['data']['lut']['id'],
            'band_mapping': [
                {'lut_band': 'Red', 'raster_band': 1},
                {'lut_band': 'Green', 'raster_band': 2},
                {'lut_band': 'NIR', 'raster_band': 3},
                {'lut_band': 'RedEdge', 'raster_band': 4},
            ],
            'target_parameters': ['lai', 'cab'],
            'alpha_band': 5,
            'neighbors': 3,
            'band_mapping_source': '测试影像波段描述',
        })
        self.assertTrue(result['ok'], result)
        data = result['data']
        self.assertEqual(data['valid_pixel_count'], 79)
        self.assertEqual(
            data['output_units']['spectral_RMSE'], 'reflectance_fraction'
        )
        self.assertNotIn('preview', data)
        with rasterio.open(
            self.store.path(data['inversion']['id'], 'alice')
        ) as ds:
            self.assertEqual(ds.count, 3)
            self.assertEqual(ds.descriptions[0], 'prosail_lai')
            self.assertEqual(ds.units[-1], 'reflectance_fraction')
            self.assertTrue(np.isnan(ds.read(1)[0, 0]))
            self.assertEqual(str(ds.crs), 'EPSG:32650')

        # What the producer claims about what it wrote, per band: a verifier has
        # nothing to check this product against otherwise, and one global range
        # would be a false claim over three different quantities.
        semantics = (data['inversion'].get('metadata') or {}).get('semantics') or {}
        self.assertEqual('prosail_inversion', semantics.get('quantity'))
        self.assertEqual('EPSG:32650', (semantics.get('grid') or {}).get('crs'))
        bands = semantics.get('bands') or {}
        self.assertEqual({'prosail_lai', 'prosail_cab', 'spectral_RMSE'}, set(bands))
        # The estimate is a weighted mean of LUT rows, so it cannot leave the range
        # the LUT sampled. That is the sampled hull, not the configured [2, 4] --
        # a Latin hypercube need not land on its own endpoints, and claiming a bound
        # no row reached would be a claim the product cannot be held to.
        for name, (low, high) in (('prosail_lai', (2.0, 4.0)),
                                  ('prosail_cab', (35.0, 45.0))):
            declared_low, declared_high = bands[name]['valid_range']
            self.assertGreaterEqual(declared_low, low)
            self.assertLessEqual(declared_high, high)
            self.assertLess(declared_low, declared_high)
        # A root-mean-square error is bounded below and not above.
        self.assertEqual([0.0, None], bands['spectral_RMSE']['valid_range'])

    def test_inspect_raster_checks_alpha_and_red_nir_overlap(self):
        path = Path(self.temp.name) / 'conditions.tif'
        red = np.array(
            [[0, 1, 1, 1], [0, -1, 1, np.nan]], dtype='float32'
        )
        nir = np.array(
            [[0, 3, 1, -1], [0, 1, 9, 1]], dtype='float32'
        )
        alpha = np.array(
            [[0, 1, 1, 1], [0, 1, 1, 0]], dtype='float32'
        )
        with rasterio.open(
            path, 'w', driver='GTiff', width=4, height=2, count=3,
            dtype='float32', crs='EPSG:32650',
            transform=from_origin(500000, 3100000, .03, .03),
        ) as ds:
            ds.write(red, 1)
            ds.write(nir, 2)
            ds.write(alpha, 3)
            ds.set_band_description(1, 'Red')
            ds.set_band_description(2, 'NIR')
            ds.set_band_description(3, 'Alpha')
        with path.open('rb') as stream:
            asset = self.store.put(stream, 'conditions.tif', 'alice')
        result = RemoteSensingTools(
            self.store, 'alice', [asset['id']]
        ).execute('inspect_raster', {'asset_id': asset['id']})
        self.assertTrue(result['ok'], result)
        data = result['data']
        self.assertEqual(
            data['roles_from_metadata'], {'red': 1, 'nir': 2, 'alpha': 3}
        )
        red_stats = data['band_statistics'][0]
        self.assertEqual(red_stats['nonfinite_pixel_count'], 1)
        self.assertEqual(red_stats['negative_pixel_count'], 1)
        self.assertEqual(red_stats['zero_pixel_count'], 2)
        pair = data['red_nir_pair']
        self.assertEqual(pair['common_valid_pixel_count'], 7)
        self.assertEqual(pair['zero_denominator_pixel_count'], 4)
        self.assertEqual(
            pair['zero_denominator_and_alpha_nonpositive_pixel_count'], 2
        )
        self.assertEqual(
            pair['zero_denominator_and_alpha_positive_pixel_count'], 2
        )
        self.assertEqual(data['alpha_analysis']['positive_pixel_count'], 5)
        invalid_input = RemoteSensingTools(
            self.store, 'alice', [asset['id']]
        ).execute('segment_canopy', {'asset_id': asset['id']})
        self.assertFalse(invalid_input['ok'])

    def test_existing_uav_products_report_metadata_qa_and_chm_inputs(self):
        root = Path(self.temp.name) / 'products'
        for folder in ('3_正射影像', 'dsm', 'dtm'):
            (root / folder).mkdir(parents=True, exist_ok=True)
        transform = from_origin(500000, 3100000, 1, 1)
        products = (
            ('3_正射影像/1605-白桦.tif', 4),
            ('dsm/1605_dsm.tif', 1),
            ('dtm/1605_dtm.tif', 1),
        )
        for relative, count in products:
            with rasterio.open(
                root / relative, 'w', driver='GTiff', width=8, height=6,
                count=count, dtype='float32', crs='EPSG:32652',
                transform=transform, nodata=-9999,
            ) as dataset:
                dataset.write(np.ones((count, 6, 8), dtype='float32'))

        box = RemoteSensingTools(self.store, 'alice', [])
        box._uav_audit = UavInspectionService(
            mapper=InputPathMapper(root, str(root)), temp_root=root
        )
        result = box.execute('inspect_uav_products', {
            'folder_path': str(root), 'max_products': 10,
        })

        self.assertTrue(result['ok'], result)
        data = result['data']
        self.assertEqual(
            data['role_counts'], {'dsm': 1, 'dtm': 1, 'orthomosaic': 1}
        )
        self.assertTrue(
            data['chm_input_suitability']['suitable_from_metadata']
        )
        self.assertEqual(
            data['chm_input_suitability']['required_unverified_evidence'],
            ['shared_vertical_reference_and_height_units'],
        )
        self.assertTrue(all(item['projected_crs'] for item in data['products']))
        self.assertIn('seamlines', data['visual_review_required'])

    def test_zip_text_and_validation(self):
        for name, valid in [('folder/readme.txt', True), ('../escape.txt', False), ('C:\\escape.txt', False)]:
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as z:
                z.writestr(name, '森林样地数据')
            data.seek(0)
            a = self.store.put(data, 'inputs.zip', 'alice')
            box = RemoteSensingTools(self.store, 'alice', [a['id']])
            result = box.execute('extract_zip', {'asset_id': a['id']})
            self.assertEqual(result['ok'], valid, result)
            if valid:
                child = result['data']['files'][0]
                self.assertEqual(child['parent_id'], a['id'])
                self.assertEqual(
                    self.store.path(child['id'], 'alice').read_text(encoding='utf-8'),
                    '森林样地数据',
                )
        self.assertFalse(box.execute('run_shell', {'command': 'echo x'})['ok'])

    def test_api_auth_upload_and_download(self):
        from fastapi.testclient import TestClient
        # Do not initialize an additional persistent database during the test.
        with patch.dict(os.environ, {'DATA_ROOT': self.temp.name, 'RUNTIME_API_KEY': 'test-key-with-more-than-24-characters'}):
            from runtime.api import app as api
            from runtime.lifecycle import Sessions
            from runtime.workspace import WorkspaceRegistry
            from runtime.session.coordinator import RunCoordinator
            manager = Sessions(Path(self.temp.name) / 'sessions-api')
            registry = WorkspaceRegistry(Path(self.temp.name) / 'sessions-api')
            coordinator = RunCoordinator(manager, registry)
            manager.cleanup = registry.cleanup_session
            with patch.object(api, 'sessions', manager), patch.object(api, 'workspaces', registry), patch.object(api, 'runs', coordinator), TestClient(api.app) as client:
                self.assertEqual(client.get('/assets').status_code, 401)
                chat_id = str(uuid.uuid4())
                headers = {'Authorization': 'Bearer test-key-with-more-than-24-characters', 'X-User-ID': 'alice', 'X-Chat-ID': chat_id}
                self.assertEqual(client.post('/sessions', headers=headers, json={'chat_id': chat_id}).status_code, 200)
                response = client.post('/assets', headers=headers, files={'file':('data.csv', b'tree,height\n1,5')})
                self.assertEqual(response.status_code, 200)
                public_asset = response.json()
                asset_id = public_asset['id']
                self.assertNotIn('sha256', public_asset)
                self.assertNotIn('managed_path', public_asset)
                self.assertNotIn('owner', public_asset)
                self.assertEqual(client.get(f'/assets/{asset_id}/content', headers=headers).content, b'tree,height\n1,5')
                other = {**headers, 'X-User-ID':'bob'}
                self.assertEqual(client.get(f'/assets/{asset_id}', headers=other).status_code, 400)
                self.assertEqual(client.get(f'/assets/{asset_id}', headers=headers).json()['name'], 'data.csv')
                # Persistence is limited to assets and survives recreating the Store.
                self.assertEqual(Store(manager.directories / chat_id).get(asset_id, 'alice')['name'], 'data.csv')

if __name__ == '__main__':
    unittest.main()
