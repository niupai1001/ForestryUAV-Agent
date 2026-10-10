"""Receipt/native-context protocol fixtures, not execution/scientific acceptance."""
import copy
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel
from test_acquisition import WHEEL, ReleaseFixture, acquire, public_task
from test_build_inspection import inspection_fixture
from test_network import resolve as approve
from test_resolution import IndexFixture, prepared_target, run_resolution
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.jobs import Jobs
from forestry_agent.store import encode, uid


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_source',[False,True])
async def test_observed_inspection_requirements_survive_new_native_turn_only_while_source_matches(client,app,tmp_path,changed_source):
    source,state,jobs,_,job_id = inspection_fixture(client,app,tmp_path)
    engine = app.state.engine
    deps = Deps(engine,state['task_id'],'observe-inspection',1)
    observed = engine.perform(deps,'observe','job.observe',{'job_id':job_id},lambda _:jobs.observation(job_id,state['task_id']))
    assert observed['kind'] == 'completed' and observed['build_requirements']['inspection'] == 'static'
    if changed_source:
        source.write_bytes(b'changed after inspection')
    turn = 'native-inspection-handoff'
    agent,connection = engine.make_agent('small',turn,state['config_snapshot'],state['task_id'])
    try:
        with agent.override(model=TestModel(call_tools=[],custom_output_args={'summary':'检查来源衔接','artifact_ids':[],'limitations':[]})):
            await agent.run('从当前成功检查继续',deps=Deps(engine,state['task_id'],turn,1))
        with engine.store.transaction() as db:
            manifest = json.loads(db.execute('SELECT payload FROM contexts WHERE turn_id=?',(turn,)).fetchone()[0])
        body = json.loads(Path(manifest['prepared_input_ref']).read_text(encoding='utf-8'))
        instructions = '\n'.join(body['instructions'])
        if changed_source:
            assert '"build_requirements":' not in instructions
        else:
            assert '"requirements":["setuptools>=40.8.0"]' in instructions
            assert '"frontend_requirements":["pyproject-hooks==1.2.0","packaging==25.0"]' in instructions
            assert observed['action_id'] in instructions
            assert observed['build_requirements']['artifact_id'] in instructions
    finally:
        await connection.aclose()


@pytest.mark.parametrize('phase', ['resolve', 'download'])
def test_dependency_approval_identifies_exact_tool_through_decision_and_receipt(client, app, phase):
    from test_core import task
    engine = app.state.engine
    state = task(client)[0]
    if phase == 'resolve':
        deps, target = prepared_target(engine, state)
        fake = IndexFixture(); fake.add('six', '1.17.0')
        engine.acquisition.http = fake
        operation = lambda call, approval=None: run_resolution(engine, deps, call, target, approval=approval)
        tool = 'resolve_dependencies'
    else:
        fake = ReleaseFixture(); engine.acquisition.http = fake
        operation = lambda call, approval=None: acquire(engine, state, call, approval=approval)
        tool = 'acquire_dependencies'
    proposed = operation('phase-proposal')
    assert proposed['kind'] == 'approval_required' and proposed['requested_tool'] == tool and not fake.calls
    record = engine.network.list(state['task_id'])[0]
    assert engine.network.model_facts(state['task_id'], 1)[0]['requested_tool'] == tool
    decision = approve(client, state, record).json()
    with engine.store.transaction() as db:
        prompt = db.execute('SELECT prompt FROM turns WHERE id=?', (decision['continuation_turn_id'],)).fetchone()[0]
    resumed = json.loads(prompt.split('\n')[-1])
    assert resumed['requested_tool'] == tool and resumed['approval_id'] == record['approval_id']
    assert resumed['request_sha256'] == record['request_sha256']
    assert engine.network.model_facts(state['task_id'], 1)[0]['request'] == record['request']
    result = operation('phase-approved', record['approval_id'])
    assert result['kind'] == 'completed' and fake.calls
    fact = engine.network.model_facts(state['task_id'], 1)[0]
    assert fact['requested_tool'] == tool and fact['scope_is_summary'] and fact['consumed_action_id'] == result['action_id']


