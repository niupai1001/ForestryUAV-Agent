import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from test_conversation_flow import public_stream_adapter
from test_job_delivery import terminal
from test_jobs import ExecutorFixture, submit
from test_roles import FixtureFunctionModel
from test_step_inbox import enqueue

from forestry_agent.runtime.job_tools import register_job_tools
from forestry_agent.runtime.tools import ToolRegistry


def test_notification_only_does_not_schedule_a_model(client, app):
    state, manager, job = terminal(client, app)
    manager.configure_completion(job['job_id'], state['task_id'], None)
    app.state.engine.process_job_completions()
    assert not app.state.engine.queue
    with manager.store.snapshot() as db:
        assert not db.execute('SELECT 1 FROM turn_continuations').fetchone()
        assert db.execute('SELECT state FROM job_deliveries').fetchone()[0] == 'pending'


def test_human_input_and_pause_prevent_background_dispatch(client, app):
    state, manager, job = terminal(client, app)
    manager.configure_completion(job['job_id'], state['task_id'], '解释已有结果')
    enqueue(client, state, 'human-first', '先回答我的新问题')
    app.state.engine.process_job_completions()
    with manager.store.snapshot() as db:
        assert not db.execute('SELECT 1 FROM turn_continuations').fetchone()
    with manager.store.transaction() as db:
        db.execute("UPDATE turns SET status='cancelled' WHERE task_id=?", (state['task_id'],))
        current = manager.store.read_task(db, state['task_id'])
        current['pause_requested'] = True
        manager.store.save_task(db, current, current['state_version'])
    app.state.engine.process_job_completions()
    assert not app.state.engine.queue


def test_inflight_worker_snapshot_cannot_erase_completion_intent(client, app):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    worker_snapshot = manager.read(job_id)
    manager.configure_completion(job_id, state['task_id'], '完成后解释结果')
    worker_snapshot['status'] = 'succeeded'
    manager.save(worker_snapshot, 'job.terminal')
    assert manager.read(job_id)['completion_instruction'] == '完成后解释结果'


@pytest.mark.asyncio
@pytest.mark.parametrize('budget_exhausted', [False, True])
async def test_completion_resumes_once_and_keeps_original_activity_budget(client, app, monkeypatch, budget_exhausted):
    state, manager, job = terminal(client, app)
    engine = app.state.engine
    manager.configure_completion(job['job_id'], state['task_id'], '解释已有结果，不重算')
    if budget_exhausted:
        with manager.store.transaction() as db:
            current = manager.store.read_task(db, state['task_id'])
            current['config_snapshot'].setdefault('generation', {})['max_model_requests_per_turn'] = 1
            manager.store.save_task(db, current, current['state_version'])
            manager.store.event(db, state['task_id'], 'model.request_started', {
                'turn_id': job['request_turn_id'], 'assignment_id': None})
    requests = []

    def model(messages, info):
        text = '\n'.join(p.content for m in messages if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, UserPromptPart) and isinstance(p.content, str))
        assert '解释已有结果，不重算' in text and job['job_id'] in text
        requests.append(text)
        return ModelResponse(parts=[TextPart('已按真实作业结果说明；未重新计算。')])

    fixture = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original(*args, **kwargs)
        agent.model = fixture
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.process_job_completions()
    run = engine.queue[state['task_id']]
    await run
    await asyncio.sleep(0)
    engine.process_job_completions()
    assert engine.queue[state['task_id']] is run
    current = manager.store.task(state['task_id'])
    if budget_exhausted:
        assert not requests
        assert current['blockers'][0]['code'] == 'MODEL_TURN_BUDGET_EXHAUSTED'
    else:
        assert len(requests) == 1 and not current['blockers']
        assert current['messages'][-1]['text'] == '已按真实作业结果说明；未重新计算。'
    with manager.store.snapshot() as db:
        rows = db.execute('SELECT * FROM turn_continuations').fetchall()
        assert len(rows) == 1 and rows[0]['root_turn_id'] == job['request_turn_id']
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
        if not budget_exhausted:
            assert db.execute('SELECT state FROM job_deliveries').fetchone()[0] == 'consumed'


@pytest.mark.asyncio
async def test_explicit_wait_yields_to_new_user_input_without_stopping_job(client, app, monkeypatch):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    registry = ToolRegistry()
    register_job_tools(registry, engine=app.state.engine, jobs=manager,
        roles=SimpleNamespace(authorize=lambda *a, **k: None), store=manager.store,
        journal=lambda ctx, kind, args, op: {'kind': 'completed', **op(None)})
    wait = next(spec.func for spec in registry.specs() if spec.name == 'wait_job')
    enqueue(client, state, 'interrupt-wait', '先解释现在的状态')

    async def forbidden_sleep(seconds):
        raise AssertionError('new user input should release the wait')

    monkeypatch.setattr(asyncio, 'sleep', forbidden_sleep)
    result = await wait(SimpleNamespace(deps=SimpleNamespace(task_id=state['task_id'],
        turn_id='waiting-owner', revision=1)), job_id, 120)
    assert result['yielded_to_user'] and result['still_running']
    assert manager.read(job_id)['status'] == 'queued'
