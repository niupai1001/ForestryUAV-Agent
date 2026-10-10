"""Real offline dependency failures, installation and reuse, without a model.

Only the trusted fixture downloader acquires public bytes. Preparation and all
package execution occur in real restricted containers; existing services stay up.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.jobs import TERMINAL
from forestry_agent.resources import file_hash, native_path

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


def host_package_hash():
    packages = sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())
    return hashlib.sha256(json.dumps(packages).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config/app.wsl.example.yaml')
    parser.add_argument('--fixture-lock', required=True)
    parser.add_argument('--subprocess-only', action='store_true', help='Only install six and check child interpreter reuse')
    args = parser.parse_args()
    identifier = str(uuid4())
    directory = ROOT/'workspace'/'dependency-trials'/identifier
    directory.mkdir(parents=True)
    config = load_config(args.config).model_copy(update={'workspace_root': directory/'runtime'})
    fixture = json.loads(Path(args.fixture_lock).read_text(encoding='utf-8'))
    records = fixture['wheels']
    for record in records:
        assert file_hash(Path(record['path'])) == record['sha256'], 'Fixture changed since public acquisition'
    original_host = host_package_hash()
    evidence = {'trial_id': identifier, 'model_used': False, 'agent_download': False,
        'real_process_restart': False, 'scientific_accuracy': 'not_assessed',
        'general_stage_acceptance': 'incomplete', 'fixture_lock': fixture, 'cases': [], 'all_passed': False}
    output = ROOT/'workspace'/'evidence'/('dependencies-real-'+identifier+'.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(config, enable_agent=False)
    engine, jobs = app.state.engine, app.state.engine.jobs
    with TestClient(app, base_url='http://127.0.0.1', headers={'origin':'http://127.0.0.1'}) as client:
        client.get('/session').raise_for_status()
        refs = {}
        for record in records:
            reply = client.post('/resources/register', json={'request_id':str(uuid4()), 'path':record['path'],
                'classification':'public', 'read_only':True})
            reply.raise_for_status()
            refs[record['filename']] = reply.json()['resource_id']
        source = directory/'heights.csv'
        source.write_bytes(b'height_m\n8.25\n12.50\n16.75\n')
        source_sha = file_hash(source)
        reply = client.post('/resources/register', json={'request_id':str(uuid4()), 'path':str(source),
            'classification':'private', 'read_only':True})
        reply.raise_for_status()
        source_ref = reply.json()['resource_id']
        reply = client.post('/tasks', json={'request_id':str(uuid4()), 'objective':'实际离线依赖失败、修复及复用验证',
            'input_refs':[source_ref, *refs.values()], 'constraints':{'allowed_capabilities':[
                'code.execute', 'environment.prepare', 'job.observe', 'artifact.read']}})
        reply.raise_for_status()
        task_id = reply.json()['task_id']
        evidence['task_id'] = task_id
        deps = Deps(engine, task_id, 'dependency-smoke-'+identifier, 1)
        submitted_jobs = []
        print(json.dumps({'task_id':task_id, 'trial_id':identifier}), flush=True)

        def observe(result, scenario):
            assert result['kind'] == 'completed', result
            job_id = result['job']['job_id']
            submitted_jobs.append(job_id)
            deadline = time.monotonic()+180
            while time.monotonic() < deadline:
                jobs.tick()
                job = jobs.read(job_id, task_id)
                if job['status'] in TERMINAL or job.get('blocker'):
                    break
                time.sleep(0.3)
            value = {'scenario':scenario, 'job':job, 'logs':jobs.logs(job_id, task_id),
                'container':jobs.docker.inspect(job['container_name'])}
            evidence['cases'].append(value)
            print(json.dumps({'scenario':scenario, 'job_id':job_id, 'status':job['status'],
                'blocker':job.get('blocker')}), flush=True)
            if job['status'] not in TERMINAL:
                raise RuntimeError('Observation expired or blocked; job retained for scoped cleanup: '+job_id)
            return value

        def prepare(wheel_refs, scenario):
            return observe(engine.perform(deps, str(uuid4()), 'environment.prepare', {'input_refs':wheel_refs},
                lambda action: engine.environments.prepare(task_id, 1, action, wheel_refs)), scenario)

        def code_run(code, scenario, environment=None, inputs=None):
            arguments = {'code':code, 'input_refs':inputs or []}
            if environment:
                arguments['environment_artifact_id'] = environment
            return observe(engine.perform(deps, str(uuid4()), 'code.execute', arguments,
                lambda action: jobs.submit(task_id, 1, action, code, inputs or [], 60,
                    environment_artifact_id=environment)), scenario)

        def subprocess_case():
            ready = prepare([refs['six-1.17.0-py2.py3-none-any.whl']], 'subprocess_environment')
            assert ready['job']['status'] == 'succeeded', ready['logs']
            archive = next(a for a in ready['job']['artifact_ids'] if engine.resources.artifact(a)['name'] == 'environment.zip')
            environment = engine.environments.resolve(task_id, 1, archive)
            child = f'''import csv, importlib.metadata, json, os, socket
import six
values = [float(row['height_m']) for row in csv.DictReader(open('/inputs/{source_ref}'))]
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
    blocked = False
except OSError:
    blocked = True
print(json.dumps(dict(six_version=importlib.metadata.version('six'), total=sum(values), uid=os.getuid(), network_blocked=blocked)))
'''
            code = f'''import importlib.metadata, json, subprocess, sys
from pathlib import Path
parent_version = importlib.metadata.version('six')
child = subprocess.run([sys.executable, '-c', {child!r}], capture_output=True, text=True, timeout=20)
explicit = subprocess.run([sys.executable, '-c', 'import six'], env={{}}, capture_output=True, text=True, timeout=20)
result = dict(parent_version=parent_version, child_exit=child.returncode, child_stderr=child.stderr,
              child=json.loads(child.stdout) if child.returncode == 0 else None,
              explicit_override_exit=explicit.returncode, explicit_override_stderr=explicit.stderr)
Path('/output/environment-subprocess.json').write_text(json.dumps(result), encoding='utf-8')
print(json.dumps(result))
'''
            executed = code_run(code, 'subprocess_reuse', archive, [source_ref])
            product = next(engine.resources.artifact(a) for a in executed['job']['artifact_ids']
                           if engine.resources.artifact(a)['name'] == 'environment-subprocess.json')
            numbers = json.loads(native_path(product['locator']).read_bytes())
            executed['numbers'] = numbers
            checks = {
                'environment_ready':ready['job']['status'] == 'succeeded',
                'real_job_succeeded':executed['job']['status'] == 'succeeded',
                'same_environment':executed['job']['environment']['artifact_id'] == archive,
                'same_image':executed['container']['Image'] == environment['image_id'],
                'parent_version':numbers['parent_version'] == '1.17.0',
                'child_version':numbers['child_exit'] == 0 and numbers['child'].get('six_version') == '1.17.0',
                'child_reads_same_input':numbers['child_exit'] == 0 and numbers['child'].get('total') == 37.5,
                'child_unprivileged':numbers['child_exit'] == 0 and numbers['child'].get('uid') == 65534,
                'child_network_blocked':numbers['child_exit'] == 0 and numbers['child'].get('network_blocked') is True,
                'explicit_env_override_respected':numbers['explicit_override_exit'] != 0 and 'ModuleNotFoundError' in numbers['explicit_override_stderr'],
                'containers_restricted':all(c['container']['HostConfig']['NetworkMode'] == 'none' and c['container']['HostConfig']['ReadonlyRootfs']
                    and 'ALL' in c['container']['HostConfig']['CapDrop'] for c in evidence['cases']),
                'source_unchanged':file_hash(source) == source_sha,
                'wheel_unchanged':all(file_hash(Path(r['path'])) == r['sha256'] for r in records),
                'host_packages_unchanged':host_package_hash() == original_host,
            }
            evidence.update(checks=checks, all_passed=all(checks.values()), state=engine.store.describe_task(task_id),
                            events=engine.store.events(task_id, limit=1000), environment={k:v for k,v in environment.items() if k != 'source'})
            print(json.dumps({'checks':checks}), flush=True)

        try:
            if args.subprocess_only:
                subprocess_case()
            else:
                missing = code_run('import numpy\nprint(numpy.__version__)\n', 'missing_numpy')
                dateutil = refs['python_dateutil-2.9.0.post0-py2.py3-none-any.whl']
                incomplete = prepare([dateutil], 'missing_transitive_six')
                incompatible = prepare([refs['numpy-2.2.6-cp312-cp312-win_amd64.whl']], 'incompatible_windows_wheel')
                ready = prepare([refs['numpy-2.2.6-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'],
                    dateutil, refs['six-1.17.0-py2.py3-none-any.whl']], 'locked_environment')
                assert ready['job']['status'] == 'succeeded', ready['logs']
                archive = next(a for a in ready['job']['artifact_ids'] if engine.resources.artifact(a)['name'] == 'environment.zip')
                environment = engine.environments.resolve(task_id, 1, archive)
                evidence['environment'] = {k:v for k,v in environment.items() if k != 'source'}
                reopened = create_app(config, enable_agent=False)
                assert reopened.state.engine.environments.resolve(task_id, 1, archive) == environment
                computations = []
                for index in range(2):
                    code = f'''import csv, json, os, socket
    import numpy as np, six, dateutil
    from dateutil.parser import parse
    from pathlib import Path
    values = np.array([float(r['height_m']) for r in csv.DictReader(Path('/inputs/{source_ref}').open())])
    try:
        socket.create_connection(('1.1.1.1',443), timeout=1)
        network_blocked = False
    except OSError:
        network_blocked = True
    result = dict(tree_count=int(values.size), total_height_m=float(values.sum()), mean_height_m=float(values.mean()),
        parsed_date=parse('2026-10-06').date().isoformat(), numpy_version=np.__version__, dateutil_version=dateutil.__version__,
        six_version=six.__version__, uid=os.getuid(), network_blocked=network_blocked)
    Path('/output/result-{index}.json').write_text(json.dumps(result), encoding='utf-8')
    print('ENVIRONMENT_REUSED '+json.dumps(result), flush=True)
    '''
                    case = code_run(code, 'reuse_'+str(index), archive, [source_ref])
                    products = [engine.resources.artifact(a) for a in case['job']['artifact_ids']]
                    numbers = next((json.loads(Path(a['locator']).read_bytes()) for a in products if a['name'] == f'result-{index}.json'), {})
                    case['numbers'] = numbers
                    computations.append(case)
                installed = {d['name'].lower():d['version'] for d in environment['manifest']['installed']}
                checks = {'actual_missing_import':missing['job']['status'] == 'failed' and 'ModuleNotFoundError' in missing['logs']['stderr'],
                    'actual_missing_transitive':incomplete['job']['status'] == 'failed' and 'six' in incomplete['logs']['stderr'],
                    'actual_incompatible_wheel':incompatible['job']['status'] == 'failed' and 'not a supported wheel' in incompatible['logs']['stderr'],
                    'actual_environment_ready':ready['job']['status'] == 'succeeded' and 'ENVIRONMENT_READY' in ready['logs']['text'],
                    'exact_install_lock':installed == {'numpy':'2.2.6', 'python-dateutil':'2.9.0.post0', 'six':'1.17.0'},
                    'same_environment_reused':all(c['job']['environment']['artifact_id'] == archive and c['job']['environment']['sha256'] == environment['sha256'] for c in computations),
                    'same_image_identity':all(c['container']['Image'] == environment['image_id'] for c in computations),
                    'computations_succeeded':all(c['job']['status'] == 'succeeded' for c in computations),
                    'exact_numbers':all(all(c['numbers'].get(k) == v for k,v in {'tree_count':3,'total_height_m':37.5,'mean_height_m':12.5,'parsed_date':'2026-10-06'}.items()) for c in computations),
                    'actual_unprivileged':all(c['numbers'].get('uid') == 65534 for c in computations),
                    'actual_network_blocked':all(c['numbers'].get('network_blocked') is True for c in computations),
                    'all_containers_restricted':all(c['container']['HostConfig']['NetworkMode'] == 'none' and c['container']['HostConfig']['ReadonlyRootfs'] and 'ALL' in c['container']['HostConfig']['CapDrop'] for c in evidence['cases']),
                    'source_unchanged':file_hash(source) == source_sha,
                    'wheel_sources_unchanged':all(file_hash(Path(r['path'])) == r['sha256'] for r in records),
                    'host_packages_unchanged':host_package_hash() == original_host}
                evidence.update(checks=checks, all_passed=all(checks.values()), state=engine.store.describe_task(task_id),
                    events=engine.store.events(task_id, limit=1000))
                print(json.dumps({'checks':checks}), flush=True)
        except Exception as error:
            logger.exception('Real dependency trial failed')
            evidence['error'] = {'type':type(error).__name__, 'message':str(error)}
        finally:
            cleanup = []
            for job_id in submitted_jobs:
                try:
                    job = jobs.read(job_id, task_id)
                    if job['status'] not in TERMINAL:
                        jobs.request_cancel(job_id, task_id)
                        jobs.tick()
                        job = jobs.read(job_id, task_id)
                    cleanup.append({'job_id':job_id, 'status':job['status'], 'confirmed_terminal':job['status'] in TERMINAL,
                        'blocker':job.get('blocker')})
                except Exception as error:
                    logger.exception('Owned dependency job cleanup was not confirmed')
                    cleanup.append({'job_id':job_id, 'confirmed_terminal':False, 'error':str(error)})
            evidence['cleanup'] = cleanup
            if not all(j['confirmed_terminal'] for j in cleanup):
                evidence['all_passed'] = False
            output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
            print(output, flush=True)
    return 0 if evidence['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