@pytest.mark.parametrize('phase', ['resolve', 'download'])
def test_failed_approved_dependency_request_requires_distinct_approval_before_retry(client, app, phase):
    from test_core import task
    engine = app.state.engine; state = task(client)[0]
    if phase == 'resolve':
        deps, target = prepared_target(engine, state)
        fake = IndexFixture(); fake.add('six', '1.17.0')
        operation = lambda call, approval=None: run_resolution(engine, deps, call, target, approval=approval)
        hook = 'after'
    else:
        fake = ReleaseFixture()
        operation = lambda call, approval=None: acquire(engine, state, call, approval=approval)
        hook = 'after_response'
    engine.acquisition.http = fake
    proposed = operation('first-proposal')
    original = engine.network.list(state['task_id'])[0]
    assert proposed['kind'] == 'approval_required' and not fake.calls
    assert approve(client, state, original, request_id='approve-first').status_code == 200
    def fail():
        raise Failure('RESOURCE_EXHAUSTED', 'metadata request deadline fixture', 422)
    setattr(fake, hook, fail)
    failed = operation('failed-approved', original['approval_id'])
    assert failed['error']['code'] == 'RESOURCE_EXHAUSTED' and len(fake.calls) == 1
    consumed = engine.network.model_facts(state['task_id'], 1)[0]
    assert consumed['status'] == 'consumed' and consumed['consumed_action_id'] == failed['action_id']
    assert engine.acquisition.confirmed_receipts(state['task_id'], 1) == []
    denied = operation('old-approval-retry', original['approval_id'])
    assert denied['error']['code'] == 'PERMISSION_DENIED' and '不带approval_id' in denied['error']['message']
    assert len(fake.calls) == 1
    retry = operation('new-attempt-without-old-approval')
    new = engine.network.list(state['task_id'])[-1]
    assert retry['kind'] == 'approval_required' and new['approval_id'] != original['approval_id']
    assert new['request_sha256'] == original['request_sha256'] and len(fake.calls) == 1
    assert approve(client, state, new, request_id='approve-new-attempt').status_code == 200
    setattr(fake, hook, lambda: None)
    completed = operation('new-approved-attempt', new['approval_id'])
    assert completed['kind'] == 'completed' and len(fake.calls) == 3
    assert engine.acquisition.confirmed_receipts(state['task_id'], 1)[0]['source_action_id'] == completed['action_id']


@pytest.mark.parametrize('phase', ['resolve', 'download'])
@pytest.mark.parametrize('approved', [False, True])
def test_expired_pending_or_approved_dependency_scope_can_request_fresh_approval(client, app, phase, approved):
    from test_core import task
    engine = app.state.engine; state = task(client)[0]
    if phase == 'resolve':
        deps, target = prepared_target(engine, state)
        fake = IndexFixture(); fake.add('six', '1.17.0')
        operation = lambda call, approval=None: run_resolution(engine, deps, call, target, approval=approval)
    else:
        fake = ReleaseFixture()
        operation = lambda call, approval=None: acquire(engine, state, call, approval=approval)
    engine.acquisition.http = fake
    operation('original-scope')
    record = engine.network.list(state['task_id'])[0]
    if approved:
        assert approve(client, state, record, request_id='original-decision').status_code == 200
        record = engine.network.list(state['task_id'])[0]
    record['expires_at'] = '2000-01-01T00:00:00+00:00'
    with engine.store.transaction() as db:
        db.execute('UPDATE approvals SET payload=? WHERE id=?', (encode(record), record['approval_id']))
    denied = operation('expired-id', record['approval_id'])
    assert denied['error']['code'] == 'PERMISSION_DENIED' and not fake.calls
    fresh = operation('fresh-scope-without-expired-id')
    old, new = engine.network.list(state['task_id'])
    assert fresh['kind'] == 'approval_required' and fresh['approval_id'] == new['approval_id'] != old['approval_id']
    assert old['status'] == 'expired' and old['consumed_action_id'] is None
    assert new['status'] == 'pending' and new['request_sha256'] == old['request_sha256'] and not fake.calls
    facts = engine.network.model_facts(state['task_id'], 1)
    assert facts[0]['scope_is_summary'] and 'scope_is_summary' not in facts[1]
    assert approve(client, state, new, request_id='fresh-decision').status_code == 200
    result = operation('fresh-approved-attempt', new['approval_id'])
    assert result['kind'] == 'completed' and len(fake.calls) == 2


def test_model_job_receipt_keeps_identities_without_mutating_complete_record():
    raw = {'kind': 'completed', 'action_id': 'producer', 'job': {'job_id': 'job', 'status': 'queued',
        'environment': {'artifact_id': 'archive', 'sha256': 'digest', 'image_id': 'actual-image',
            'lock_artifact_id': 'lock', 'manifest': {'wheels': ['large-recipe'], 'installed': ['version']}},
        'environment_recipe': ['large-preparation'], 'artifact_ids': ['product']}, 'logs': {'stderr': 'actual diagnostic'}}
    original = copy.deepcopy(raw)
    short = Jobs.model_receipt(raw)
    assert raw == original and 'manifest' not in short['job']['environment']
    assert 'environment_recipe' not in short['job']
    assert short['job']['environment'] == {k: v for k, v in raw['job']['environment'].items() if k != 'manifest'}
    assert short['logs'] == raw['logs'] and short['job']['artifact_ids'] == ['product']
    assert Jobs.model_receipt(short) == short  # Stable for new and historical replays.


