"""Exercise attachment and inline-image paths with a real >30 MiB TIFF."""
import base64
import hashlib
from pathlib import Path
import sqlite3
import httpx
from install_forestry_runtime import admin_client

path = Path('/tmp/forestry_large.tif')
binary = path.read_bytes()
assert len(binary) > 30 * 1048576
checksum = hashlib.sha256(binary).hexdigest()
with admin_client() as client:
    client.timeout = 240
    with path.open('rb') as stream:
        response = client.post('/api/v1/files/', params={'process':'false'}, files={'file':('forestry_large.tif',stream,'image/tiff')})
    response.raise_for_status()
    file = response.json()
    payload = {'model':'forestry_runtime','stream':False,
               'files':[{'type':'file','id':file['id'],'name':file['filename'],'file':file,'url':file['id']}],
               'messages':[{'role':'user','content':[
                   {'type':'text','text':'请调用inspect_file检查上传的forestry_large.tif，报告宽高、波段数量和坐标系。'},
                   {'type':'image_url','image_url':{'url':'data:image/tiff;base64,'+base64.b64encode(binary).decode()}}]}]}
    response = client.post('/api/chat/completions', json=payload)
    response.raise_for_status()
    answer = response.json()['choices'][0]['message']['content']
    print(answer)
    assert '4096' in answer and ('32650' in answer or '50N' in answer), 'Large image did not reach the inspection tool'
    valves = client.get('/api/v1/functions/id/forestry_runtime/valves')
    valves.raise_for_status()
    valves = valves.json()
    with sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True) as db:
        owner = db.execute("SELECT id FROM user WHERE role='admin' ORDER BY created_at LIMIT 1").fetchone()[0]
    listing = httpx.get(valves['RUNTIME_URL'] + '/assets', headers={
        'Authorization':'Bearer ' + valves['API_KEY'], 'X-User-ID':owner})
    listing.raise_for_status()
    matching = [a for a in listing.json()['assets'] if a['sha256']==checksum]
    assert len(matching)==1, 'Duplicate inline-image asset was registered'
    assert matching[0]['name']=='forestry_large.tif'
    print(f'PASS: {len(binary)} byte TIFF via Open WebUI HTTP; attachment + base64 deduplicated into one asset.')
