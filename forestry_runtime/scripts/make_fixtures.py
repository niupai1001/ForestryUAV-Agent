from pathlib import Path
import zipfile
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import from_origin

root = Path('/tmp/forestry_fixtures')
root.mkdir(exist_ok=True)
with rasterio.open(root / 'demo_forest.tif', 'w', driver='GTiff', width=64, height=48, count=3,
                   dtype='uint16', crs='EPSG:32650', transform=from_origin(500000, 3100000, .05, .05), nodata=0) as ds:
    ds.write(np.arange(3*48*64, dtype='uint16').reshape(3,48,64))
Image.new('RGB', (80, 60), (20, 130, 50)).save(root / 'demo_image.png')
with zipfile.ZipFile(root / 'demo_bundle.zip', 'w') as z:
    z.writestr('plot/notes.txt', '测试样地编号：FOREST-042\n样地面积：400平方米\n此为合成验收数据，不代表真实调查。')
print('Generated synthetic TIFF, PNG and ZIP fixtures.')
