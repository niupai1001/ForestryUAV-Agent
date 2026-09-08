import io
import json
from pathlib import Path
import re
from PIL import Image
from install_forestry_runtime import admin_client

with admin_client() as client:
    client.timeout = 240
    with Path('/tmp/forestry_fixtures/demo_forest.tif').open('rb') as f:
        r = client.post('/api/v1/files/',params={'process':'false'},files={'file':('demo_forest.tif',f,'image/tiff')})
    r.raise_for_status()
    file = r.json()
    attachments = [{'type':'file','id':file['id'],'name':file['filename'],'file':file,'url':file['id']}]
    prompt = '调用preview_image为上传的TIFF生成预览。'
    response = client.post('/api/chat/completions',json={'model':'forestry_runtime','stream':False,'files':attachments,'messages':[{'role':'user','content':prompt}]})
    response.raise_for_status()
    answer=response.json()['choices'][0]['message']['content']
    print(answer)
    image_id = re.search(r'!\[预览\]\(/api/v1/files/([0-9a-f-]+)/content\)',answer).group(1)
    image=client.get(f'/api/v1/files/{image_id}/content')
    image.raise_for_status()
    with Image.open(io.BytesIO(image.content)) as preview:
        assert preview.size == (64,48)
    follow = client.post('/api/chat/completions',json={'model':'forestry_runtime','stream':False,'messages':[
        {'role':'user','content':prompt},{'role':'assistant','content':answer},
        {'role':'user','content':'检查上一轮生成的PNG预览文件尺寸。请调用工具确认。'}]})
    follow.raise_for_status()
    result=follow.json()['choices'][0]['message']['content']
    print(result)
    assert '64' in result and '48' in result
    print('PASS: preview download and generated-file access in the next turn.')
