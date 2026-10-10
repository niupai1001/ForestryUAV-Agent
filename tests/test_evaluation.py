import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest
from fastapi.testclient import TestClient

from forestry_agent import evaluation
from forestry_agent.config import Config, ModelProfile
from forestry_agent.contracts import Failure


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    config = Config(workspace_root=tmp_path/'runtime', models={
        key: ModelProfile(repository=key, served_name=key) for key in ['small', 'large']})
    source = tmp_path/'source.csv'
    source.write_text('amount\n12.50\n37.50\n62.50\n', encoding='utf-8')
    oracle = tmp_path/'oracle.json'
    oracle.write_text(json.dumps({'result': {'mean': 37.5}, 'report_contains': ['平均', '局限'],
        'required_installed': [{'name': 'six', 'version': '1.17.0'}],
        'diagnostic_contains': ['ModuleNotFoundError', 'six']}), encoding='utf-8')
    case = {'case_id': 'CASE', 'objective': 'Read input and repair code; save exact result and Chinese report.',
        'inputs': [evaluation.file_record(source)], 'oracle': evaluation.file_record(oracle),
        'result_name': 'result.json', 'report_name': 'report.md', 'constraints': {'allowed_capabilities': ['fs.read']}}
    runtime = {'source': {'sha256': 'code'}}
    monkeypatch.setattr(evaluation, 'runtime_manifest', lambda _: runtime)
    body = {'schema_version': 1, 'stage': 'general-pilot', 'config': config.model_dump(mode='json'), 'runtime': runtime, 'cases': [case],
        'trials': evaluation.roster([case], 3), 'models': {key: {'profile': config.models[key].model_dump(), 'digest': key+'-digest'}
        for key in ['small', 'large']}, 'release_gates': 'TBD_AFTER_PILOT'}
    return config, case, {'manifest': body, 'manifest_sha256': evaluation.digest(body)}


def test_six_combinations_three_repetitions_and_unrun_denominators(pilot, tmp_path):
    _, _, envelope = pilot
    report = evaluation.summarize(envelope, tmp_path/'trials')
    assert len(report['all_trials']) == 18
    assert len({r['trial']['trial_id'] for r in report['all_trials']}) == 18
    assert {(g['model'], g['harness'], g['scheduled'], g['observed']) for g in report['groups']} == {
        (model, harness, 3, 0) for model in ['small', 'large'] for harness in ['H0', 'H1', 'H2']}
    assert not report['stage_1_passed']
    assert all(r['observation'] == 'not_run' for r in report['all_trials'])


@pytest.mark.parametrize('kind', ['input', 'oracle', 'roster'])
def test_frozen_mutation_refused_before_task_creation(pilot, kind):
    config, case, envelope = pilot
    if kind == 'roster':
        envelope['manifest']['trials'].pop()
        envelope['manifest_sha256'] = evaluation.digest(envelope['manifest'])
    else:
        path = case['inputs'][0]['path'] if kind == 'input' else case['oracle']['path']
        from pathlib import Path
        Path(path).write_text('changed', encoding='utf-8')
    with pytest.raises(Failure):
        evaluation.verify_manifest(envelope, config)


def test_oracle_cannot_be_registered_as_input(pilot):
    config, case, envelope = pilot
    case['inputs'] = [case['oracle']]
    envelope['manifest_sha256'] = evaluation.digest(envelope['manifest'])
    with pytest.raises(Failure, match='评分答案'):
        evaluation.verify_manifest(envelope, config)


