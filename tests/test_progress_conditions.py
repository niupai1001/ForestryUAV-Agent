"""Only backend-observed conditions distinguish otherwise identical failures."""
import json

import pytest
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure


def sources(client, tmp_path):
    paths, refs = [], []
    for i in range(3):
        path = tmp_path/f'binary-{i}.txt'
        path.write_bytes(b'not text\x00binary fixture')
        paths.append(path)
        refs.append(client.post('/resources/register',json={'request_id':f'binary-{i}',
            'path':str(path),'read_only':True}).json()['resource_id'])
    return paths,refs


def test_actual_read_failures_bind_source_and_changed_version_but_do_not_invent_progress(client, app, tmp_path):
    paths,refs = sources(client,tmp_path)
    state,_ = task(client,input_refs=refs)
    engine = app.state.engine
    deps = Deps(engine,state['task_id'],'condition-checks',1)

    def read(index,call):
        args = {'resource_id':refs[index], 'relative_path':'','offset':0}
        result = engine.perform(deps,call,'fs.read',args,
            lambda _:engine.resources.read(refs[index],task_id=deps.task_id))
        assert result['error']['code'] == 'PRECONDITION_MISSING'
        assert result['error']['condition']['resource_id'] == refs[index]
        return engine.progress.record(deps,call,'read_file',args,result),result

    first,result_one = read(0,'first')
    second,_ = read(1,'second')
    assert first.evidence.obstacle != second.evidence.obstacle
    paths[0].write_bytes(b'changed binary\x00with a different size')
    changed,result_changed = read(0,'changed-version')
    assert result_changed['error']['condition']['version'] != result_one['error']['condition']['version']
    assert not changed.new and not changed.stop_required and not changed.repeated_request
    assert changed.stalled == 3 and changed.evidence.obstacle != first.evidence.obstacle
    with engine.store.transaction() as db:
        engine.progress.check(db,deps)
    with pytest.raises(Failure,match='连续4'):
        read(0,'repeat-version')
    # The authoritative journal still replays the original failure/condition.
    replay,replayed_result = read(0,'first')
    assert replay.replay and replayed_result == result_one
    with engine.store.transaction() as db:
        assert engine.progress.latest(db,deps)['stalled'] == 4
        with pytest.raises(Failure):
            engine.progress.check(db,deps)
        event = json.loads(db.execute("SELECT payload FROM events WHERE task_id=? "
            "AND kind='progress.observed' ORDER BY seq DESC LIMIT 1",(deps.task_id,)).fetchone()[0])
    assert event['condition_sha256'] == changed.evidence.condition


@pytest.mark.asyncio
async def test_native_distinct_binary_sources_can_reach_final_report(client, app, tmp_path, monkeypatch):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from test_conversation_flow import public_stream_adapter
    from test_roles import FixtureFunctionModel

    _,refs = sources(client,tmp_path)
    state,_ = task(client,input_refs=refs,harness_mode='H0')
    engine = app.state.engine
    calls = 0

    def model(messages,info):
        nonlocal calls
        calls += 1
        if calls <= 3:
            return ModelResponse(parts=[ToolCallPart('read_file',{'resource_id':refs[calls-1]},f'file-{calls}')])
        return ModelResponse(parts=[TextPart('三个来源均为二进制，普通文本读取不适用；未重试或启动计算。')])

    fixture = FixtureFunctionModel(model,stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args,**kwargs):
        agent,channel = original(*args,**kwargs)
        agent.model = fixture
        return agent,channel

    monkeypatch.setattr(engine,'make_agent',make_agent)
    engine.submit(state['task_id'],'核查三个输入是否文本并报告限制','binary-native')
    await engine.queue[state['task_id']]
    current = engine.store.task(state['task_id'])
    assert calls == current['model_requests'] == 4 and not current['blockers']
    assert current['messages'][-1]['text'].startswith('三个来源均为二进制')
    observations = [e['payload'] for e in engine.store.events(state['task_id']) if e['type']=='progress.observed']
    assert len({e['condition_sha256'] for e in observations}) == 3
    assert all(not e['new_observation'] and not e['stop_required'] for e in observations)
    assert engine.jobs.list(state['task_id'])['jobs'] == []
