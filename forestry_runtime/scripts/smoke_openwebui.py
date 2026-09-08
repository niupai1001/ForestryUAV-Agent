"""End-to-end HTTP test using the existing local admin account; no tokens printed."""
import json
from pathlib import Path
import re
import sys

from install_forestry_runtime import admin_client


with admin_client() as client:
    client.timeout = 240
    attachments = []
    for name in ['demo_forest.tif', 'demo_image.png', 'demo_bundle.zip']:
        with (Path('/tmp/forestry_fixtures') / name).open('rb') as stream:
            response = client.post('/api/v1/files/', params={'process': 'false'}, files={'file': (name, stream)})
        response.raise_for_status()
        f = response.json()
        attachments.append({'type': 'file', 'id': f['id'], 'name': name, 'file': f, 'url': f['id']})
    prompt = ('请实际调用工具检查demo_forest.tif和demo_image.png的尺寸、TIFF坐标系与分辨率；'
              '解压demo_bundle.zip并读取其中notes.txt。最后将这些真实信息保存为runtime_acceptance.md，给出下载链接。')
    payload = {'model': 'forestry_runtime', 'stream': True,
               'messages': [{'role': 'user', 'content': prompt}], 'files': attachments}
    answer = ''
    with client.stream('POST', '/api/chat/completions', json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith('data: ') and line[6:] != '[DONE]':
                data = json.loads(line[6:])
                if 'error' in data:
                    raise RuntimeError(data['error'])
                answer += data.get('choices', [{}])[0].get('delta', {}).get('content', '') or ''
    print(answer)
    ids = re.findall(r'/api/v1/files/([0-9a-f-]+)/content', answer)
    assert ids, 'No downloadable artifact was returned'
    bodies = []
    for file_id in set(ids):
        downloaded = client.get(f'/api/v1/files/{file_id}/content')
        downloaded.raise_for_status()
        bodies.append(downloaded.text)
    combined = answer + '\n'.join(bodies)
    for expected in ['32650', '64', '48', '80', '60', 'FOREST-042']:
        assert expected in combined, f'Missing verified fixture fact: {expected}'
    print('PASS: Open WebUI HTTP → Pipe → runtime → Qwen → real tools → downloadable files.')
