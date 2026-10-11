"""Source acquisition authority/protocol only; fixture bytes are never executed."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from test_acquisition import ReleaseFixture, public_task
from test_core import task
from test_jobs import ExecutorFixture
from test_network import resolve
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.progress import observation
from forestry_agent.roles import AssignmentRequest

SOURCE = {'name':'six', 'version':'1.17.0', 'filename':'six-1.17.0.tar.gz'}


class SourceFixture(ReleaseFixture):
    def __init__(self):
        super().__init__()
        self.entry.update(filename=SOURCE['filename'], packagetype='sdist',
            url='https://files.pythonhosted.org/packages/ab/'+SOURCE['filename'])


def acquire(engine, state, call='source-download', approval=None, sources=None, lock=None):
    deps = Deps(engine, state['task_id'], 'source-acquisition', 1)
    payload = {'source_packages':sources or [SOURCE], 'source':'pypi', 'approval_id':approval}
    return engine.perform(deps, call, 'environment.acquire', payload, lambda action:
        engine.acquisition.acquire(deps, action, [], approval_id=approval, source_packages=sources or [SOURCE],
            resolution_lock_artifact_id=lock))


def test_source_approval_is_exact_typed_replayed_and_separate_from_wheel_lock(client, app):
    state, _ = task(client, objective='PRIVATE_SOURCE_MARKER')
    engine, fake = app.state.engine, SourceFixture()
    fake.data = b'PK\x00\x80source protocol fixture never extracted'
    fake.entry.update(size=len(fake.data),digests={'sha256':hashlib.sha256(fake.data).hexdigest()})
    engine.acquisition.http = fake
    proposal = acquire(engine, state)
    assert proposal['kind'] == 'approval_required' and not fake.calls
    grant = engine.network.list(state['task_id'])[0]
    payload = json.loads(grant['request']['value'])
    assert payload == {'wheels':[], 'source':'pypi', 'source_packages':[SOURCE]}
    assert 'PRIVATE_SOURCE_MARKER' not in json.dumps(grant['request'])
    assert grant['request']['package_execution'] is False
    resolve(client, state, grant).raise_for_status()
    denied = acquire(engine, state, 'changed-source', grant['approval_id'], [{**SOURCE,'filename':'six-1.17.0.zip'}])
    assert denied['error']['code'] == 'VERSION_CONFLICT' and not fake.calls
    result = acquire(engine, state, 'approved-source', grant['approval_id'])
    assert result['kind'] == 'completed' and len(fake.calls) == 2
    assert result['wheels'] == [] and result['wheel_artifact_ids'] == [] and len(result['source_artifact_ids']) == 1
    assert acquire(engine, state, 'approved-source', grant['approval_id']) == result and len(fake.calls) == 2
    reused = acquire(engine,state,'reuse-source')
    assert reused['source_artifact_ids'] == result['source_artifact_ids'] and reused['reused_from_action_id'] == result['action_id']
    assert len(fake.calls) == 2 and len(engine.network.list(state['task_id'])) == 1
    ref = result['source_artifact_ids'][0]
    with pytest.raises(Failure) as error:
        engine.resources.read_artifact(state['task_id'],ref)
    assert error.value.code == 'PRECONDITION_MISSING'
    assert 'source_artifact_ids' in str(error.value) and ref in str(error.value)
    assert 'frontend_requirements' in str(error.value)
    lock = json.loads(engine.resources.read_artifact(state['task_id'], result['lock_artifact_id'])['text'])
    assert lock['kind'] == 'public_package_acquisition' and lock['source_packages'] == result['source_packages']
    denied = acquire(engine, state, 'source-with-wheel-lock', lock='foreign-wheel-lock')
    assert denied['error']['code'] == 'VERSION_CONFLICT' and len(fake.calls) == 2


def test_source_download_receipt_remains_visible_after_wheel_download(client,app):
    from test_acquisition import acquire as acquire_wheel
    state = public_task(client)
    engine = app.state.engine
    engine.acquisition.http = SourceFixture()
    source = acquire(engine,state)
    engine.acquisition.http = ReleaseFixture()
    downloaded = acquire_wheel(engine,state,'wheel-after-source')
    receipts = engine.acquisition.confirmed_receipts(state['task_id'],1)
    assert len(receipts) == 2 and all(r['operation']=='download' for r in receipts)
    source_receipt = next(r for r in receipts if r.get('source_packages'))
    wheel_receipt = next(r for r in receipts if r.get('wheels'))
    assert source_receipt['source_action_id']==source['action_id']
    assert source_receipt['source_packages'][0]['artifact_id']==source['source_artifact_ids'][0]
    assert source_receipt['lock_artifact_id']==source['lock_artifact_id']
    assert wheel_receipt['source_action_id']==downloaded['action_id']
    assert wheel_receipt['lock_artifact_id']==downloaded['lock_artifact_id']


@pytest.mark.parametrize('condition', ['wrong_type','wrong_sha','wrong_size','yanked','private_url','query','unsafe_name','wheel_in_source'])
def test_bad_source_metadata_or_file_cannot_become_build_input(client, app, condition):
    state = public_task(client)
    engine, fake = app.state.engine, SourceFixture()
    engine.acquisition.http = fake
    sources = [SOURCE]
    if condition == 'wrong_type':fake.entry['packagetype'] = 'bdist_wheel'
    elif condition == 'wrong_sha':fake.entry['digests']['sha256'] = '0'*64
    elif condition == 'wrong_size':fake.entry['size'] += 1
    elif condition == 'yanked':fake.entry['yanked'] = True
    elif condition == 'private_url':fake.entry['url'] = 'https://127.0.0.1/packages/'+SOURCE['filename']
    elif condition == 'query':fake.entry['url'] += '?credential=x'
    elif condition == 'unsafe_name':sources = [{**SOURCE,'filename':'../six-1.17.0.tar.gz'}]
    elif condition == 'wheel_in_source':sources = [{**SOURCE,'filename':'six-1.17.0-py3-none-any.whl'}]
    result = acquire(engine, state, sources=sources)
    assert result['kind'] == 'failed'
    with engine.store.transaction() as db:
        assert not db.execute('SELECT 1 FROM jobs').fetchone()
        assert not any(json.loads(r[0])['name'].endswith('.tar.gz') for r in db.execute('SELECT payload FROM artifacts'))


def test_acquired_source_requires_execution_and_cannot_masquerade_as_wheel(client, app):
    state = public_task(client, constraints={'allowed_capabilities':['environment.acquire','environment.prepare']})
    engine = app.state.engine
    engine.acquisition.http = SourceFixture()
    acquired = acquire(engine, state)
    ref = acquired['source_artifact_ids'][0]
    assert Path(engine.environments.acquired_source(state['task_id'], 1, ref)['locator']).read_bytes() == engine.acquisition.http.data
    with pytest.raises(Failure, match='允许范围'):
        engine.perform(Deps(engine,state['task_id'],'source-acquisition',1),'no-execution','environment.prepare',
            {'source_artifact_ids':[ref]},lambda _:pytest.fail('Source build without execution permission'))
    with pytest.raises(Failure, match='允许范围'):
        engine.environments.prepare(state['task_id'],1,'direct',[],source_artifact_ids=[ref])
    with pytest.raises(Failure, match='获取动作'):
        engine.environments.acquired_wheel(state['task_id'],1,ref)
    with pytest.raises(Failure, match='当前修订'):
        engine.environments.acquired_source(state['task_id'],2,ref)


def test_source_job_revalidates_acquisition_producer_hash_and_uses_readonly_mount(client, app):
    state = public_task(client)
    engine = app.state.engine
    engine.acquisition.http = SourceFixture()
    acquired = acquire(engine,state)
    ref = acquired['source_artifact_ids'][0]
    deps = Deps(engine,state['task_id'],'source-acquisition',1)
    reply = engine.perform(deps,'prepare-source','environment.prepare',{'source_artifact_ids':[ref]},lambda action:
        engine.environments.prepare(state['task_id'],1,action,[],source_artifact_ids=[ref]))
    fake = ExecutorFixture()
    engine.jobs.docker = fake
    engine.jobs.tick()
    job = engine.jobs.read(reply['job']['job_id'])
    assert job['status'] == 'running' and job['source_recipe'][0]['source_kind'] == 'acquired_artifact'
    assert job['mounts'][0]['artifact_id'] == ref
    create = next(c for c in fake.commands if c[0]=='create')
    assert any(f'dst=/inputs/{ref},readonly' in field for field in create)
    assert '/build:rw,exec,nosuid,nodev,mode=1777,size=1073741824' in create
    forged = engine.resources.write(state['task_id'],'forged',SOURCE['filename'],'not acquired')
    with pytest.raises(Failure, match='获取动作'):
        engine.environments.acquired_source(state['task_id'],1,forged['artifact_id'])
    Path(engine.resources.artifact(ref)['locator']).write_bytes(b'tampered')
    with pytest.raises(Failure,match='校验和'):
        engine.jobs.may_start(job)


def test_source_receipt_summary_and_repeat_download_progress_keep_real_source_facts(client, app):
    state = public_task(client)
    engine = app.state.engine
    engine.acquisition.http = SourceFixture()
    first = acquire(engine,state)
    calls = list(engine.acquisition.http.calls)
    second = acquire(engine,state,'same-bytes-again')
    assert first['source_artifact_ids'] == second['source_artifact_ids']
    assert second['reused_from_action_id'] == first['action_id']
    assert engine.acquisition.http.calls == calls
    assert observation('acquire_dependencies',first) == observation('acquire_dependencies',second)
    summary = engine.acquisition.confirmed_receipts(state['task_id'],1)[0]
    assert summary['wheels']==[] and summary['source_packages']==[{**SOURCE,'artifact_id':second['source_artifact_ids'][0]}]
    assert summary['source_action_id']==first['action_id']


def test_source_artifact_does_not_escape_role_intersection(client, app):
    state = public_task(client)
    engine = app.state.engine
    engine.acquisition.http = SourceFixture()
    ref = acquire(engine,state)['source_artifact_ids'][0]
    deps = Deps(engine,state['task_id'],'source-acquisition',1)
    handle = engine.perform(deps,'delegate-scope','role.delegate',{},lambda action:
        engine.roles.prepare(deps,action,'code_experiment',AssignmentRequest(objective='source scope')))
    value = engine.roles.start(engine.roles.read(handle['assignment_id']))
    child = Deps(engine,state['task_id'],value['run_id'],1,assignment_id=value['assignment_id'])
    with engine.store.transaction() as db, pytest.raises(Failure,match='委派产物范围'):
        engine.roles.authorize(db,child,'environment.prepare',{'source_artifact_ids':[ref]})


@pytest.mark.asyncio
async def test_native_source_acquisition_schema_and_journal_use_same_tool(client, app):
    state = public_task(client,constraints={'allowed_capabilities':['environment.acquire']})
    engine = app.state.engine
    engine.acquisition.http = SourceFixture()
    turn_id='native-source-acquisition'
    agent, connection = engine.make_agent('small',turn_id,state['config_snapshot'],state['task_id'])
    def model(messages, info):
        returns=[p.content for m in messages if isinstance(m,ModelRequest) for p in m.parts
            if isinstance(p,ToolReturnPart) and p.tool_name=='acquire_dependencies']
        if not returns:
            return ModelResponse(parts=[ToolCallPart('acquire_dependencies',{'source_packages':[SOURCE]},'source')])
        result=returns[-1]
        assert result['kind']=='completed' and result['wheel_artifact_ids']==[]
        return ModelResponse(parts=[ToolCallPart('final_result',{'summary':'只下载未执行源码','artifact_ids':result['source_artifact_ids']},'done')])
    try:
        with agent.override(model=FixtureFunctionModel(model,stream_function=stream_adapter(model))):
            result=await agent.run('取得公开源码但不执行',deps=Deps(engine,state['task_id'],turn_id,1))
        assert len(result.output.artifact_ids)==1
    finally:
        await connection.aclose()
