"""End-to-end smoke test: Qwen inspects, calculates NDVI and segments."""
import asyncio
import io
from pathlib import Path
import tempfile

import numpy as np
import rasterio
from rasterio.transform import from_origin

from runtime.agent import stream_agent
from runtime.lifecycle import Sessions
from runtime.workspace import WorkspaceRegistry


async def main():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'source.tif'
        red = np.full((16, 16), 50, dtype='uint16')
        nir = np.full((16, 16), 50, dtype='uint16')
        red[:, 8:] = 10
        nir[:, 8:] = 90
        with rasterio.open(
            source, 'w', driver='GTiff', width=16, height=16, count=5,
            dtype='uint16', crs='EPSG:32650',
            transform=from_origin(500000, 3100000, .03, .03),
        ) as dataset:
            for index, values in enumerate(
                (
                    red, np.full_like(red, 30), nir,
                    np.full_like(red, 45), np.full_like(red, 255),
                ), 1
            ):
                dataset.write(values, index)
            for index, name in enumerate(
                ('Red', 'Green', 'NIR', 'RedEdge', 'Alpha'), 1
            ):
                dataset.set_band_description(index, name)

        sessions = Sessions(Path(directory) / 'data')
        chat_id = '00000000-0000-0000-0000-000000000001'
        sessions.create('acceptance', chat_id)
        store = sessions.acquire('acceptance', chat_id)
        registry = WorkspaceRegistry(Path(directory) / 'data')
        with source.open('rb') as stream:
            asset = store.put(stream, 'known_multispectral.tif', 'acceptance')
        events = [event async for event in stream_agent(
            store,
            'acceptance',
            [asset['id']],
            [{'role': 'user', 'content': (
                '请检查附件的真实像元数据条件，计算NDVI，然后执行初步林冠'
                '候选分割并生成Mask。请实际调用工具完成，不要只给公式。'
            )}],
            max_requests=6,
            workspace_registry=registry,
        )]

        tool_names = [
            event['name'] for event in events if event['type'] == 'tool_start'
        ]
        required = ['inspect_raster', 'calculate_ndvi', 'segment_canopy']
        positions = [
            tool_names.index(name) if name in tool_names else -1
            for name in required
        ]
        if -1 in positions or positions != sorted(positions):
            raise AssertionError(
                f'Expected inspect_raster -> calculate_ndvi -> segment_canopy, '
                f'got {tool_names}'
            )
        if 'fs_write' in tool_names:
            raise AssertionError(f'Unexpected fs_write call: {tool_names}')
        if not any(event['type'] == 'thinking' for event in events):
            raise AssertionError('Ollama did not return a thinking field')
        if any(event['type'] == 'error' for event in events):
            raise AssertionError([e for e in events if e['type'] == 'error'])
        outputs = [
            event['result']['data']['ndvi']
            for event in events
            if event['type'] == 'tool_end'
            and event['name'] == 'calculate_ndvi'
            and event['ok']
        ]
        if len(outputs) != 1:
            raise AssertionError(f'Expected one NDVI output, got {len(outputs)}')
        with rasterio.open(store.path(outputs[0]['id'], 'acceptance')) as dataset:
            mean = float(np.nanmean(dataset.read(1)))
        if not np.isclose(mean, .4):
            raise AssertionError(f'Expected NDVI 0.4, got {mean}')

        masks = [
            event['result']['data']['mask']
            for event in events
            if event['type'] == 'tool_end'
            and event['name'] == 'segment_canopy'
            and event['ok']
        ]
        if len(masks) != 1:
            raise AssertionError(f'Expected one candidate mask, got {len(masks)}')
        with rasterio.open(store.path(masks[0]['id'], 'acceptance')) as dataset:
            mask = dataset.read(1)
        if not np.isclose(np.count_nonzero(mask == 1) / mask.size, .5):
            raise AssertionError('Expected half of the fixture as candidates')

        print('PASS: Qwen called', ' -> '.join(tool_names))
        print('PASS: native thinking events are present')
        print('PASS: generated NDVI GeoTIFF mean =', mean)
        print('PASS: generated candidate canopy Mask fraction = 0.5')


if __name__ == '__main__':
    asyncio.run(main())