def completed_dependencies(client, engine):
    state = public_task(client, request_id='dependency-facts-'+uid())
    deps, target = prepared_target(engine, state)
    fake = IndexFixture(); fake.add('six', '1.17.0', tag='py2.py3-none-any')
    engine.acquisition.http = fake
    resolved = run_resolution(engine, deps, 'resolve', target)
    engine.acquisition.http = ReleaseFixture()
    downloaded = acquire(engine, state, 'download')
    assert resolved['kind'] == downloaded['kind'] == 'completed'
    return state, resolved, downloaded


def test_dependency_facts_bound_latest_operations_and_validate_artifact_producers(client, app):
    engine = app.state.engine
    state, resolved, downloaded = completed_dependencies(client, engine)
    for n in range(4):
        downloaded = acquire(engine, state, f'new-download-{n}')
    facts = engine.acquisition.confirmed_receipts(state['task_id'], 1)
    assert len(facts) == 2
    assert facts[0]['operation'] == 'download' and facts[0]['source_action_id'] == downloaded['action_id']
    assert facts[0]['wheels'] == [{**WHEEL, 'artifact_id': downloaded['wheel_artifact_ids'][0]}]
    assert facts[1]['operation'] == 'resolve' and facts[1]['source_action_id'] == resolved['action_id']
    assert not any(k in encode(facts) for k in ['metadata_url', 'sha256', 'bounded-download-fixture-never-installed'])
    assert engine.acquisition.confirmed_receipts(state['task_id'], 2) == []
    foreign, _, other_download = completed_dependencies(client, engine)
    with engine.store.transaction() as db:
        fake = {**other_download, 'wheel_artifact_ids': downloaded['wheel_artifact_ids']}
        db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)', ('forged',state['task_id'],'old-turn','fake',1,
            'environment.acquire','{}','confirmed',encode(fake)))
    assert engine.acquisition.confirmed_receipts(state['task_id'], 1) == facts
    assert engine.acquisition.confirmed_receipts(foreign['task_id'], 1)[0]['source_action_id'] != downloaded['action_id']


def test_failed_or_incomplete_acquisition_does_not_become_completion_fact(client, app):
    engine = app.state.engine
    state, _, downloaded = completed_dependencies(client, engine)
    before = engine.acquisition.confirmed_receipts(state['task_id'], 1)
    with engine.store.transaction() as db:
        for action_id, status, receipt in [('failed', 'failed', downloaded), ('unconfirmed', 'pending', downloaded),
                ('missing-wheel', 'confirmed', {**downloaded, 'wheel_artifact_ids': ['unknown']}),
                ('duplicate-wheel', 'confirmed', {**downloaded, 'wheel_artifact_ids': downloaded['wheel_artifact_ids']*2})]:
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)', (action_id,state['task_id'],'other-turn',action_id,1,
                'environment.acquire','{}',status,encode(receipt)))
    assert engine.acquisition.confirmed_receipts(state['task_id'], 1) == before


