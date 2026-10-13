"""Targeted review regressions; no external model, Docker or scientific computation."""
import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from test_core import task
from test_delivery import ready
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.lidar_products import automatic_inputs, product_set
from forestry_agent.provenance import source_manifest
from forestry_agent.roles import AssignmentRequest
from forestry_agent.store import encode, now, uid

pytestmark = pytest.mark.offline_web


def upload(client, *, name='样地.csv', request_id='upload-one', data=b'x,y\n1,2\n'):
    return client.post('/uploads', params={'filename': name, 'draft_id': 'draft-A', 'request_id': request_id},
                       content=data)


def test_upload_owned_by_conversation_and_removal_revokes_future_reads(client, app):
    first = upload(client)
    assert first.status_code == 201, first.text
    resource = first.json()
    ref = resource['resource_id']
    assert upload(client).json() == resource
    assert upload(client, data=b'changed').status_code == 409
    assert upload(client, name='../escape.csv', request_id='escape').status_code == 400
    state, _ = task(client, input_refs=[ref])
    other = client.post('/tasks', json={'request_id': 'task-B', 'objective': 'B', 'input_refs': [ref]})
    assert other.status_code == 403
    assert [r['resource_id'] for r in client.get(f"/tasks/{state['task_id']}/sources").json()] == [ref]
    old_bytes = Path(resource['locator']).read_bytes()
    result = client.post(f"/tasks/{state['task_id']}/sources", json={
        'request_id': 'detach', 'expected_spec_revision': 1, 'remove_refs': [ref]})
    assert result.status_code == 200, result.text
    assert result.json()['input_refs'] == []
    with pytest.raises(Failure, match='输入范围'):
        app.state.resources.resolve(ref, task_id=state['task_id'])
    assert Path(resource['locator']).read_bytes() == old_bytes
    assert client.get(f"/tasks/{state['task_id']}/sources").json() == []


def test_sealed_conversation_continues_and_each_delivery_stays_immutable(client, app):
    task_id, _, _, payload = ready(client, app)
    first = client.post(f'/tasks/{task_id}/acceptance', json=payload).json()
    manifest = app.state.resources.artifact(first['delivery']['manifest_artifact_id'])
    frozen = Path(manifest['locator']).read_bytes()
    request = {'request_id': 'after-seal', 'expected_spec_revision': 1, 'text': '继续解释报告'}
    continued = client.post(f'/tasks/{task_id}/messages', json=request)
    assert continued.status_code == 200, continued.text
    assert continued.json()['spec_revision'] == 2 and continued.json()['status'] == 'created'
    assert continued.json()['delivery_history'] == [first['delivery']]
    assert client.post(f'/tasks/{task_id}/messages', json=request).json() == continued.json()
    assert continued.json()['model_requests'] == first['model_requests']
    def finish(s, db):
        db.execute("UPDATE turns SET status='completed' WHERE task_id=?", (task_id,))
        s['status'] = 'waiting_user'
        s['messages'].append({'id': 'second', 'role': 'assistant', 'text': '后续说明', 'artifact_ids': [],
                              'limitations': [], 'turn_id': 'second-turn', 'spec_revision': 2, 'at': now()})
    app.state.store.change(task_id, 'fixture.second', finish)
    candidate = app.state.delivery.candidate(task_id)
    second = client.post(f'/tasks/{task_id}/acceptance', json={**payload, 'request_id': 'accept-second',
        'expected_spec_revision': 2, 'expected_state_version': candidate['state_version'],
        'candidate_sha256': candidate['candidate_sha256']})
    assert second.status_code == 200, second.text
    assert second.json()['delivery']['delivery_id'] != first['delivery']['delivery_id']
    assert Path(manifest['locator']).read_bytes() == frozen
    assert client.post(f'/tasks/{task_id}/acceptance', json=payload).json() == first


