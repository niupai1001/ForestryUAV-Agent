"""One pre-registered real report task per discovery mode; never recreate a trial."""
import argparse
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

from fastapi.testclient import TestClient

from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.provenance import source_manifest

ROOT = Path('workspace/discovery-task-20261026')
ALIAS = 'forestry-v2-4b-ctx32768-20261026:latest'
SOURCE = 'qwen3.5:4b'
EXPECTED = 'd8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04'
OBJECTIVE = ('结合已保存的方法笔记与附件中的真实PROSAIL反演统计，生成并保存一份中文Markdown核查报告。'
    '报告应核对LAI、Cab、光谱残差、有效像元和坐标信息，解释定标、波段映射、Cab接近先验上界与结果适用范围，'
    '明确能否据此宣称现场LAI精度。引用输入及方法笔记来源，给出需要补充的独立验证。'
    '只审查既有成果，不重算科学结果；完成后给出报告下载链接。')
CAPS = ['fs.inputs', 'fs.list', 'fs.read', 'fs.write_workspace', 'artifact.inspect', 'artifact.read',
        'document.read', 'memory.read', 'task.plan']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def api(endpoint, payload=None):
    request = urllib.request.Request('http://127.0.0.1:11434/api/' + endpoint,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=60) as response:
        return json.load(response)


def manifest(name):
    model, tag = name.split(':')
    base = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    return json.loads((base / 'manifests/registry.ollama.ai/library' / model / tag).read_bytes())


def prepare():
    ROOT.mkdir(parents=True, exist_ok=False)
    before, tags = api('ps'), api('tags')
    save(ROOT / 'before.json', {'resident': before, 'tags': tags, 'version': api('version')})
    assert not before['models'], 'shared model already occupied; do not unload it'
    assert not any(item['name'] == ALIAS for item in tags['models']), 'never overwrite an existing alias'
    original = next(item for item in tags['models'] if item['name'] == SOURCE)
    assert original['digest'] == EXPECTED, 'original model identity changed'
    request = {'from': SOURCE, 'model': ALIAS, 'parameters': {'num_ctx': 32768}, 'stream': False}
    save(ROOT / 'alias-request.json', request)
    created = api('create', request)
    save(ROOT / 'alias-response.json', created)
    assert created.get('status') == 'success'
    finalize()


def finalize():
    # Complete metadata inspection after a successful create; never recreate it.
    assert not (ROOT / 'protocol.json').exists(), 'protocol already frozen'
    assert json.loads((ROOT / 'alias-response.json').read_text())['status'] == 'success'
    source = api('show', {'model': SOURCE})
    show = api('show', {'model': ALIAS})
    if not (ROOT / 'alias-show.json').exists():
        save(ROOT / 'alias-show.json', show)
    alias = next(item for item in api('tags')['models'] if item['name'] == ALIAS)
    original = next(item for item in api('tags')['models'] if item['name'] == SOURCE)
    assert original['digest'] == EXPECTED
    layers = [(layer['mediaType'], layer['digest']) for layer in manifest(SOURCE)['layers']
              if layer['mediaType'] in {'application/vnd.ollama.image.model', 'application/vnd.ollama.image.projector'}]
    from_paths = lambda shown: [line[5:].strip() for line in shown['modelfile'].splitlines() if line.startswith('FROM ')]
    assert from_paths(source) == from_paths(show), 'server must reference the same weight/projector blobs'
    assert {Path(path).name for path in from_paths(show)} == {value.replace(':', '-') for _, value in layers}
    assert show.get('template') == source.get('template') and show.get('thinking') == source.get('thinking')
    assert 'num_ctx' in show['parameters'] and '32768' in show['parameters']
    catalog_path = Path('workspace/lai-chat-fix-20261022/verified-imagery-catalog.json')
    raw = catalog_path.read_bytes()
    entry = next(item for item in json.loads(raw)['entries'] if item['operation'] == 'invert')
    inputs = ROOT / 'inputs'
    inputs.mkdir()
    summary = {k: v for k, v in entry['summary'].items() if k not in {'declaration', 'limitations'}}
    save(inputs / 'result.json', {'summary': summary, 'provenance': entry['provenance'],
                                 'catalog_sha256': digest(raw)})
    note = {'declaration': entry['declaration'], 'limitations': entry['limitations'],
            'summary_limitations': entry['summary']['limitations']}
    protocol = {'objective': OBJECTIVE, 'modes': ['static', 'native_bm25'], 'harness': 'H1',
        'model': alias, 'original': original, 'weight_layers': layers, 'server_from_paths': from_paths(show),
        'alias_local_manifest_available': False, 'context_limit': 32768,
        'thinking_effort': 'medium', 'temperature': 0.2, 'output_limit': 4096,
        'per_mode_limits': {'model_requests': 12, 'actions': 30, 'seconds': 900, 'scientific_jobs': 0},
        'input_sha256': digest((inputs / 'result.json').read_bytes()), 'source_catalog_sha256': digest(raw),
        'note': note, 'capabilities': CAPS, 'source': source_manifest()['sha256'],
        'script_sha256': digest(Path(__file__).read_bytes()), 'semantic_review_required': True,
        'review_requirements': ['actual statistics and CRS', 'method note provenance',
            'camera normalization not independent calibration', 'Cab boundary limitations without unsupported attribution',
            'spectral fit not field LAI accuracy', 'real saved report and link', 'no recomputation'],
        'scope': 'one development screening task, not held-out, scientific recomputation, or statistical evaluation'}
    save(ROOT / 'protocol.json', protocol)
    print(json.dumps({'prepared': True, 'alias': alias['name'], 'digest': alias['digest']}, ensure_ascii=True), flush=True)


