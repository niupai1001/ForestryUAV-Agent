import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import uuid

import httpx
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import from_origin

from runtime.storage import Store, AssetError
from runtime.tools import Toolbox
from runtime.agent import run_agent


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
        self.assertFalse(Toolbox(self.store, 'alice', []).execute('read_text', {'asset_id': a['id']})['ok'])
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
        box = Toolbox(self.store, 'alice', [a['id']])
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
        self.assertFalse(Toolbox(self.store, 'alice', [bad['id']]).execute('inspect_file', {'asset_id': bad['id']})['ok'])

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
        box = Toolbox(self.store, 'alice', [asset['id']])
        result = box.execute('calculate_ndvi', {
            'asset_id': asset['id'], 'red_band': 1, 'nir_band': 3,
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
            'asset_id': asset['id'], 'red_band': 3, 'nir_band': 1,
        })
        self.assertFalse(swapped['ok'])

    def test_zip_text_and_validation(self):
        for name, valid in [('folder/readme.txt', True), ('../escape.txt', False), ('C:\\escape.txt', False)]:
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as z:
                z.writestr(name, '森林样地数据')
            data.seek(0)
            a = self.store.put(data, 'inputs.zip', 'alice')
            box = Toolbox(self.store, 'alice', [a['id']])
            result = box.execute('extract_zip', {'asset_id': a['id']})
            self.assertEqual(result['ok'], valid, result)
            if valid:
                child = result['data']['files'][0]
                self.assertEqual(child['parent_id'], a['id'])
                self.assertEqual(box.execute('read_text', {'asset_id': child['id']})['data']['content'], '森林样地数据')
        self.assertFalse(box.execute('run_shell', {'command': 'echo x'})['ok'])
        self.assertFalse(box.execute('read_text', {'asset_id': a['id'], 'max_chars': -1})['ok'])
        self.assertFalse(box.execute('save_text', {'filename': 'x.py', 'content': 'x'})['ok'])

    def test_tool_loop_receives_real_result(self):
        a = self.store.put(io.BytesIO(b'field=oak'), 'plot.txt', 'alice')
        seen = []
        async def handler(request):
            data = json.loads(request.content)
            seen.append(data)
            if len(seen) == 1:
                self.assertTrue(data['think'])
                return httpx.Response(200, json={'message': {'role': 'assistant', 'content': '', 'thinking': '需要读取文件', 'tool_calls': [
                    {'function': {'name': 'read_text', 'arguments': {'asset_id': a['id']}}}]}})
            self.assertEqual(data['messages'][-1]['role'], 'tool')
            self.assertIn('field=oak', data['messages'][-1]['content'])
            return httpx.Response(200, json={'message': {'role': 'assistant', 'content': 'oak'}})
        async def check():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return [e async for e in run_agent(self.store, 'alice', [a['id']], [{'role':'user','content':'read it'}], client=client)]
        events = asyncio.run(check())
        self.assertEqual([e['type'] for e in events], ['thinking', 'tool_start', 'tool_end', 'message', 'done'])
        self.assertEqual(events[0]['content'], '需要读取文件')
        self.assertEqual(events[1]['arguments']['asset_id'], a['id'])
        self.assertIn('field=oak', events[2]['result']['data']['content'])
        self.assertEqual(events[-2]['content'], 'oak')

    def test_api_auth_upload_and_download(self):
        from fastapi.testclient import TestClient
        # Do not initialize an additional persistent database during the test.
        with patch.dict(os.environ, {'DATA_ROOT': self.temp.name, 'RUNTIME_API_KEY': 'test-key-with-more-than-24-characters'}):
            from runtime import app as api
            from runtime.lifecycle import Sessions
            manager = Sessions(Path(self.temp.name) / 'sessions-api')
            with patch.object(api, 'sessions', manager), TestClient(api.app) as client:
                self.assertEqual(client.get('/assets').status_code, 401)
                chat_id = str(uuid.uuid4())
                headers = {'Authorization': 'Bearer test-key-with-more-than-24-characters', 'X-User-ID': 'alice', 'X-Chat-ID': chat_id}
                self.assertEqual(client.post('/sessions', headers=headers, json={'chat_id': chat_id}).status_code, 200)
                response = client.post('/assets', headers=headers, files={'file':('data.csv', b'tree,height\n1,5')})
                self.assertEqual(response.status_code, 200)
                asset_id = response.json()['id']
                self.assertEqual(client.get(f'/assets/{asset_id}/content', headers=headers).content, b'tree,height\n1,5')
                other = {**headers, 'X-User-ID':'bob'}
                self.assertEqual(client.get(f'/assets/{asset_id}', headers=other).status_code, 400)
                result = client.post('/tools/execute', headers=headers, json={'name':'read_text', 'asset_ids':[asset_id], 'arguments':{'asset_id':asset_id}})
                self.assertTrue(result.json()['ok'])
                # Persistence is limited to assets and survives recreating the Store.
                self.assertEqual(Store(manager.directories / chat_id).get(asset_id, 'alice')['name'], 'data.csv')

    def test_unknown_tool_recovers_and_round_limit_is_explicit(self):
        async def handler(request):
            return httpx.Response(200, json={'message':{'role':'assistant','content':'','tool_calls':[
                {'function':{'name':'missing_tool','arguments':{}}}]}})
        async def check():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return [e async for e in run_agent(self.store,'alice',[],[{'role':'user','content':'go'}],client=client,max_rounds=2)]
        events = asyncio.run(check())
        self.assertEqual(sum(e['type']=='tool_end' and not e['ok'] for e in events), 2)
        self.assertEqual(events[-2]['type'], 'error')


if __name__ == '__main__':
    unittest.main()