@pytest.mark.asyncio
async def test_plain_answer_no_acceptance_blocker_and_followup_keeps_history_after_sealing(client, app, monkeypatch):
    state, _ = task(client, objective='请记住软件夹具暗号 ZEBRA42')
    engine = app.state.engine
    calls = []
    def model(messages, info):
        calls.append(messages)
        assert not {'search_web', 'read_web_page'} & {t.name for t in info.function_tools}
        return ModelResponse(parts=[ToolCallPart('final_result', {'summary': 'ZEBRA42', 'artifact_ids': []}, uid())])
    fixture = FixtureFunctionModel(model, stream_function=stream_adapter(model))
    original = engine.make_agent
    def make(*args, **kwargs):
        agent, connection = original(*args, **kwargs)
        agent.model = fixture
        return agent, connection
    monkeypatch.setattr(engine, 'make_agent', make)
    turn = engine.submit(state['task_id'], state['objective'])
    await engine.queue[state['task_id']]
    result = engine.store.task(state['task_id'])
    assert result['status'] == 'waiting_user' and result['blockers'] == []
    assert result['acceptance']['file_checks'] == 'not_applicable'
    candidate = app.state.delivery.candidate(state['task_id'])
    app.state.delivery.accept(state['task_id'], {'request_id': 'seal-answer', 'expected_state_version': candidate['state_version'],
        'expected_spec_revision': 1, 'candidate_sha256': candidate['candidate_sha256'], 'verdict': 'accept', 'comment': ''})
    reply = client.post(f"/tasks/{state['task_id']}/messages", json={
        'request_id': 'recall', 'expected_spec_revision': 1, 'text': '刚才暗号是什么？'})
    assert reply.json()['history_bridge_turn_id'] == turn
    engine.start_queued(state['task_id'])
    await engine.queue[state['task_id']]
    assert len(calls) == 2
    assert '请记住软件夹具暗号 ZEBRA42' in str(calls[1])
    assert engine.store.task(state['task_id'])['status'] == 'waiting_user'


@pytest.mark.asyncio
async def test_attachment_change_during_live_turn_cannot_pause_new_turn(client, app, monkeypatch):
    state, _ = task(client)
    engine = app.state.engine
    entered, release = asyncio.Event(), asyncio.Event()
    original = engine.make_agent
    def model(messages, info):
        return ModelResponse(parts=[ToolCallPart('final_result', {'summary': '新会话修订回复', 'artifact_ids': []}, uid())])
    fixture = FixtureFunctionModel(model, stream_function=stream_adapter(model))
    def make(*args, **kwargs):
        agent, connection = original(*args, **kwargs)
        agent.model = fixture
        original_run = agent.run
        async def delayed(*a, **kw):
            if kw.get('deps').revision == 1:
                entered.set()
                await release.wait()
                raise Failure('EXECUTION_STOPPED', '旧附件修订已停止', 409)
            return await original_run(*a, **kw)
        agent.run = delayed
        return agent, connection
    monkeypatch.setattr(engine, 'make_agent', make)
    engine.submit(state['task_id'], state['objective'])
    old = engine.queue[state['task_id']]
    await entered.wait()
    ref = upload(client).json()['resource_id']
    changed = client.post(f"/tasks/{state['task_id']}/sources", json={
        'request_id': 'add-live', 'expected_spec_revision': 1, 'add_refs': [ref]}).json()
    assert changed['attachment_pause'] and changed['status'] == 'paused'
    response = client.post(f"/tasks/{state['task_id']}/messages", json={
        'request_id': 'continue-live', 'expected_spec_revision': 2, 'text': '处理新附件'})
    assert response.json()['status'] in {'created', 'active'}
    release.set()
    await old
    await asyncio.sleep(0)
    await engine.queue[state['task_id']]
    result = engine.store.task(state['task_id'])
    assert result['status'] == 'waiting_user', result
    assert result['spec_revision'] == 2 and result['messages'][-1]['text'] == '新会话修订回复'


def test_disabled_search_rejects_public_context_and_legacy_network_grants(client, app):
    state, _ = task(client, context_classification='public')
    deps = Deps(app.state.engine, state['task_id'], 'disabled-web', 1)
    for kind in ('web.search', 'web.read'):
        with pytest.raises(Failure, match='暂缓'):
            app.state.engine.perform(deps, kind, kind, {}, lambda _: pytest.fail('must not execute'))
        with pytest.raises(Failure, match='暂缓'):
            app.state.engine.network.execute(deps, 'action', kind, 'https://example.org')
    assert app.state.engine.config.search_configuration() == {}
    assert client.get('/capabilities').json()['web']['enabled'] is False


