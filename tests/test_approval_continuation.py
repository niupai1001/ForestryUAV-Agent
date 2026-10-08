"""Native dependency approval waits: no live network/model or source execution."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from test_core import task
from test_resolution import IndexFixture, approve, prepared_target
from test_roles import FixtureFunctionModel, stream_adapter
from test_source_acquisition import SOURCE, SourceFixture

from forestry_agent.agent import Engine
from forestry_agent.contracts import Failure
from forestry_agent.store import encode


def install_model(engine, monkeypatch, model):
    fixture=FixtureFunctionModel(model,stream_function=stream_adapter(model))
    original=engine.make_agent
    def make(*args,**kwargs):
        agent,connection=original(*args,**kwargs);agent.model=fixture
        return agent,connection
    monkeypatch.setattr(engine,'make_agent',make)


async def proposed(client,app,monkeypatch,*,mode='H1',two=False,read_sibling=False):
    refs=[]
    if read_sibling:
        source=app.state.engine.config.workspace_root.parent/'native-notes.txt'
        source.write_text('source read sibling',encoding='utf-8')
        refs.append(client.post('/resources/register',json={'request_id':'native-source','path':str(source),
            'classification':'private','read_only':True}).json()['resource_id'])
    state,_=task(client,harness_mode=mode,input_refs=refs)
    engine=app.state.engine;fake=IndexFixture()
    fake.add('six','1.17.0',tag='py2.py3-none-any')
    if two:fake.add('peppercorn','0.6',tag='py3-none-any')
    engine.acquisition.http=fake
    _,target=prepared_target(engine,state)
    requests=[]
    def model(messages,info):
        requests.append(messages)
        if len(requests)==1:
            parts=[ToolCallPart('resolve_dependencies',{'requirements':['six==1.17.0'],
                'target_artifact_id':target['artifact_id']},'native-resolve')]
            if read_sibling:parts.insert(0,ToolCallPart('read_file',{'resource_id':refs[0]},'read-sibling'))
            if two:parts.append(ToolCallPart('resolve_dependencies',{'requirements':['peppercorn==0.6'],
                'target_artifact_id':target['artifact_id']},'native-resolve-other'))
            return ModelResponse(parts=parts)
        returns=[p for m in messages if isinstance(m,ModelRequest) for p in m.parts if isinstance(p,ToolReturnPart)]
        assert any(p.tool_name=='resolve_dependencies' for p in returns)
        return ModelResponse(parts=[ToolCallPart('final_result',{'summary':'根据真实审批续执行回执返回',
            'artifact_ids':[],'limitations':['纯协议夹具']},'native-final')])
    install_model(engine,monkeypatch,model)
    engine.submit(state['task_id'],state['objective'],'native-proposal')
    await engine.queue[state['task_id']]
    assert engine.store.task(state['task_id'])['status']=='waiting_user'
    assert engine.store.task(state['task_id'])['acceptance'] is None
    assert engine.store.task(state['task_id'])['blockers'][0]['code']=='APPROVAL_REQUIRED'
    assert len(requests)==1 and fake.calls==[]
    snapshot=await engine.latest_checkpoint('native-proposal')
    assert snapshot and snapshot.state=='interrupted'
    return state,engine,fake,requests,snapshot


async def continue_grant(client,state,engine,record,verdict='approve',request_id='approved'):
    reply=await asyncio.to_thread(approve,client,state,record,verdict,request_id)
    assert reply.status_code==200,reply.text
    turn=engine.start_queued(state['task_id'])
    assert turn==reply.json()['continuation_turn_id']
    with engine.store.transaction() as db:
        prompt=db.execute('SELECT prompt FROM turns WHERE id=?',(turn,)).fetchone()[0]
    assert '尚未执行才' in prompt and '原生审批会续接原工具' in prompt
    await engine.queue[state['task_id']]
    await asyncio.sleep(0)
    return reply.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['H0','H1','H2'])
async def test_native_wait_stops_before_second_model_request_then_executes_exact_once(client,app,monkeypatch,mode):
    state,engine,fake,requests,_snapshot=await proposed(client,app,monkeypatch,mode=mode)
    record=engine.network.list(state['task_id'])[0]
    result=await continue_grant(client,state,engine,record)
    assert len(requests)==2 and len(fake.calls)>0
    calls=list(fake.calls)
    assert approve(client,state,record,request_id='approved').json()==result
    assert engine.start_queued(state['task_id']) is None and fake.calls==calls
    consumed=engine.network.list(state['task_id'])[0]
    assert consumed['status']=='consumed'
    with engine.store.transaction() as db:
        action=db.execute('SELECT * FROM actions WHERE id=?',(consumed['consumed_action_id'],)).fetchone()
    assert action['call_id']=='native-resolve' and action['turn_id']==result['continuation_turn_id']
    args=json.loads(action['args'])
    assert args['requirements']==['six==1.17.0'] and args['approval_id']==record['approval_id']
    assert json.loads(action['result'])['kind']=='completed'
    assert engine.store.task(state['task_id'])['status']=='waiting_user'
    assert engine.store.task(state['task_id'])['model_requests']==2


@pytest.mark.asyncio
@pytest.mark.parametrize('verdict',['reject','revoke'])
async def test_native_denial_closes_call_without_sending(client,app,monkeypatch,verdict):
    state,engine,fake,requests,_=await proposed(client,app,monkeypatch)
    await continue_grant(client,state,engine,engine.network.list(state['task_id'])[0],verdict)
    assert fake.calls==[] and len(requests)==2
    parts=[p for m in requests[-1] if isinstance(m,ModelRequest) for p in m.parts if isinstance(p,ToolReturnPart)]
    assert any(p.tool_call_id=='native-resolve' and p.outcome=='denied' for p in parts)


@pytest.mark.asyncio
async def test_partial_batch_approval_does_not_execute_pending_or_repeat_settled_sibling(client,app,monkeypatch):
    state,engine,fake,requests,_=await proposed(client,app,monkeypatch,two=True)
    records=engine.network.list(state['task_id'])
    await continue_grant(client,state,engine,records[0],request_id='first-approved')
    assert len(requests)==1 and engine.store.task(state['task_id'])['status']=='waiting_user'
    first=list(fake.calls);assert first==[]
    await continue_grant(client,state,engine,records[1],request_id='second-approved')
    assert len(requests)==2 and fake.calls[:len(first)]==first
    assert len([url for url in fake.calls if '/simple/six/' in url])==1
    assert all(g['status']=='consumed' for g in engine.network.list(state['task_id']))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['expiry','binding','revision'])
async def test_native_resume_never_turns_invalid_binding_into_permission(client,app,monkeypatch,fault):
    state,engine,fake,_,snapshot=await proposed(client,app,monkeypatch)
    record=engine.network.list(state['task_id'])[0]
    with engine.store.transaction() as db:
        record['status']='approved'
        if fault=='expiry':record['expires_at']=(datetime.now(UTC)-timedelta(seconds=1)).isoformat()
        if fault=='binding':record['request_sha256']='0'*64
        db.execute("UPDATE approvals SET status='approved',payload=? WHERE id=?",(encode(record),record['approval_id']))
    if fault in {'binding','revision'}:
        with pytest.raises(Failure,match='不匹配'):
            engine.approval_results(state['task_id'],2 if fault=='revision' else 1,snapshot)
    else:
        results,_=engine.approval_results(state['task_id'],1,snapshot)
        assert results.approvals['native-resolve'].kind=='tool-denied'
    assert fake.calls==[]


@pytest.mark.asyncio
async def test_native_wait_retains_settled_sibling_result_without_repeating_read(client,app,monkeypatch):
    state,engine,_fake,requests,snapshot=await proposed(client,app,monkeypatch,read_sibling=True)
    returns=[p for m in snapshot.messages if isinstance(m,ModelRequest) for p in m.parts if isinstance(p,ToolReturnPart)]
    assert len([p for p in returns if p.tool_call_id=='read-sibling'])==1
    await continue_grant(client,state,engine,engine.network.list(state['task_id'])[0])
    assert len(requests)==2
    with engine.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM actions WHERE task_id=? AND kind='fs.read'",(state['task_id'],)).fetchone()[0]==1


@pytest.mark.asyncio
async def test_native_wait_survives_new_engine_and_scope_is_rechecked_before_request(client,app,monkeypatch):
    state,engine,fake,requests,_=await proposed(client,app,monkeypatch)
    restored=Engine(engine.config,engine.store,engine.resources)
    restored.acquisition.http=fake
    restored.jobs.docker=engine.jobs.docker  # Same frozen executor identity after restart.
    restored.reconcile_startup()
    assert restored.store.task(state['task_id'])['status']=='waiting_user'
    def model(messages,info):
        requests.append(messages)
        assert engine.network.list(state['task_id'])[0]['status']=='consumed'
        return ModelResponse(parts=[ToolCallPart('final_result',{'summary':'重启后精确审批续接',
            'artifact_ids':[]},'restored-final')])
    install_model(restored,monkeypatch,model)
    await continue_grant(client,state,restored,restored.network.list(state['task_id'])[0])
    assert len(requests)==2 and len(fake.calls)>0
    assert restored.store.task(state['task_id'])['model_requests']==2


@pytest.mark.asyncio
async def test_original_readonly_source_change_after_approval_blocks_native_dispatch(client,app,monkeypatch):
    state,engine,fake,_requests,_=await proposed(client,app,monkeypatch,read_sibling=True)
    record=engine.network.list(state['task_id'])[0]
    reply=await asyncio.to_thread(approve,client,state,record)
    assert reply.status_code==200
    source=engine.config.workspace_root.parent/'native-notes.txt'
    source.write_text('changed after user approval',encoding='utf-8')
    engine.start_queued(state['task_id']);await engine.queue[state['task_id']]
    assert fake.calls==[]
    with engine.store.transaction() as db:
        result=json.loads(db.execute("SELECT result FROM actions WHERE task_id=? AND turn_id=? AND kind='environment.acquire'",
            (state['task_id'],reply.json()['continuation_turn_id'])).fetchone()[0])
    assert result['kind']=='failed' and result['error']['code']=='VERSION_CONFLICT'


@pytest.mark.asyncio
async def test_native_source_download_uses_same_model_call_and_separate_exact_approval(client,app,monkeypatch):
    state,_=task(client,constraints={'allowed_capabilities':['environment.acquire']})
    engine=app.state.engine;fake=SourceFixture();engine.acquisition.http=fake
    requests=[]
    def model(messages,info):
        requests.append(messages)
        if len(requests)==1:
            return ModelResponse(parts=[ToolCallPart('acquire_dependencies',{
                'source':'pypi','source_packages':[SOURCE]},'native-source-download')])
        result=next(p.content for m in reversed(messages) if isinstance(m,ModelRequest)
            for p in m.parts if isinstance(p,ToolReturnPart) and p.tool_name=='acquire_dependencies')
        assert result['kind']=='completed' and result['wheel_artifact_ids']==[]
        return ModelResponse(parts=[ToolCallPart('final_result',{'summary':'精确取得源码，未执行',
            'artifact_ids':result['source_artifact_ids']},'download-final')])
    install_model(engine,monkeypatch,model)
    engine.submit(state['task_id'],state['objective'],'native-source-proposal')
    await engine.queue[state['task_id']]
    assert len(requests)==1 and fake.calls==[]
    grant=engine.network.list(state['task_id'])[0]
    assert json.loads(grant['request']['value'])['source_packages']==[SOURCE]
    await continue_grant(client,state,engine,grant)
    assert len(requests)==2 and len(fake.calls)==2
    consumed=engine.network.list(state['task_id'])[0]
    with engine.store.transaction() as db:
        action=db.execute('SELECT * FROM actions WHERE id=?',(consumed['consumed_action_id'],)).fetchone()
    assert action['call_id']=='native-source-download'
    args=json.loads(action['args']);assert args['source_packages']==[SOURCE] and args['approval_id']==grant['approval_id']
    artifact=engine.resources.artifact(json.loads(action['result'])['source_artifact_ids'][0],state['task_id'])
    assert artifact['producer_action_id']==action['id']
