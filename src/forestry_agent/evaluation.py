"""Frozen local pilot trials through the existing application API, not an agent loop."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from pydantic import Field

from .client import LocalClient, collect_events
from .contracts import Boundary, Failure, TaskConstraints, TaskCreate
from .provenance import runtime_manifest
from .store import encode, now

LIVE = {'created', 'active', 'waiting_job', 'publishing', 'cancelling'}
MAX_FILE_BYTES = 16 * 1024 * 1024


class EvaluationCase(Boundary):
    case_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
    family: str = Field(min_length=1, max_length=80)
    split: str = Field(pattern=r'^pilot$')
    objective: str = Field(min_length=1, max_length=32000)
    inputs: list[str] = Field(min_length=1, max_length=20)
    oracle: str
    result_name: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    report_name: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    constraints: TaskConstraints
    provenance: dict


class EvaluationSuite(Boundary):
    schema_version: int = Field(ge=1, le=1)
    repetitions: int = Field(ge=3, le=100)
    observation_seconds: int = Field(default=30, ge=1, le=60)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=50)


class EvaluationReview(Boundary):
    manifest_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    trial_id: str
    task_id: str
    report_artifact_id: str
    report_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewer: str = Field(min_length=1, max_length=100)
    criteria: dict[str, Literal['passed', 'failed', 'unknown']]
    observations: list[str] = Field(min_length=1, max_length=20)
    failure_category: Literal['model_protocol', 'harness_context', 'tool_environment', 'data_observability',
                             'resource', 'policy_interaction', 'unresolved'] = 'unresolved'


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def file_record(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise Failure('SCHEMA_INVALID', '通用pilot夹具须为不超过16MiB的单文件', 422)
    data = path.read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(uuid4()) + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def roster(cases, repetitions):
    if type(repetitions) is not int or not 3 <= repetitions <= 100:
        raise Failure('SCHEMA_INVALID', '每案例/组合pilot重复次数须为3–100', 422)
    return [{'trial_id': f'{case["case_id"]}-{model}-{harness}-r{repeat}',
             'case_id': case['case_id'], 'model': model, 'harness': harness, 'repeat': repeat,
             'generation_seed': None}
            for case in cases for model in ['small', 'large'] for harness in ['H0', 'H1', 'H2']
            for repeat in range(1, repetitions + 1)]


def freeze_suite(suite_path, config, client, deployment):
    """Capture pilot identity. Unknown release instrumentation stays explicitly unknown."""
    suite_path = Path(suite_path).resolve(strict=True)
    suite = EvaluationSuite.model_validate_json(suite_path.read_text(encoding='utf-8')).model_dump()
    cases = []
    for case in suite['cases']:
        if not case['case_id'].replace('-', '').replace('_', '').isalnum():
            raise Failure('SCHEMA_INVALID', 'case_id只允许字母/数字/连字符/下划线', 422)
        inputs = [file_record(suite_path.parent / item) for item in case['inputs']]
        oracle = file_record(suite_path.parent / case['oracle'])
        if not inputs or oracle['path'] in {item['path'] for item in inputs}:
            raise Failure('SCHEMA_INVALID', '独立评分答案不能注册为任务输入', 422)
        TaskCreate.model_validate({'request_id': 'validate', 'objective': case['objective'],
                                  'constraints': case['constraints']})
        cases.append({**case, 'inputs': inputs, 'oracle': oracle})
    if not 1 <= len(cases) <= 50 or len({case['case_id'] for case in cases}) != len(cases):
        raise Failure('SCHEMA_INVALID', 'pilot案例须为1–50个且ID唯一', 422)
    runtime = client.request('GET', '/runtime')
    if runtime != runtime_manifest(config):
        raise Failure('VERSION_CONFLICT', '服务启动代码/配置与当前评估代码不一致；先核对服务身份', 409)
    root = Path(__file__).resolve().parents[2]
    git = ['git', '-c', f'safe.directory={root}', '-C', str(root)]
    commit = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise Failure('VERSION_CONFLICT', '冻结前须保存已跟踪源码/夹具修改', 409)
    models = {}
    for name in ['small', 'large']:
        observed = deployment['models'].get(name, {})
        if not observed.get('digest') or observed.get('profile') != config.models[name].model_dump():
            raise Failure('MODEL_UNVERIFIED', f'{name}模型部署摘要缺失或配置不同', 422)
        # Never send the application session credential to the model endpoint.
        response = httpx.post(config.ollama_base_url + '/api/show', trust_env=False, timeout=30,
                              json={'model': config.models[name].served_name})
        response.raise_for_status()
        shown = response.json()
        models[name] = {'profile': config.models[name].model_dump(), 'digest': observed['digest'],
                        'precision': observed['precision'], 'template_sha256': digest(shown.get('template')),
                        'model_info_sha256': digest(shown.get('model_info')),
                        'backend_parameters': shown.get('parameters'),
                        'tokenizer_metadata_sha256': digest({k: v for k, v in shown.get('model_info', {}).items() if 'tokenizer' in k})}
    tags = httpx.get(config.ollama_base_url + '/api/tags', trust_env=False, timeout=15).json()['models']
    for item in models.values():
        current = next((model for model in tags if model['name'] == item['profile']['served_name']), {})
        if current.get('digest') != item['digest']:
            raise Failure('MODEL_UNVERIFIED', '冻结过程中权重摘要变化', 409)
    backend = httpx.get(config.ollama_base_url + '/api/version', trust_env=False, timeout=15)
    backend.raise_for_status()
    body = {'schema_version': 1, 'stage': 'general-pilot', 'frozen_at': now(),
            'source_commit': commit, 'runtime': runtime, 'config': config.model_dump(mode='json'),
            'suite_sha256': file_record(suite_path)['sha256'], 'cases': cases,
            'models': models, 'deployment': deployment, 'backend_version': backend.json(),
            'trials': roster(cases, suite['repetitions']),
            'interaction': {'network_approvals': 'explicit_operator_only', 'auto_acceptance': False,
                            'follow_up_messages': False, 'auto_resume_failed_trials': False},
            'generation': {'temperature': 0.2, 'think': False, 'seed': 'not_configurable_in_current_app'},
            'limits': {'concurrency': 1, 'observation_seconds': suite.get('observation_seconds', 30)},
            'release_gates': 'TBD_AFTER_PILOT', 'holdout': 'not_authorized_by_this_pilot_manifest',
            'instrumentation_missing': ['per_worker_peak_resources', 'explicit_generation_seed',
                                        'formal_task_sampling_and_holdout_gates'],
            'scientific_accuracy': 'not_assessed', 'scorer': {'kind': 'independent_exact_json_and_provenance',
                                                          'report_semantics': 'requires_independent_review'}}
    return {'manifest_sha256': digest(body), 'manifest': body}


def verify_manifest(envelope, config):
    body = envelope['manifest']
    if (envelope.get('manifest_sha256') != digest(body) or body.get('stage') != 'general-pilot' or
        type(body.get('schema_version')) is not int or body['schema_version'] != 1):
        raise Failure('VERIFICATION_FAILED', '冻结pilot清单摘要或阶段不匹配', 422)
    if body['config'] != config.model_dump(mode='json') or body['runtime'] != runtime_manifest(config):
        raise Failure('VERSION_CONFLICT', '代码/配置已改变，不能混入冻结试次', 409)
    for case in body['cases']:
        for item in [*case['inputs'], case['oracle']]:
            if file_record(item['path']) != item:
                raise Failure('VERSION_CONFLICT', '冻结输入或独立评分器已改变', 409)
        if case['oracle']['path'] in {item['path'] for item in case['inputs']}:
            raise Failure('VERIFICATION_FAILED', '评分答案与执行输入重合', 422)
    repetitions = max(t['repeat'] for t in body['trials'])
    if body['trials'] != roster(body['cases'], repetitions):
        raise Failure('VERIFICATION_FAILED', '六组合重复清单不完整', 422)
    return body


def trial_snapshot(client, config, task_id):
    state = client.request('GET', f'/tasks/{task_id}')
    events = collect_events(client.http, task_id)
    jobs = [client.request('GET', f'/tasks/{task_id}/jobs/{job_id}') for job_id in
            dict.fromkeys(e['payload']['job_id'] for e in events if e['type'] == 'job.queued')]
    artifacts = client.request('GET', f'/tasks/{task_id}/artifacts')
    db_path = config.workspace_root / '.forestry/state.sqlite'
    with sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True) as db:
        contexts = [json.loads(row[0]) for row in db.execute('SELECT payload FROM contexts WHERE task_id=?', (task_id,))]
    return {'state': state, 'events': events, 'jobs': jobs, 'artifacts': artifacts, 'contexts': contexts,
            'approvals': client.request('GET', f'/tasks/{task_id}/approvals')}


def request_result(config, request_id):
    path = config.workspace_root / '.forestry/state.sqlite'
    with sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True) as db:
        row = db.execute('SELECT response FROM requests WHERE id=?', (request_id,)).fetchone()
        return json.loads(row[0]) if row else None


def artifact_bytes(client, artifact):
    data = bytearray()
    with client.http.stream('GET', f'/artifacts/{artifact["artifact_id"]}/content') as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_FILE_BYTES:
                raise Failure('RESOURCE_EXHAUSTED', 'pilot产物超过读取预算', 422)
    if len(data) != artifact['size_bytes'] or hashlib.sha256(data).hexdigest() != artifact['sha256']:
        raise Failure('VERIFICATION_FAILED', '实际产物字节与来源记录不匹配', 422)
    return bytes(data)


def score_trial(case, snapshot, contents):
    """Answers are read only by this scorer, never supplied to the executing task."""
    oracle = json.loads(Path(case['oracle']['path']).read_text(encoding='utf-8'))
    artifacts = {artifact['name']: artifact for artifact in sorted(snapshot['artifacts'], key=lambda a: a['created_at'])}
    result = artifacts.get(case['result_name'])
    report = artifacts.get(case['report_name'])
    observations = [event['payload']['result'] for event in snapshot['events'] if event['type'] == 'action.observed']
    successful = [job for job in snapshot['jobs'] if job['status'] == 'succeeded']
    checks = {'result_exists': result is not None, 'report_exists': report is not None,
              'input_bytes_unchanged': all(file_record(item['path']) == item for item in case['inputs']),
              'observed_original_failure': any(job['status'] == 'failed' and job['kind'] == 'code.execute' and
                  any(observation.get('job', {}).get('job_id') == job['job_id'] and all(term in
                      (observation.get('logs', {}).get('text', '') + observation.get('logs', {}).get('stderr', ''))
                      for term in oracle['diagnostic_contains']) for observation in observations)
                  for job in snapshot['jobs']),
              'no_search': not any(event['type'] == 'action.started' and event['payload']['tool'] in
                                  ['web.search', 'web.read'] for event in snapshot['events'])}
    if result:
        checks['exact_values'] = json.loads(contents[result['artifact_id']]) == oracle['result']
        checks['result_from_real_calculation'] = any(job['kind'] == 'code.execute' and
            job['action_id'] == result['producer_action_id'] and result['artifact_id'] in job['artifact_ids'] for job in successful)
        checks['required_installed_versions'] = any(job['kind'] == 'code.execute' and
            job['action_id'] == result['producer_action_id'] and all(package in job.get('environment', {}).get('manifest', {}).get('installed', [])
            for package in oracle['required_installed']) for job in successful)
        checks['result_readback'] = full_readback(result, observations, contents[result['artifact_id']])
    if report:
        text = contents[report['artifact_id']].decode('utf-8')
        checks['report_required_text'] = all(term in text for term in oracle['report_contains'])
        checks['report_actual_input_refs'] = all(resource_id in text for resource_id in snapshot['state']['input_refs'])
        checks['report_actual_result_ref'] = bool(result and result['artifact_id'] in text)
        checks['report_readback'] = full_readback(report, observations, contents[report['artifact_id']])
    return {'checks': checks, 'automatic_checks_passed': all(checks.values()),
            'report_semantics': 'not_reviewed', 'full_task_passed': False,
            'notice': '文本字段存在不证明报告主张准确；独立语义审查前不标完整通过。'}


def full_readback(artifact, observations, data):
    covered = 0
    windows = []
    for o in observations:
        if (o.get('artifact_id') != artifact['artifact_id'] or o.get('sha256') != artifact['sha256'] or
            not isinstance(o.get('text'), str) or type(o.get('offset')) is not int or type(o.get('next')) is not int or
            not 0 <= o['offset'] <= o['next'] <= artifact['size_bytes']):
            continue
        try:
            if data[o['offset']:o['next']].decode('utf-8-sig') == o['text']:
                windows.append((o['offset'], o['next']))
        except UnicodeError:
            continue
    windows.sort()
    for start, end in windows:
        if start > covered:
            break
        covered = max(covered, end)
    return bool(windows) and covered >= artifact['size_bytes']


@contextmanager
def pilot_lock(config, manifest_sha256):
    """OS advisory lock automatically released on exit/crash; no lease scheduler."""
    path = config.workspace_root / '.forestry' / f'pilot-{manifest_sha256}.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if __import__('os').name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise Failure('RESOURCE_EXHAUSTED', '同一冻结pilot已有观察/提交进程', 409) from error
        try:
            yield
        finally:
            if __import__('os').name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_trial(envelope, trial_id, directory, config, *, timeout=30, client=None):
    if not 1 <= timeout <= 60:
        raise Failure('SCHEMA_INVALID', '单次观察窗口须为1–60秒，不改变后台预算', 422)
    verify_manifest(envelope, config)
    with pilot_lock(config, envelope['manifest_sha256']):
        return _run_trial(envelope, trial_id, directory, config, timeout=timeout, client=client)


def _run_trial(envelope, trial_id, directory, config, *, timeout, client):
    body = verify_manifest(envelope, config)
    entry = next((t for t in body['trials'] if t['trial_id'] == trial_id), None)
    if not entry:
        raise Failure('SCHEMA_INVALID', '试次ID不在冻结清单中', 422)
    own_client = client is None
    client = client or LocalClient(config)
    try:
        if client.request('GET', '/runtime') != body['runtime']:
            raise Failure('VERSION_CONFLICT', '运行服务与冻结代码/配置不符', 409)
        path = Path(directory) / f'{trial_id}.json'
        case = next(c for c in body['cases'] if c['case_id'] == entry['case_id'])
        namespace = envelope['manifest_sha256'] + ':' + trial_id
        task_request_id = str(uuid5(NAMESPACE_URL, namespace + ':task'))
        registrations = [{'request_id': str(uuid5(NAMESPACE_URL, namespace + f':input:{index}')),
                          'path': item['path'], 'classification': 'private', 'read_only': True}
                         for index, item in enumerate(case['inputs'])]
        if path.exists():
            record = json.loads(path.read_text(encoding='utf-8'))
            if record['manifest_sha256'] != envelope['manifest_sha256'] or record['trial'] != entry:
                raise Failure('VERSION_CONFLICT', '既有试次属于其他清单', 409)
        else:
            # Persist every idempotency key before any registration/task POST.
            record = {'manifest_sha256': envelope['manifest_sha256'], 'trial': entry, 'created_at': now(),
                      'task_request_id': task_request_id, 'registrations': registrations,
                      'task_id': None, 'accepted': False, 'observation': 'prepared_awaiting_reconciliation'}
            write_json(path, record)
        if record['task_request_id'] != task_request_id or record['registrations'] != registrations:
            raise Failure('VERIFICATION_FAILED', '既有请求身份/输入授权记录改变', 422)
        prior = request_result(config, task_request_id)
        if prior:
            if record['task_id'] not in [None, prior['task_id']]:
                raise Failure('VERIFICATION_FAILED', '试次任务与原创建请求不匹配', 422)
            record['task_id'] = prior['task_id']
            record['input_refs'] = [request_result(config, item['request_id'])['resource_id'] for item in registrations]
        elif record['task_id']:
            raise Failure('RECONCILIATION_REQUIRED', '原创建请求缺失，不能重建试次', 409)
        if not record['task_id']:
            if any(task['status'] in LIVE for task in client.request('GET', '/tasks')):
                raise Failure('RESOURCE_EXHAUSTED', '当前有活跃任务；本pilot按单并发提交', 409)
            tags = httpx.get(config.ollama_base_url + '/api/tags', trust_env=False, timeout=15).json()['models']
            current = next((model for model in tags if model['name'] == body['models'][entry['model']]['profile']['served_name']), {})
            if current.get('digest') != body['models'][entry['model']]['digest']:
                raise Failure('MODEL_UNVERIFIED', '提交前权重摘要已变，未创建任务', 409)
            references = [client.request('POST', '/resources/register', item)['resource_id'] for item in record['registrations']]
            payload = {'request_id': record['task_request_id'], 'objective': case['objective'], 'input_refs': references,
                       'model_profile': entry['model'], 'harness_mode': entry['harness'],
                       'context_classification': 'private', 'constraints': case['constraints'],
                       'output_preferences': {'report_language': 'zh'}}
            state = client.request('POST', '/tasks', payload)
            record['task_id'] = state['task_id']
            record['input_refs'] = references
            write_json(path, record)
        deadline = time.monotonic() + timeout
        while True:
            state = client.request('GET', f'/tasks/{record["task_id"]}')
            identity = state['config_snapshot']['model_identity']
            if (identity.get('digest') != body['models'][entry['model']]['digest'] or
                state['config_snapshot']['model_profile'] != entry['model'] or
                state['config_snapshot']['harness_mode'] != entry['harness'] or
                state['objective'] != case['objective'] or state['spec_revision'] != 1 or
                state['input_refs'] != record['input_refs']):
                # Never auto-cancel or retry an already submitted task on a mismatch.
                record.update(observation='model_identity_mismatch', state=state)
                write_json(path, record)
                raise Failure('MODEL_UNVERIFIED', '实际任务权重/规范/输入与冻结试次不同，保留任务等待对账', 409)
            if state['status'] not in LIVE or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        previous_snapshot = record.get('snapshot', {})
        snapshot = trial_snapshot(client, config, record['task_id'])
        state = snapshot['state']
        if (state['objective'] != case['objective'] or state['spec_revision'] != 1 or
            state['input_refs'] != record['input_refs']):
            record.update(snapshot=snapshot, observation='trial_spec_changed')
            write_json(path, record)
            raise Failure('VERSION_CONFLICT', '观察期间任务规范已变，未重跑', 409)
        record.update(observed_at=now(), observation='live' if state['status'] in LIVE else 'attention_or_terminal')
        record['accepted'] = state['status'] == 'completed'
        record['snapshot'] = snapshot
        pending = any(a['status'] in {'pending', 'approved'} for a in snapshot['approvals'])
        record['interaction_required'] = pending
        if state['status'] not in LIVE and not pending:
            selected = [a for a in snapshot['artifacts'] if a['name'] in [case['result_name'], case['report_name']]]
            try:
                contents = {a['artifact_id']: artifact_bytes(client, a) for a in selected}
                record['score'] = score_trial(case, snapshot, contents)
                review = record.get('semantic_review')
                if (review and previous_snapshot.get('state', {}).get('state_version') == state['state_version'] and
                    any(a['artifact_id'] == review['report_artifact_id'] and a['sha256'] == review['report_sha256'] for a in selected)):
                    apply_semantic_score(record['score'], review, state, pending)
            except (Failure, ValueError, UnicodeError) as error:
                record['score'] = {'automatic_checks_passed': False, 'full_task_passed': False,
                                   'report_semantics': 'not_reviewed', 'error': str(error)}
        write_json(path, record)
        return record
    finally:
        if own_client:
            client.close()


def review_trial(envelope, review, directory, config, *, client=None):
    """Attach a separately authored semantic review; never accept the app task."""
    body = verify_manifest(envelope, config)
    review = EvaluationReview.model_validate(review).model_dump()
    path = Path(directory) / f'{review["trial_id"]}.json'
    if review['manifest_sha256'] != envelope['manifest_sha256']:
        raise Failure('VERIFICATION_FAILED', '审查不属于此冻结清单', 422)
    entry = next((entry for entry in body['trials'] if entry['trial_id'] == review['trial_id']), None)
    if not entry:
        raise Failure('SCHEMA_INVALID', '审查试次不在冻结清单中', 422)
    record = json.loads(path.read_text(encoding='utf-8'))
    if record['manifest_sha256'] != envelope['manifest_sha256'] or record['trial'] != entry or record['task_id'] != review['task_id']:
        raise Failure('VERIFICATION_FAILED', '审查任务与记录不同', 422)
    case = next(case for case in body['cases'] if case['case_id'] == entry['case_id'])
    report = max((a for a in record['snapshot']['artifacts'] if a['name'] == case['report_name']), key=lambda a: a['created_at'])
    if report['artifact_id'] != review['report_artifact_id'] or report['sha256'] != review['report_sha256']:
        raise Failure('VERSION_CONFLICT', '审查不是当前报告版本', 409)
    required = {'result_consistency', 'provenance_claims', 'language_and_limitations', 'failure_history', 'execution_boundaries'}
    if set(review['criteria']) != required:
        raise Failure('SCHEMA_INVALID', '审查须逐项覆盖结果、引用、语言/局限、失败历史和执行边界', 422)
    own_client = client is None
    client = client or LocalClient(config)
    try:
        state = client.request('GET', f'/tasks/{record["task_id"]}')
        if state['state_version'] != record['snapshot']['state']['state_version']:
            raise Failure('VERSION_CONFLICT', '审查后任务状态已变，先观察原试次', 409)
        artifact_bytes(client, report)
    finally:
        if own_client:
            client.close()
    record['semantic_review'] = review
    score = record.setdefault('score', {'automatic_checks_passed': False})
    apply_semantic_score(score, review, state, record.get('interaction_required'))
    write_json(path, record)
    return {'trial': entry, 'task_id': record['task_id'], 'score': score, 'auto_accepted': False}


def apply_semantic_score(score, review, state, pending):
    score['report_semantics'] = 'passed' if all(status == 'passed' for status in review['criteria'].values()) else 'failed_or_unknown'
    score['full_task_passed'] = (score['automatic_checks_passed'] is True and score['report_semantics'] == 'passed' and
        not pending and state['status'] in ['waiting_user', 'completed'] and
        all(blocker['code'] == 'ACCEPTANCE_REQUIRED' for blocker in state['blockers']))


def summarize(envelope, directory):
    body = envelope['manifest']
    if digest(body) != envelope['manifest_sha256']:
        raise Failure('VERIFICATION_FAILED', '清单摘要不匹配', 422)
    records = []
    for entry in body['trials']:
        path = Path(directory) / f'{entry["trial_id"]}.json'
        if not path.exists():
            records.append({'trial': entry, 'observation': 'not_run'})
            continue
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['manifest_sha256'] != envelope['manifest_sha256'] or record['trial'] != entry:
            raise Failure('VERIFICATION_FAILED', '试次与冻结清单不匹配', 422)
        records.append(record)
    task_ids = [record['task_id'] for record in records if record.get('task_id')]
    if len(task_ids) != len(set(task_ids)):
        raise Failure('VERIFICATION_FAILED', '不同重复试次不能计入同一个任务', 422)
    groups = []
    for model in ['small', 'large']:
        for harness in ['H0', 'H1', 'H2']:
            selected = [r for r in records if r['trial']['model'] == model and r['trial']['harness'] == harness]
            contexts = [c for r in selected for c in r.get('snapshot', {}).get('contexts', [])]
            groups.append({'model': model, 'harness': harness, 'scheduled': len(selected),
                'observed': sum('snapshot' in r for r in selected),
                'automatic_passed': sum(r.get('score', {}).get('automatic_checks_passed', False) for r in selected),
                'full_task_passed': sum(r.get('score', {}).get('full_task_passed', False) for r in selected),
                'awaiting_interaction': sum(r.get('interaction_required', False) for r in selected),
                'approval_requests': sum(len(r.get('snapshot', {}).get('approvals', [])) for r in selected),
                'approval_decisions': sum(a['status'] != 'pending' for r in selected for a in r.get('snapshot', {}).get('approvals', [])),
                'live': sum(r.get('observation') == 'live' for r in selected),
                'observed_wall_seconds': sum((datetime.fromisoformat(r['observed_at']) - datetime.fromisoformat(r['created_at'])).total_seconds()
                                             for r in selected if 'observed_at' in r),
                'measured_requests': sum(c.get('usage_measurement_status') == 'measured' for c in contexts),
                'input_tokens': sum(c.get('actual_input_tokens') or 0 for c in contexts),
                'output_tokens': sum(c.get('actual_output_tokens') or 0 for c in contexts),
                'unmeasured_requests': sum(c.get('usage_measurement_status') != 'measured' for c in contexts)})
    return {'manifest_sha256': envelope['manifest_sha256'], 'groups': groups,
            'all_trials': [{'trial': r['trial'], 'task_id': r.get('task_id'), 'observation': r.get('observation', 'prepared_awaiting_reconciliation'),
                           'blockers': r.get('snapshot', {}).get('state', {}).get('blockers', []),
                           'failure_category': r.get('semantic_review', {}).get('failure_category', 'unresolved'),
                           'score': r.get('score')} for r in records],
            'confidence_interval': 'not_estimated_repetitions_are_not_independent_tasks',
            'stage_1_passed': False, 'release_gates': body['release_gates'],
            'notice': '包括未运行/失败/待交互；自动字段检查不能替代报告审查或完整阶段验收。'}
