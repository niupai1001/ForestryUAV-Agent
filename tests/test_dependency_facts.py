"""Receipt/native-context protocol fixtures, not execution/scientific acceptance."""
import copy
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel
from test_acquisition import WHEEL, ReleaseFixture, acquire, public_task
from test_network import resolve as approve
from test_resolution import IndexFixture, prepared_target, run_resolution
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps
from forestry_agent.jobs import Jobs
from forestry_agent.store import encode, uid


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
