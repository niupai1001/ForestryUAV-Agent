"""End-to-end smoke test: local Qwen selects and runs inspect -> NDVI."""
import asyncio
import io
from pathlib import Path
import tempfile

import numpy as np
import rasterio
from rasterio.transform import from_origin

from runtime.agent import run_agent
from runtime.storage import Store


async def main():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'source.tif'
        red = np.full((16, 16), 20, dtype='uint16')
        nir = np.full((16, 16), 60, dtype='uint16')
        with rasterio.open(
            source, 'w', driver='GTiff', width=16, height=16, count=4,
            dtype='uint16', crs='EPSG:32650',
            transform=from_origin(500000, 3100000, .03, .03),
        ) as dataset:
            for index, values in enumerate(
                (red, np.full_like(red, 30), nir, np.full_like(red, 45)), 1
            ):
                dataset.write(values, index)
            for index, name in enumerate(('Red', 'Green', 'NIR', 'RedEdge'), 1):
                dataset.set_band_description(index, name)

        store = Store(Path(directory) / 'assets')
        with source.open('rb') as stream:
            asset = store.put(stream, 'known_multispectral.tif', 'acceptance')
        events = [event async for event in run_agent(
            store,
            'acceptance',
            [asset['id']],
            [{'role': 'user', 'content': (
                '请检查附件的波段元数据，然后计算NDVI并生成结果文件。'
                '请实际调用工具完成，不要只给公式。'
            )}],
            max_rounds=6,
        )]

        tool_names = [
            event['name'] for event in events if event['type'] == 'tool_start'
        ]
        if tool_names != ['inspect_file', 'calculate_ndvi']:
            raise AssertionError(
                f'Expected only inspect_file -> calculate_ndvi, got {tool_names}'
            )
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
        if not np.isclose(mean, .5):
            raise AssertionError(f'Expected NDVI 0.5, got {mean}')

        print('PASS: Qwen called', ' -> '.join(tool_names))
        print('PASS: native thinking events are present')
        print('PASS: generated NDVI GeoTIFF mean =', mean)


if __name__ == '__main__':
    asyncio.run(main())
