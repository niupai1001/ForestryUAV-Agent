"""Update only this Pipe's source, retaining valves and model settings."""
import json
from pathlib import Path
import time
from install_forestry_runtime import admin_client

with admin_client() as client:
    route = '/api/v1/functions/id/forestry_runtime'
    response = client.get(route)
    response.raise_for_status()
    prior = response.json()
    backup = Path('/app/backend/data/forestry_runtime_backups')
    backup.mkdir(exist_ok=True)
    (backup / f'pipe_update_{int(time.time())}.json').write_text(json.dumps(prior, ensure_ascii=False))
    payload = {k: prior[k] for k in ('id', 'name', 'meta')}
    payload['content'] = Path('/tmp/forestry_runtime_pipe.py').read_text()
    response = client.post(route + '/update', json=payload)
    response.raise_for_status()
    print('Updated Forestry Runtime Pipe source; existing model settings and valves retained.')