def test_active_approval_has_full_scope_terminal_has_intent_and_consumption_provenance(client, app):
    from test_core import task
    state = task(client)[0]; engine = app.state.engine; engine.acquisition.http = ReleaseFixture()
    acquire(engine, state, 'propose')
    record = engine.network.list(state['task_id'])[0]
    pending = engine.network.model_facts(state['task_id'], 1)[0]
    assert pending['request'] == record['request'] and 'scope_is_summary' not in pending
    approve(client, state, record)
    assert engine.network.model_facts(state['task_id'], 1)[0]['request'] == record['request']
    receipt = acquire(engine, state, 'approved', approval=record['approval_id'])
    consumed = engine.network.model_facts(state['task_id'], 1)[0]
    assert consumed['status'] == 'consumed' and consumed['scope_is_summary'] is True
    assert consumed['consumed_action_id'] == receipt['action_id'] and consumed['request_sha256'] == record['request_sha256']
    assert consumed['request']['value'] == record['request']['value']
    assert consumed['request']['destinations'] == record['request']['destinations']
    assert engine.network.list(state['task_id'])[0]['request'] == record['request']
    assert engine.network.model_facts(state['task_id'], 2) == []
    denied = acquire(engine, state, 'wrong-scope', source='tuna', approval=record['approval_id'])
    assert denied['error']['code'] == 'VERSION_CONFLICT' and '不同请求' in denied['error']['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['H0', 'H1', 'H2'])
async def test_native_dependency_facts_survive_turn_boundary_without_bodies(client, app, mode):
    engine = app.state.engine
    state = public_task(client, harness_mode=mode)
    engine.acquisition.http = ReleaseFixture()
    downloaded = acquire(engine, state, 'previous-turn-download')
    turn = 'facts-next-turn-'+state['task_id']
    agent, connection = engine.make_agent('small', turn, state['config_snapshot'], state['task_id'])
    try:
        with agent.override(model=TestModel(call_tools=[], custom_output_args={'summary':'事实范围协议夹具','artifact_ids':[]})):
            await agent.run('检查先前回合完成事实', deps=Deps(engine, state['task_id'], turn, 1))
        with engine.store.transaction() as db:
            manifest = json.loads(db.execute('SELECT payload FROM contexts WHERE turn_id=? ORDER BY rowid DESC LIMIT 1',(turn,)).fetchone()[0])
        body = json.loads(Path(manifest['prepared_input_ref']).read_bytes())
        text = '\n'.join(body['instructions'])
        assert 'bounded-download-fixture-never-installed' not in text
        if mode == 'H0':
            assert 'confirmed_dependency_receipts' not in text
        else:
            note = next(t for t in body['instructions'] if t.startswith('当前权威任务事实'))
            facts,_ = json.JSONDecoder().raw_decode(note[note.index('{'):])
            assert facts['confirmed_dependency_receipts'][0]['source_action_id'] == downloaded['action_id']
            assert '不证明当前完整性/安装/任务验收' in text
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_native_target_requires_uuid_or_null_and_recovers_before_any_wrong_probe(client, app):
    from test_jobs import ExecutorFixture
    engine = app.state.engine
    state = public_task(client, constraints={'allowed_capabilities':['environment.acquire','code.execute']})
    engine.jobs.docker = ExecutorFixture()
    turn = 'nullable-target-'+state['task_id']; phase = []
    agent, connection = engine.make_agent('small', turn, state['config_snapshot'], state['task_id'])
    def model(messages, info):
        if not phase:
            schema = next(t.parameters_json_schema for t in info.function_tools if t.name=='resolve_dependencies')
            assert {'type':'string','format':'uuid'} in schema['properties']['target_artifact_id']['anyOf']
            phase.append('bad')
            return ModelResponse(parts=[ToolCallPart('resolve_dependencies',{'requirements':['six'],'target_artifact_id':'None'},'bad')])
        if len(phase)==1:
            assert any(isinstance(p,RetryPromptPart) for m in messages for p in m.parts)
            with engine.store.transaction() as db:
                assert not db.execute('SELECT 1 FROM jobs WHERE task_id=?',(state['task_id'],)).fetchone()
            phase.append('correct')
            return ModelResponse(parts=[ToolCallPart('resolve_dependencies',{'requirements':['six'],'target_artifact_id':None},'correct')])
        receipt = next(p.content for m in reversed(messages) for p in m.parts if isinstance(p,ToolReturnPart) and p.tool_name=='resolve_dependencies')
        job = engine.jobs.read(receipt['job']['job_id']);job['status']='failed';job['exit_code']=1
        engine.jobs.save(job,'job.fixture_terminal')
        return ModelResponse(parts=[ToolCallPart('final_result',{'summary':'原生参数纠正夹具','artifact_ids':[]},'done')])
    try:
        with agent.override(model=FixtureFunctionModel(model,stream_function=stream_adapter(model))):
            await agent.run('检查空值参数纠正',deps=Deps(engine,state['task_id'],turn,1))
        with engine.store.transaction() as db:
            rows=db.execute('SELECT args FROM actions WHERE task_id=?',(state['task_id'],)).fetchall()
        assert len(rows)==1 and json.loads(rows[0][0])['target_artifact_id'] is None
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_native_unknown_job_is_journalled_failure_and_loop_can_continue(client, app):
    engine = app.state.engine
    state = public_task(client,constraints={'allowed_capabilities':['job.observe']})
    turn='unknown-job-'+state['task_id'];phase=[]
    agent,connection=engine.make_agent('small',turn,state['config_snapshot'],state['task_id'])
    def model(messages,info):
        if not phase:
            phase.append('lookup')
            return ModelResponse(parts=[ToolCallPart('wait_job',{'job_id':'not-a-submitted-job'},'unknown')])
        receipt=next(p.content for m in reversed(messages) for p in m.parts if isinstance(p,ToolReturnPart) and p.tool_name=='wait_job')
        assert receipt['kind']=='failed' and receipt['error']['code']=='NOT_FOUND' and 'action_id' in receipt['error']['message']
        return ModelResponse(parts=[ToolCallPart('final_result',{'summary':'观察句柄纠正夹具','artifact_ids':[]},'done')])
    try:
        with agent.override(model=FixtureFunctionModel(model,stream_function=stream_adapter(model))):
            await agent.run('检查无效句柄的失败回执',deps=Deps(engine,state['task_id'],turn,1))
        with engine.store.transaction() as db:
            assert db.execute('SELECT status FROM actions WHERE task_id=?',(state['task_id'],)).fetchone()[0]=='failed'
            assert not db.execute('SELECT 1 FROM jobs WHERE task_id=?',(state['task_id'],)).fetchone()
    finally:
        await connection.aclose()
