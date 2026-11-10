"""No evidence and a proven repeated action are separate decisions."""
import json

import pytest
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.progress import Progress
from forestry_agent.store import Store


def test_distinct_failed_checks_can_continue_without_claiming_evidence_or_resetting_budget(client, app):
    state,_ = task(client)
    engine = app.state.engine
    deps = Deps(engine,state['task_id'],'distinct-checks',1)
    errors = ['MISSING_METADATA','UNSUPPORTED_ENCODING','DEPENDENCY_MISSING']
    for index,code in enumerate(errors):
        verdict = engine.progress.record(deps,str(index),'read_document',{'resource_id':str(index)},
            {'kind':'failed','error':{'code':code,'message':code}})
        assert not verdict.new and not verdict.stop_required and verdict.stalled == index+1
    reopened = Progress(Store(engine.store.path),engine.config)
    with engine.store.transaction() as db:
        reopened.check(db,deps)
        events = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM events WHERE task_id=? AND kind='progress.observed' ORDER BY seq", (deps.task_id,))]
    assert len(events) == 3 and events[-1]['stalled'] == events[-1]['limit'] == 3
    replay = reopened.record(deps,'2','read_document',{'resource_id':'2'},
        {'kind':'failed','error':{'code':errors[-1],'message':errors[-1]}})
    assert replay.replay and not replay.stop_required and replay.stalled == 3
    # Returning to an already failed request/obstacle is a loop, not a new check.
    with pytest.raises(Failure,match='NO_PROGRESS|连续4'):
        reopened.record(deps,'repeat','read_document',{'resource_id':'0'},
            {'kind':'failed','error':{'code':errors[0],'message':errors[0]}})
    with engine.store.transaction() as db, pytest.raises(Failure):
        reopened.check(db,deps)
    assert engine.store.task(deps.task_id) == state


def test_changing_failure_text_does_not_excuse_same_request(client, app):
    state,_ = task(client)
    deps = Deps(app.state.engine,state['task_id'],'same-request',1)
    progress = app.state.engine.progress
    for index in range(2):
        progress.record(deps,str(index),'code_execute',{'code':'unchanged'},
            {'kind':'failed','error':{'code':'EXECUTION_FAILED','message':f'failure detail {index}'}})
    with pytest.raises(Failure) as caught:
        progress.record(deps,'third','code_execute',{'code':'unchanged'},
            {'kind':'failed','error':{'code':'EXECUTION_FAILED','message':'different wording'}})
    assert caught.value.code == 'NO_PROGRESS'


def test_hypothesis_text_cannot_hide_repeated_obstacle(client, app):
    state,_ = task(client)
    deps = Deps(app.state.engine,state['task_id'],'hypotheses',1)
    progress = app.state.engine.progress
    failure = {'kind':'failed','error':{'code':'PERMISSION_DENIED','message':'no grant'}}
    for index in range(2):
        progress.record(deps,str(index),'read_file',{'hypothesis':f'new story {index}'},failure)
    with pytest.raises(Failure,match='同一阻碍'):
        progress.record(deps,'third','read_document',{'hypothesis':'another story'},failure)


@pytest.mark.asyncio
async def test_native_turn_can_report_distinct_failed_checks_after_threshold(client, app, tmp_path, monkeypatch):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from test_conversation_flow import public_stream_adapter
    from test_roles import FixtureFunctionModel

    refs = []
    for index in range(3):
        path = tmp_path/f'source-{index}.txt'
        path.write_text('unchanged fixture')
        refs.append(client.post('/resources/register',json={'request_id':f'source-{index}',
            'path':str(path),'read_only':True}).json()['resource_id'])
    state,_ = task(client,harness_mode='H0',input_refs=refs)
    engine = app.state.engine
    errors = dict(zip(refs,['MISSING_METADATA','UNSUPPORTED_ENCODING','DEPENDENCY_MISSING'],strict=True))

    def failed_read(resource_id, *args, **kwargs):
        raise Failure(errors[resource_id], 'controlled distinct source check', 422)

    monkeypatch.setattr(engine.resources,'read',failed_read)
    calls = 0

    def model(messages,info):
        nonlocal calls
        calls += 1
        if calls <= 3:
            return ModelResponse(parts=[ToolCallPart('read_file',{'resource_id':refs[calls-1]},f'check-{calls}')])
        return ModelResponse(parts=[TextPart('三项不同检查均有明确障碍；本次没有取得新证据或启动计算。')])

    fixture = FixtureFunctionModel(model,stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args,**kwargs):
        agent,channel = original(*args,**kwargs)
        agent.model = fixture
        return agent,channel

    monkeypatch.setattr(engine,'make_agent',make_agent)
    engine.submit(state['task_id'],'核查三个来源并说明结果，不重试相同检查','distinct-native')
    await engine.queue[state['task_id']]
    current = engine.store.task(state['task_id'])
    assert calls == current['model_requests'] == 4 and not current['blockers']
    assert current['messages'][-1]['text'] == '三项不同检查均有明确障碍；本次没有取得新证据或启动计算。'
    observations = [e['payload'] for e in engine.store.events(state['task_id']) if e['type'] == 'progress.observed']
    assert [e['stalled'] for e in observations] == [1,2,3]
    assert all(not e['new_observation'] and not e['stop_required'] for e in observations)
    assert current['config_snapshot']['generation'] == state['config_snapshot']['generation']
    assert engine.jobs.list(state['task_id'])['jobs'] == []
