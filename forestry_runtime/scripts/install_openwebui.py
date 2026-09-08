"""Run inside the existing Open WebUI container; use its authenticated admin API.

Only installs/updates our Pipe and matching model. It does not change other tools,
connections, chat history, upload rules or the default model.
"""
import datetime
import json
import os
from pathlib import Path
import sqlite3
import time

import httpx
import jwt


def admin_client():
    c = sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True)
    row = c.execute("SELECT id FROM user WHERE role='admin' ORDER BY created_at LIMIT 1").fetchone()
    c.close()
    if not row:
        raise RuntimeError('No existing administrator account')
    secret = os.getenv('WEBUI_SECRET_KEY') or Path('/app/backend/.webui_secret_key').read_text().strip()
    token = jwt.encode({'id': row[0], 'exp': int(time.time()) + 300, 'jti': __import__('uuid').uuid4().hex}, secret, algorithm='HS256')
    return httpx.Client(base_url='http://localhost:8080', headers={'Authorization': 'Bearer ' + token}, timeout=60)


def main():
    source = Path('/tmp/forestry_runtime_pipe.py').read_text()
    env_file = Path('/tmp/forestry_runtime.env')
    key = next(line.split('=', 1)[1].strip() for line in env_file.read_text(encoding='utf-8-sig').splitlines() if line.startswith('RUNTIME_API_KEY='))
    identifier = 'forestry_runtime'
    with admin_client() as client:
        existing = client.get(f'/api/v1/functions/id/{identifier}')
        backup_dir = Path('/app/backend/data/forestry_runtime_backups')
        backup_dir.mkdir(exist_ok=True)
        snapshot = {'function': existing.json() if existing.status_code == 200 else None}
        # Backups stay inside the protected Open WebUI data volume.
        (backup_dir / f'install_{int(time.time())}.json').write_text(json.dumps(snapshot, ensure_ascii=False))
        payload = {'id': identifier, 'name': 'Forestry Runtime', 'content': source,
                   'meta': {'description': '独立运行时：真实文件工具与Qwen3.5，支持TIFF、PNG、ZIP和文本。'}}
        route = f'/api/v1/functions/id/{identifier}/update' if existing.status_code == 200 else '/api/v1/functions/create'
        response = client.post(route, json=payload)
        response.raise_for_status()
        response = client.post(f'/api/v1/functions/id/{identifier}/valves/update', json={'RUNTIME_URL': 'http://host.docker.internal:8010', 'API_KEY': key})
        response.raise_for_status()
        details = client.get(f'/api/v1/functions/id/{identifier}').json()
        if not details.get('is_active'):
            response = client.post(f'/api/v1/functions/id/{identifier}/toggle')
            response.raise_for_status()
        # Capabilities stop Open WebUI from running a competing tool loop or RAG.
        model = {'id': identifier, 'base_model_id': None, 'name': 'Forestry Runtime', 'params': {},
                 'meta': {'description': '文件检查、ZIP解压、预览和文本读写；使用本机Qwen3.5:4b。',
                          'capabilities': {'file_upload': True, 'vision': True, 'file_context': False,
                                           'builtin_tools': False, 'web_search': False, 'code_interpreter': False,
                                           'image_generation': False, 'terminal': False}, 'knowledge': [], 'toolIds': []},
                 'access_grants': [], 'is_active': True}
        prior = client.get('/api/v1/models/model', params={'id': identifier})
        if prior.status_code == 200:
            response = client.post('/api/v1/models/model/update', params={'id': identifier}, json=model)
        else:
            response = client.post('/api/v1/models/create', json=model)
        response.raise_for_status()
        models = client.get('/api/models')
        models.raise_for_status()
        assert any(m['id'] == identifier for m in models.json()['data']), 'Pipe model is not listed'
        print('Installed and enabled Forestry Runtime; model is visible in /api/models.')
    env_file.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