def test_scientific_artifact_mount_is_same_task_readonly_checked_and_role_scoped(client, app):
    engine = app.state.engine
    engine.config.science_image = 'sha256:' + 'a' * 64
    state, _ = task(client)
    own = engine.resources.write(state['task_id'], 'fixture-output', 'chm.tif', 'immutable fixture')
    deps = Deps(engine, state['task_id'], 'scientific-ref', 1)
    args = {'method_id': 'lidar.p2r_lmf_dalponte.v1', 'input_refs': [own['artifact_id']]}
    result = engine.perform(deps, 'submit', 'code.execute', args, lambda action: engine.jobs.submit(
        state['task_id'], 1, action, 'print("mount contract fixture")', [own['artifact_id']], runtime_profile='scientific'))
    assert result['kind'] == 'completed'
    job = engine.jobs.read(result['job']['job_id'])
    assert job['mounts'][0]['artifact_id'] == own['artifact_id']
    assert job['mounts'][0]['sha256'] == own['sha256']
    engine.jobs.verify_inputs(job)
    assigned = engine.perform(deps, 'role-fixture', 'role.delegate', {}, lambda action:
        engine.roles.prepare(deps, action, 'code_experiment', AssignmentRequest(objective='fixture')))
    value = engine.roles.read(assigned['assignment_id'], state['task_id'])
    with engine.store.transaction() as db:
        value['status'] = 'running'
        engine.roles.save(db, value)
    child = Deps(engine, state['task_id'], value['run_id'], 1, assignment_id=value['assignment_id'])
    with pytest.raises(Failure, match='委派范围'):
        engine.perform(child, 'denied', 'code.execute', args, lambda _: {})
    other = client.post('/tasks', json={'request_id': 'different-owner', 'objective': 'B'}).json()
    foreign = engine.resources.write(other['task_id'], 'output-B', 'foreign.tif', 'B')
    with pytest.raises(Failure, match='本会话'):
        engine.resources.resolve_input(foreign['artifact_id'], state['task_id'])
    Path(own['locator']).write_text('tampered', encoding='utf-8')
    with pytest.raises(Failure, match='校验和'):
        engine.jobs.verify_inputs(job)


def scientific_batch(engine, state, stage, output_names, input_sha):
    request = {'task_id': state['task_id'], 'method_id': 'lidar.p2r_lmf_dalponte.v1', 'stage': stage,
               'science_image': state['config_snapshot']['science_image'], 'parameters': {'fixture': True}}
    action = uid()
    deps = Deps(engine, state['task_id'], 'product-fixture', 1)
    receipt = engine.perform(deps, action, 'code.execute', {'method_id': request['method_id'], 'input_refs': []},
        lambda a: engine.jobs.submit(state['task_id'], 1, a, 'REQUEST = ' + repr(request) + '\nprint("fixture")', [], runtime_profile='scientific'))
    job = engine.jobs.read(receipt['job']['job_id'])
    files = {n: engine.resources.write(state['task_id'], job['action_id'], n, n) for n in output_names}
    data = {**request, 'input_sha256': input_sha, 'source_sha256': 'd'*64, 'status': 'partial',
            'outputs': {n: {'sha256': a['sha256']} for n, a in files.items()}}
    manifest = engine.resources.write(state['task_id'], job['action_id'], 'stage-result.json', encode(data))
    files['stage-result.json'] = manifest
    job.update(status='succeeded', artifact_ids=[a['artifact_id'] for a in files.values()])
    engine.jobs.save(job, 'fixture.metadata_only')
    return files


def test_product_selection_follows_manifest_not_latest_filenames(client, app):
    engine = app.state.engine
    engine.config.science_image = 'sha256:' + 'a' * 64
    state, _ = task(client)
    chm = scientific_batch(engine, state, 'chm', ['chm-p2r.tif', 'canopy-support.tif'],
                           {r: 'b'*64 for r in ('working', 'terrain_quality', 'terrain_support')})
    seeds = scientific_batch(engine, state, 'seeds', ['accepted-seeds.gpkg', 'tree-id-mapping.csv'],
                             {'chm': chm['chm-p2r.tif']['sha256'], 'chm_record': chm['stage-result.json']['sha256']})
    inputs = {r: 'c'*64 for r in ('working', 'original', 'hag', 'chm', 'seeds')}
    inputs.update(chm_record=chm['stage-result.json']['sha256'], seed_record=seeds['stage-result.json']['sha256'])
    instances = scientific_batch(engine, state, 'instances', ['segmented.laz', 'instance-quality.json', 'trees.csv', 'crown-labels.tif'], inputs)
    decoy = engine.resources.write(state['task_id'], 'general-write', 'trees.csv', 'wrong later table')
    engine.resources.write(state['task_id'], 'general-write', 'stage-result.json', '{}')
    selected = product_set(engine, state['task_id'])
    assert selected['product_set_id'] == instances['stage-result.json']['artifact_id']
    assert next(a for a in selected['files'] if a['name'] == 'trees.csv')['artifact_id'] != decoy['artifact_id']
    assert automatic_inputs(engine, state['task_id'], 'seeds')['chm'] == chm['chm-p2r.tif']['artifact_id']


