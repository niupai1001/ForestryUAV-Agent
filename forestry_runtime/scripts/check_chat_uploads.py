"""Exercise the same MIME/process choices as Open WebUI's current chat uploader."""
from pathlib import Path
import json
from install_forestry_runtime import admin_client

with admin_client() as client:
    client.timeout = 90
    attachments = []
    for name, mime, process in [('demo_forest.tif','image/tiff','false'), ('demo_image.png','image/png','false'), ('demo_bundle.zip','application/zip','true')]:
        with (Path('/tmp/forestry_fixtures') / name).open('rb') as f:
            r = client.post('/api/v1/files/', params={'process':process}, files={'file':(name,f,mime)})
        r.raise_for_status()
        record = r.json()
        with client.stream('GET', f'/api/v1/files/{record["id"]}/process/status', params={'stream':'true'}) as status:
            status.raise_for_status()
            events = list(status.iter_lines())
        print(name, 'upload=ok', 'processing=', events)
        attachments.append({'type':'file','id':record['id'],'name':name,'file':record,'url':record['id']})
    r=client.post('/api/chat/completions', json={'model':'forestry_runtime','stream':False,'files':attachments,
                  'messages':[{'role':'user','content':'调用list_files列出当前附件文件名，不需要分析。'}]})
    r.raise_for_status()
    print(r.text[:3000])