def run(mode):
    protocol = json.loads((ROOT / 'protocol.json').read_text(encoding='utf-8'))
    assert source_manifest()['sha256'] == protocol['source'], 'source changed since freeze'
    assert digest(Path(__file__).read_bytes()) == protocol['script_sha256']
    assert digest((ROOT / 'inputs/result.json').read_bytes()) == protocol['input_sha256']
    directory = ROOT / mode
    directory.mkdir(exist_ok=False)
    save(directory / 'started.json', {'mode': mode, 'started_at': time.time(), 'protocol_sha256': digest((ROOT / 'protocol.json').read_bytes())})
    assert not api('ps')['models'], 'shared model occupied; never unload another owner'
    current = next(item for item in api('tags')['models'] if item['name'] == ALIAS)
    assert current['digest'] == protocol['model']['digest']
    config = load_config('config/app.local.yaml').model_copy(deep=True)
    config.workspace_root = directory.resolve() / 'runtime'
    config.tool_discovery_mode = mode
    config.model_keep_alive = '5m'
    config.model_thinking_effort = protocol['thinking_effort']
    config.max_model_requests_per_turn = config.max_model_requests_per_task = 12
    config.max_actions_per_task = 30
    config.models['small'].served_name = ALIAS
    save(directory / 'config.json', config.model_dump(mode='json'))
    app = create_app(config)
    report = {'mode': mode, 'runtime_window_observations': [], 'task_id': None}
    started = time.monotonic()
    try:
        with TestClient(app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'}) as client:
            client.get('/session')
            def post(path, body):
                response = client.post(path, json=body)
                response.raise_for_status()
                return response.json()
            note = post('/memory', {'request_id': 'method-note', 'text': json.dumps(protocol['note'], ensure_ascii=False),
                'source_note': 'Frozen catalog ' + protocol['source_catalog_sha256'],
                'conditions': 'Review of the existing 20240718 flight only; does not establish independent field accuracy.'})
            report['memory'] = note
            resource = post('/resources/register', {'request_id': 'report-input', 'path': str((ROOT / 'inputs').resolve()), 'classification': 'private'})
            task = post('/tasks', {'request_id': 'report-task', 'objective': protocol['objective'], 'model_profile': 'small',
                'harness_mode': 'H1', 'input_refs': [resource['resource_id']], 'constraints': {'allowed_capabilities': CAPS}})
            report['task_id'] = task['task_id']
            while True:
                state = client.get('/tasks/' + task['task_id']).json()
                resident = api('ps')['models']
                assert all(item['name'] == ALIAS for item in resident), 'another model owner appeared during trial'
                observed = next((item for item in resident if item['name'] == ALIAS), None)
                if observed and not report['runtime_window_observations']:
                    report['runtime_window_observations'].append(observed)
                    save(directory / 'runtime-window.json', observed)
                    assert observed['context_length'] == 32768 and observed['digest'] == current['digest']
                if state['status'] not in {'created', 'active'}:
                    break
                if time.monotonic() - started >= 900:
                    post('/tasks/' + task['task_id'] + '/pause', {'request_id': 'trial-time-limit', 'expected_state_version': state['state_version']})
                    report['time_limit_reached'] = True
                    break
                time.sleep(0.5)
            report['state'] = client.get('/tasks/' + task['task_id']).json()
            report['events'] = app.state.store.events(task['task_id'], limit=100000)
            with app.state.store.snapshot() as db:
                report['contexts'] = [json.loads(row[0]) for row in db.execute('SELECT payload FROM contexts')]
                report['actions'] = [dict(row) for row in db.execute('SELECT * FROM actions')]
                report['jobs_count'] = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
            report['wall_seconds'] = time.monotonic() - started
    finally:
        save(directory / 'report.json', report)
        # Empty generate with keep_alive=0 unloads only this research alias.
        if any(item['name'] == ALIAS for item in api('ps')['models']):
            save(directory / 'release.json', api('generate', {'model': ALIAS, 'keep_alive': 0}))
        save(directory / 'after.json', api('ps'))
    print(json.dumps({'mode': mode, 'task_id': report['task_id'], 'status': report.get('state', {}).get('status'),
                      'seconds': report.get('wall_seconds'), 'window_observed': bool(report['runtime_window_observations'])}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'finalize', 'static', 'native_bm25'])
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare()
    elif args.operation == 'finalize':
        finalize()
    else:
        run(args.operation)