def test_r_source_and_method_recipe_are_in_runtime_identity(tmp_path):
    r = tmp_path / 'algorithm.R'
    r.write_text('x <- 1', encoding='utf-8')
    old = source_manifest(tmp_path)
    assert 'algorithm.R' in old['files']
    r.write_text('x <- 2', encoding='utf-8')
    assert source_manifest(tmp_path)['sha256'] != old['sha256']
    assert 'config/methods/lidar-lidr-canopy.yaml' in source_manifest()['files']


def test_malformed_scientific_record_returns_explicit_failure(client, app):
    engine = app.state.engine
    engine.config.science_image = 'sha256:' + 'a' * 64
    state, _ = task(client)
    files = scientific_batch(engine, state, 'instances', ['segmented.laz', 'instance-quality.json', 'trees.csv', 'crown-labels.tif'], {})
    assert client.get(f"/tasks/{state['task_id']}/lidar/products").status_code == 422
    old = files['stage-result.json']
    import hashlib
    import json
    Path(old['locator']).write_bytes(b'[]')
    with engine.store.transaction() as db:
        old.update(sha256=hashlib.sha256(b'[]').hexdigest(), size_bytes=2)
        db.execute('UPDATE artifacts SET payload=? WHERE id=?', (json.dumps(old), old['artifact_id']))
    response = client.get(f"/tasks/{state['task_id']}/lidar/products")
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'VERIFICATION_FAILED'


@pytest.mark.asyncio
async def test_doctor_does_not_probe_deferred_web_or_count_it_as_prerequisite(app, monkeypatch):
    import httpx

    import forestry_agent.doctor as module
    original = httpx.AsyncClient
    def respond(request):
        return httpx.Response(200, json={'models': [{'name': 'qwen3.5:4b'}]})
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(module.Docker, 'call', lambda *a: {'code': 0, 'stdout': '29.1.3', 'stderr': ''})
    monkeypatch.setattr(module.PublicHTTP, 'get', lambda *a, **kw: pytest.fail('deferred search must never contact DNS/HTTP'))
    report = await module.doctor(app.state.engine.config)
    assert report['capabilities']['search']['status'] == 'disabled'
    assert 'search' not in report['general_prerequisite_blockers']


def test_disabled_web_revokes_pending_grants_without_queueing_and_roi_stays_experimental(client, app):
    engine = app.state.engine
    engine.config.science_image = 'sha256:' + 'a'*64
    state, _ = task(client)
    grant = {'approval_id': 'legacy-web', 'task_id': state['task_id'], 'spec_revision': 1,
             'status': 'approved', 'request': {'kind': 'web.read'}, 'request_sha256': 'b'*64}
    with engine.store.transaction() as db:
        db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,NULL)',
                   (grant['approval_id'], state['task_id'], 1, 'approved', encode(grant)))
    engine.network.disable_web_grants()
    assert engine.network.list(state['task_id'])[0]['status'] == 'revoked'
    with engine.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM turns WHERE task_id=?', (state['task_id'],)).fetchone()[0] == 0
    reply = client.post(f"/tasks/{state['task_id']}/lidar/roi", json={'request_id': 'experimental-roi',
        'expected_spec_revision': 1, 'input_refs': {r: 'fixture' for r in
            ['segmented','chm','trees','vectors','quality','instance_record','roi']}})
    assert reply.status_code == 403 and reply.json()['error']['code'] == 'CAPABILITY_DISABLED'
    amended = client.post(f"/tasks/{state['task_id']}/amendments", json={'request_id':'roi-amend',
        'expected_spec_revision':1, 'objective':'fixture', 'input_refs':[], 'requested_method_id':'lidar.roi_clip.v1'})
    assert amended.status_code == 422