def score_data(case):
    result = json.dumps({'mean': 37.5}).encode()
    report = '平均37.5；局限；源ref-source；结果result-id'.encode()
    artifacts = [{'artifact_id': ref, 'name': name, 'producer_action_id': producer,
        'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data), 'created_at': '2026-10-10'}
        for ref, name, producer, data in [('result-id', 'result.json', 'calculate', result),
                                        ('report-id', 'report.md', 'write', report)]]
    observations = [{'job': {'job_id': 'failed'}, 'logs': {'stderr': "ModuleNotFoundError: No module named 'six'"}}]
    observations.extend({'artifact_id': a['artifact_id'], 'sha256': a['sha256'], 'offset': 0,
                         'next': a['size_bytes'], 'text': data.decode('utf-8')} for a, data in zip(artifacts, [result, report]))
    snapshot = {'state': {'input_refs': ['ref-source']}, 'artifacts': artifacts,
        'jobs': [{'job_id': 'failed', 'kind': 'code.execute', 'status': 'failed'},
                 {'job_id': 'calculation', 'kind': 'code.execute', 'status': 'succeeded', 'action_id': 'calculate',
                  'artifact_ids': ['result-id'], 'environment': {'manifest': {'installed': [{'name': 'six', 'version': '1.17.0'}]}}}],
        'events': [{'type': 'action.observed', 'payload': {'result': o}} for o in observations]}
    return snapshot, {'result-id': result, 'report-id': report}


def test_correct_values_do_not_replace_real_job_or_semantic_review(pilot):
    _, case, _ = pilot
    snapshot, contents = score_data(case)
    score = evaluation.score_trial(case, snapshot, contents)
    assert score['automatic_checks_passed']
    assert not score['full_task_passed'] and score['report_semantics'] == 'not_reviewed'
    snapshot['artifacts'][0]['producer_action_id'] = 'model-written-guess'
    score = evaluation.score_trial(case, snapshot, contents)
    assert score['checks']['exact_values']
    assert not score['checks']['result_from_real_calculation']
    assert not score['automatic_checks_passed']


def test_readback_with_gap_is_not_full_and_failure_must_be_actual_diagnostic(pilot):
    _, case, _ = pilot
    snapshot, contents = score_data(case)
    snapshot['events'][1]['payload']['result']['offset'] = 5
    snapshot['events'][0]['payload']['result']['logs']['stderr'] = 'unrelated environment failure'
    score = evaluation.score_trial(case, snapshot, contents)
    assert not score['checks']['result_readback']
    assert not score['checks']['observed_original_failure']


@pytest.fixture
def captured_failure(pilot, tmp_path):
    config, case, envelope = pilot
    original = tmp_path/'original.py'
    original.write_text('import six\n', encoding='utf-8')
    case['inputs'].insert(0, evaluation.file_record(original))
    guide = tmp_path/'instructions.md'
    guide.write_text('Execute the original program using the CSV; this guide need not be mounted.', encoding='utf-8')
    case['inputs'].append(evaluation.file_record(guide))
    envelope['manifest_sha256'] = evaluation.digest(envelope['manifest'])
    trial = envelope['manifest']['trials'][0]
    snapshot, contents = score_data(case)
    state = snapshot['state']
    state.update(task_id='task-one', input_refs=['ref-program', 'ref-source', 'ref-guide'], state_version=7,
                 objective=case['objective'], spec_revision=1, status='paused',
                 config_snapshot={'model_profile': 'small', 'harness_mode': 'H0',
                                  'model_identity': {'digest': 'small-digest'}})
    code = ('import subprocess, sys\n'
            'proc = subprocess.run([sys.executable, "/inputs/ref-program"], capture_output=True, text=True)\n'
            'print(proc.returncode, proc.stderr)\n')
    code_sha = hashlib.sha256(code.encode()).hexdigest()
    job = snapshot['jobs'][0]
    job.update(status='succeeded', code_sha256=code_sha, prepared_code_sha256=code_sha,
               container_id='container-one', image_id='image-one', action_id='capture-original', artifact_ids=[])
    event = snapshot['events'][0]
    event['event_id'] = 'failure-observation'
    event['payload']['result']['logs'] = {'text': '1\nTraceback: ModuleNotFoundError: six\n', 'bounded': True}
    directory = tmp_path/'trials'
    record = {'manifest_sha256': envelope['manifest_sha256'], 'trial': trial, 'task_id': 'task-one',
              'snapshot': snapshot, 'score': evaluation.score_trial(case, snapshot, contents), 'interaction_required': False}
    evaluation.write_json(directory/f'{trial["trial_id"]}.json', record)
    config.workspace_root.mkdir(parents=True)
    runtime_dir = config.workspace_root/'.forestry'
    runtime_dir.mkdir()
    job_dir = runtime_dir/'job'
    job_dir.mkdir()
    (job_dir/'run.py').write_text(code, encoding='utf-8', newline='\n')
    raw_job = {**job, 'code': code, 'directory': str(job_dir), 'mounts': [
        {'resource_id': ref, 'source': item['path'], 'target': '/inputs/'+ref}
        for ref, item in zip(state['input_refs'][:2], case['inputs'][:2], strict=True)]}
    namespace = envelope['manifest_sha256']+':'+trial['trial_id']
    with sqlite3.connect(runtime_dir/'state.sqlite') as db:
        db.executescript('CREATE TABLE requests(id TEXT,response TEXT);'
                         'CREATE TABLE jobs(id TEXT,task_id TEXT,payload TEXT);'
                         'CREATE TABLE events(id TEXT,task_id TEXT,kind TEXT,payload TEXT);')
        db.execute('INSERT INTO requests VALUES(?,?)',
                   (str(uuid5(NAMESPACE_URL, namespace+':task')), json.dumps({'task_id': 'task-one'})))
        for i, ref in enumerate(state['input_refs']):
            db.execute('INSERT INTO requests VALUES(?,?)',
                       (str(uuid5(NAMESPACE_URL, namespace+f':input:{i}')), json.dumps({'resource_id': ref})))
        db.execute('INSERT INTO jobs VALUES(?,?,?)', (job['job_id'], 'task-one', evaluation.encode(raw_job)))
        db.execute('INSERT INTO events VALUES(?,?,?,?)',
                   (event['event_id'], 'task-one', event['type'], evaluation.encode(event['payload'])))
    review = {'manifest_sha256': envelope['manifest_sha256'], 'trial_id': trial['trial_id'], 'task_id': 'task-one',
              'job_id': job['job_id'], 'code_sha256': code_sha,
              'logs_sha256': evaluation.digest(event['payload']['result']['logs']),
              'input_refs': state['input_refs'][:2],
              'reviewer': 'Independent reviewer',
              'criteria': dict.fromkeys(['original_program_invoked', 'nonzero_exit_captured', 'diagnostic_from_execution'], 'passed'),
              'observations': ['Reviewed submitted subprocess invocation, frozen mounts and captured nonzero result.']}
    calls = []

    class Client:
        def request(self, method, endpoint):
            calls.append((method, endpoint))
            assert method == 'GET'
            return job if '/jobs/' in endpoint else state

    return config, envelope, directory, review, Client(), calls, contents, raw_job


def test_rescore_captured_original_failure_is_read_only_and_preserves_old_grade(captured_failure, monkeypatch):
    config, envelope, directory, review, client, calls, contents, _ = captured_failure
    record_path = directory/f'{review["trial_id"]}.json'
    before = record_path.read_bytes()
    monkeypatch.setattr(evaluation, 'runtime_manifest', lambda _: {'source': {'sha256': 'new-code'}})
    with pytest.raises(Failure, match='代码/配置'):
        evaluation.verify_manifest(envelope, config)
    monkeypatch.setattr(evaluation, 'artifact_bytes', lambda _, a: contents[a['artifact_id']])
    result = evaluation.rescore_trial(envelope, review, directory, config, client=client)
    assert not result['previous_score']['checks']['observed_original_failure']
    assert result['score']['checks']['observed_original_failure']
    assert not result['score']['full_task_passed'] and not result['auto_accepted']
    assert result['source_record_sha256'] == hashlib.sha256(before).hexdigest()
    assert record_path.read_bytes() == before and len(calls) == 2


@pytest.mark.parametrize('changed', ['code', 'prepared', 'event', 'input', 'logs_hash', 'task_state'])
def test_diagnostic_review_rejects_changed_provenance(captured_failure, monkeypatch, changed):
    config, envelope, directory, review, client, _, contents, job = captured_failure
    if changed == 'prepared':
        Path(job['directory'], 'run.py').write_text('print("fabricated traceback")', encoding='utf-8')
    elif changed == 'input':
        Path(envelope['manifest']['cases'][0]['inputs'][0]['path']).write_text('print("changed")', encoding='utf-8')
    elif changed == 'logs_hash':
        review['logs_sha256'] = '0'*64
    elif changed == 'task_state':
        original = client.request
        client.request = lambda method, endpoint: {**original(method, endpoint), 'state_version': 8}
    else:
        with sqlite3.connect(config.workspace_root/'.forestry/state.sqlite') as db:
            if changed == 'code':
                job['code'] = 'print("fabricated traceback")'
                db.execute('UPDATE jobs SET payload=?', (evaluation.encode(job),))
            else:
                db.execute('UPDATE events SET payload=?', ('{}',))
    monkeypatch.setattr(evaluation, 'artifact_bytes', lambda _, a: contents[a['artifact_id']])
    with pytest.raises(Failure):
        evaluation.rescore_trial(envelope, review, directory, config, client=client)


@pytest.mark.parametrize('decision', ['failed', 'unknown'])
def test_printed_diagnostic_without_independent_execution_proof_is_not_accepted(captured_failure, monkeypatch, decision):
    config, envelope, directory, review, client, _, contents, _ = captured_failure
    review['criteria']['diagnostic_from_execution'] = decision
    monkeypatch.setattr(evaluation, 'artifact_bytes', lambda _, a: contents[a['artifact_id']])
    result = evaluation.rescore_trial(envelope, review, directory, config, client=client)
    assert not result['score']['checks']['observed_original_failure']
    assert not result['score']['automatic_checks_passed']
    assert not result['score']['full_task_passed']


@pytest.mark.parametrize('wrong_report', [False, True])
def test_rescore_reuses_only_exact_semantic_report_review(captured_failure, monkeypatch, wrong_report):
    config, envelope, directory, review, client, _, contents, _ = captured_failure
    path = directory/f'{review["trial_id"]}.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    state = client.request('GET', '/tasks/task-one')
    state.update(status='waiting_user', blockers=[{'code': 'ACCEPTANCE_REQUIRED'}])
    record['snapshot']['state'] = state
    record['semantic_review'] = {
        'manifest_sha256': envelope['manifest_sha256'], 'trial_id': review['trial_id'], 'task_id': review['task_id'],
        'report_artifact_id': 'report-id', 'report_sha256': '0'*64 if wrong_report else record['snapshot']['artifacts'][1]['sha256'],
        'reviewer': 'Independent report reviewer', 'observations': ['Reviewed exact report.'],
        'criteria': dict.fromkeys(['result_consistency', 'provenance_claims', 'language_and_limitations',
                                  'failure_history', 'execution_boundaries'], 'passed'),
    }
    evaluation.write_json(path, record)
    monkeypatch.setattr(evaluation, 'artifact_bytes', lambda _, a: contents[a['artifact_id']])
    if wrong_report:
        with pytest.raises(Failure, match='语义审查'):
            evaluation.rescore_trial(envelope, review, directory, config, client=client)
    else:
        result = evaluation.rescore_trial(envelope, review, directory, config, client=client)
        assert result['score']['report_semantics'] == 'passed'
        assert not result['score']['full_task_passed']  # Report still omits other required input refs.
        assert not result['auto_accepted']


@pytest.mark.parametrize('status', ['active', 'paused', 'waiting_user'])
def test_lost_create_response_and_observation_reuse_original_task_without_oracle(pilot, tmp_path, monkeypatch, status):
    config, case, envelope = pilot
    results, posts = {}, []
    trial = envelope['manifest']['trials'][0]
    state = {'task_id': 'task-one', 'status': status, 'objective': case['objective'], 'spec_revision': 1,
        'state_version': 3, 'input_refs': ['ref-source'], 'blockers': [],
        'config_snapshot': {'model_profile': 'small', 'harness_mode': 'H0', 'model_identity': {'digest': 'small-digest'}}}
    monkeypatch.setattr(evaluation, 'request_result', lambda _, key: results.get(key))
    monkeypatch.setattr(evaluation.httpx, 'get', lambda *a, **k: SimpleNamespace(json=lambda: {'models': [{'name': 'small', 'digest': 'small-digest'}]}))
    monkeypatch.setattr(evaluation, 'trial_snapshot', lambda *a: {'state': state, 'artifacts': [], 'jobs': [],
        'events': [], 'contexts': [], 'approvals': [{'status': 'pending'}] if status == 'waiting_user' else []})
    ticks = iter(range(100))
    monkeypatch.setattr(evaluation.time, 'monotonic', lambda: next(ticks)*5)

    class Client:
        def request(self, method, path, payload=None):
            if path == '/runtime':
                return envelope['manifest']['runtime']
            if method == 'GET':
                return [] if path == '/tasks' else state
            posts.append((path, payload))
            if path == '/resources/register':
                results[payload['request_id']] = {'resource_id': 'ref-source'}
                return results[payload['request_id']]
            results[payload['request_id']] = {'task_id': 'task-one'}
            raise httpx.ReadTimeout('server committed; response lost')

    client = Client()
    with pytest.raises(httpx.ReadTimeout):
        evaluation.run_trial(envelope, trial['trial_id'], tmp_path/'trials', config, timeout=1, client=client)
    record = evaluation.run_trial(envelope, trial['trial_id'], tmp_path/'trials', config, timeout=1, client=client)
    assert record['task_id'] == 'task-one'
    assert len(posts) == 2  # One source grant, one committed create; no replayed effects.
    assert posts[0][1]['path'] != case['oracle']['path']
    assert record['observation'] == ('live' if status == 'active' else 'attention_or_terminal')
    assert record['interaction_required'] == (status == 'waiting_user')
    assert not record['accepted']


def test_runtime_is_authenticated_startup_snapshot(app, monkeypatch):
    with TestClient(app, base_url='http://127.0.0.1') as browser:
        assert browser.get('/runtime').status_code == 403
        browser.get('/session')
        original = browser.get('/runtime').json()
        monkeypatch.setattr('forestry_agent.app.runtime_manifest', lambda _: pytest.fail('must not re-hash edited code'))
        assert browser.get('/runtime').json() == original
        assert 'config_sha256' in original and 'source' in original


@pytest.mark.parametrize('automatic_passed', [True, False])
def test_semantic_review_is_version_bound_and_cannot_override_failed_calculation(pilot, tmp_path, monkeypatch, automatic_passed):
    config, case, envelope = pilot
    trial = envelope['manifest']['trials'][0]
    snapshot, contents = score_data(case)
    snapshot['state'].update(state_version=7, status='waiting_user', blockers=[{'code': 'ACCEPTANCE_REQUIRED'}])
    record = {'manifest_sha256': envelope['manifest_sha256'], 'trial': trial, 'task_id': 'task-one',
        'snapshot': snapshot, 'score': {'automatic_checks_passed': automatic_passed}, 'interaction_required': False}
    directory = tmp_path/'trials'
    path = directory/f'{trial["trial_id"]}.json'
    evaluation.write_json(path, record)
    report = snapshot['artifacts'][1]
    review = {'manifest_sha256': envelope['manifest_sha256'], 'trial_id': trial['trial_id'], 'task_id': 'task-one',
        'report_artifact_id': report['artifact_id'], 'report_sha256': report['sha256'], 'reviewer': 'independent fixture reviewer',
        'criteria': {criterion: 'passed' for criterion in ['result_consistency', 'provenance_claims',
            'language_and_limitations', 'failure_history', 'execution_boundaries']}, 'observations': ['Reviewed exact report and actual journal.']}
    calls = []

    class Client:
        def request(self, method, endpoint):
            calls.append((method, endpoint))
            return snapshot['state']

    monkeypatch.setattr(evaluation, 'artifact_bytes', lambda _, artifact: contents[artifact['artifact_id']])
    result = evaluation.review_trial(envelope, review, directory, config, client=Client())
    assert result['score']['full_task_passed'] is automatic_passed
    assert result['auto_accepted'] is False and calls == [('GET', '/tasks/task-one')]
    review['report_sha256'] = '0'*64
    with pytest.raises(Failure, match='报告版本'):
        evaluation.review_trial(envelope, review, directory, config, client=Client())
    assert len(calls) == 1
