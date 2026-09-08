"""Run in Open WebUI. Update only Forestry functions and retain existing settings."""
import json
from pathlib import Path
import time

from install_forestry_runtime import admin_client


def main():
    env_file = Path('/tmp/forestry_runtime.env')
    key = next(line.split('=', 1)[1].strip() for line in env_file.read_text(encoding='utf-8-sig').splitlines()
               if line.startswith('RUNTIME_API_KEY='))
    with admin_client() as client:
        prior = client.get('/api/v1/functions/id/forestry_cleanup')
        existing_valves = client.get('/api/v1/functions/id/forestry_cleanup/valves') if prior.status_code == 200 else None
        saved = existing_valves.json() if existing_valves is not None and existing_valves.status_code == 200 else {}
        since = saved.get('MANAGED_SINCE') or int(time.time())
        backup = Path('/app/backend/data/forestry_runtime_backups')
        backup.mkdir(exist_ok=True)
        for identifier, source, name in [
            ('forestry_runtime', '/tmp/forestry_runtime_pipe.py', 'Forestry Runtime'),
            ('forestry_cleanup', '/tmp/forestry_cleanup.py', 'Forestry Chat Asset Cleanup'),
        ]:
            route = f'/api/v1/functions/id/{identifier}'
            previous = client.get(route)
            if previous.status_code not in (200, 404, 401):
                previous.raise_for_status()
            if previous.status_code == 200:
                details = previous.json()
                (backup / f'{identifier}_{time.time_ns()}.json').write_text(json.dumps(details, ensure_ascii=False))
                payload = {k: details[k] for k in ('id', 'name', 'meta')}
                target = route + '/update'
                old_valves = client.get(route + '/valves')
                old_valves.raise_for_status()
                valves = old_valves.json() or {}
            else:
                payload = {'id': identifier, 'name': name, 'meta': {'description': name}}
                target = '/api/v1/functions/create'
                valves = {}
            payload['content'] = Path(source).read_text(encoding='utf-8')
            response = client.post(target, json=payload)
            response.raise_for_status()
            expected_type = 'pipe' if identifier == 'forestry_runtime' else 'event'
            if response.json().get('type') != expected_type:
                raise RuntimeError(f'{identifier} was not recognized as {expected_type}')
            valves.update({'API_KEY': key, 'MANAGED_SINCE': since})
            valves.setdefault('RUNTIME_URL', 'http://host.docker.internal:8010')
            response = client.post(route + '/valves/update', json=valves)
            response.raise_for_status()
            if not client.get(route).json().get('is_active'):
                response = client.post(route + '/toggle')
                response.raise_for_status()
            print(f'Installed {identifier}; active; managed_since={since}')
    env_file.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
