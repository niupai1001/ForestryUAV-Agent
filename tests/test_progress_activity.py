"""Job continuations share observations, but keep native call/replay identities."""
from dataclasses import replace

import pytest
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.progress import Progress
from forestry_agent.store import Store, now


def continuation(store, task_id, root, child):
    with store.transaction() as db:
        for turn in (root,child):
            db.execute('INSERT OR IGNORE INTO turns VALUES(?,?,?,?,?,?)',
                (turn,task_id,1,'fixture','done',now()))
        db.execute('INSERT INTO turn_continuations VALUES(?,?,?)',(child,task_id,root))


def test_continuation_inherits_stalls_and_stop_after_reopen(client, app):
    state,_ = task(client)
    engine = app.state.engine
    root = Deps(engine,state['task_id'],'original-activity',1)
    for i in range(2):
        engine.progress.record(root,str(i),'write_plan',{}, {'kind':'completed'})
    continuation(engine.store,root.task_id,root.turn_id,'job-follow-up')
    child = replace(root,turn_id='job-follow-up')
    reopened = Progress(Store(engine.store.path),engine.config)
    with pytest.raises(Failure,match='连续3'):
        reopened.record(child,'0','write_plan',{}, {'kind':'completed'})
    with engine.store.transaction() as db:
        latest = reopened.latest(db,root)
        assert latest['stalled'] == 3 and latest['turn_id'] == child.turn_id
        with pytest.raises(Failure):
            reopened.check(db,child)
        with pytest.raises(Failure):
            reopened.check(db,root)
        # A new human turn is not an automatic continuation of the old activity.
        reopened.check(db,replace(root,turn_id='human-correction'))
        reopened.check(db,replace(child,assignment_id='independent-role'))


def test_continuation_repeated_call_id_is_new_decision_not_native_replay(client, app):
    state,_ = task(client)
    engine = app.state.engine
    root = Deps(engine,state['task_id'],'original-read',1)
    value = {'resource_id':'source','version':'v1','text':'known source'}
    engine.progress.record(root,'same-call','read_file',{'resource_id':'source'},value)
    continuation(engine.store,root.task_id,root.turn_id,'second')
    child = replace(root,turn_id='second')
    verdict = engine.progress.record(child,'same-call','read_file',{'resource_id':'source'},value)
    assert not verdict.new and not verdict.replay and verdict.repeated_request and verdict.stalled == 1
    replay = engine.progress.record(child,'same-call','read_file',{'resource_id':'source'},value)
    assert replay.replay and replay.stalled == 1
    continuation(engine.store,root.task_id,root.turn_id,'third')
    third = replace(root,turn_id='third')
    assert engine.progress.record(third,'same-call','read_file',{'resource_id':'source'},value).stalled == 2
    with pytest.raises(Failure):
        engine.progress.record(third,'another-call','read_file',{'resource_id':'source'},value)


def test_terminal_redelivery_after_reopen_and_auto_turn_cannot_reset_stalls(client, app):
    from test_job_delivery import terminal

    state,manager,job = terminal(client,app)
    engine = app.state.engine
    deps = Deps(engine,state['task_id'],job['request_turn_id'],1)
    with engine.store.snapshot() as db:
        event_id = db.execute('SELECT event_id FROM job_deliveries WHERE job_id=?', (job['job_id'],)).fetchone()[0]
    item = {'event_id':event_id,'job_id':job['job_id'],'status':job['status'],
            'artifact_ids':job.get('artifact_ids',[]),'blocker':job.get('blocker')}
    engine.progress.observe_job_input(deps,item)
    engine.progress.record(deps,'plan','write_plan',{}, {'kind':'completed'})
    continuation(engine.store,deps.task_id,deps.turn_id,'redelivered')
    child = replace(deps,turn_id='redelivered')
    reopened = Progress(Store(engine.store.path),engine.config)
    reopened.observe_job_input(child,item)
    with engine.store.snapshot() as db:
        assert reopened.latest(db,child)['stalled'] == 1
        assert db.execute('SELECT COUNT(*) FROM progress_observations WHERE task_id=?', (deps.task_id,)).fetchone()[0] == 2
    assert manager.read(job['job_id'])['status'] == 'succeeded'


@pytest.mark.asyncio
async def test_real_job_completion_adds_terminal_evidence_once_then_retains_stalls(client, app, monkeypatch):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from test_conversation_flow import public_stream_adapter
    from test_job_delivery import terminal
    from test_roles import FixtureFunctionModel

    state,manager,job = terminal(client,app)
    engine = app.state.engine
    root = Deps(engine,state['task_id'],job['request_turn_id'],1)
    for i in range(2):
        engine.progress.record(root,str(i),'write_plan',{}, {'kind':'completed'})
    manager.configure_completion(job['job_id'],state['task_id'],'只解释已有结果，不重复记账')
    calls = 0

    def model(messages,info):
        nonlocal calls
        calls += 1
        if calls > 1:
            return ModelResponse(parts=[TextPart('按已提交作业终态说明结果，不重新计算。')])
        return ModelResponse(parts=[ToolCallPart('read_plan',{},'plan-read')])

    fixture = FixtureFunctionModel(model,stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args,**kwargs):
        agent,channel = original(*args,**kwargs)
        agent.model = fixture
        return agent,channel

    monkeypatch.setattr(engine,'make_agent',make_agent)
    engine.process_job_completions()
    await engine.queue[state['task_id']]
    current = manager.store.task(state['task_id'])
    assert calls == 2 and not current['blockers']
    assert current['messages'][-1]['text'] == '按已提交作业终态说明结果，不重新计算。'
    assert manager.read(job['job_id'])['status'] == 'succeeded'
    observations = [e['payload'] for e in engine.store.events(state['task_id']) if e['type']=='progress.observed']
    assert [e['stalled'] for e in observations] == [1,2,0,1]
    assert observations[2]['tool'] == 'job.observe' and observations[2]['new_observation']
    assert all(e['activity_turn_id'] == root.turn_id for e in observations)
    assert len(engine.jobs.list(state['task_id'])['jobs']) == 1
