import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from test_conversation_flow import public_stream_adapter
from test_jobs import ExecutorFixture, submit
from test_roles import FixtureFunctionModel

from forestry_agent.jobs import Jobs
from forestry_agent.runtime.session_repository import StoreSessionRepository
from forestry_agent.store import now
from forestry_agent.runtime.job_tools import register_job_tools
from forestry_agent.runtime.tools import ToolRegistry


def terminal(client, app):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    job = manager.read(job_id)
    job['status'] = 'succeeded'
    manager.save(job, 'job.terminal')
    return state, manager, job


def test_terminal_and_delivery_commit_atomically_and_stale_callback_cannot_replace(client, app):
    state, manager, job = terminal(client, app)
    manager.save(job, 'job.terminal')
    stale = {**job, 'status': 'failed'}
    manager.save(stale, 'job.terminal')
    with manager.store.snapshot() as db:
        rows = db.execute('SELECT * FROM job_deliveries WHERE job_id=?', (job['job_id'],)).fetchall()
        assert len(rows) == 1 and rows[0]['state'] == 'pending'
        event = db.execute('SELECT kind FROM events WHERE id=?', (rows[0]['event_id'],)).fetchone()
        assert event['kind'] == 'job.terminal'
    assert manager.read(job['job_id'])['status'] == 'succeeded'
    assert len([e for e in manager.store.events(state['task_id']) if e['type'] == 'job.terminal']) == 1


def test_terminal_event_failure_rolls_back_status_and_delivery(client, app, monkeypatch):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    job = manager.read(job_id)
    original_status = job['status']
    job['status'] = 'succeeded'
    original = manager.store.event

    def fail_event(db, task_id, kind, payload):
        if kind == 'job.terminal':
            raise RuntimeError('controlled event persistence failure')
        return original(db, task_id, kind, payload)

    monkeypatch.setattr(manager.store, 'event', fail_event)
    with pytest.raises(RuntimeError, match='controlled'):
        manager.save(job, 'job.terminal')
    assert manager.read(job_id)['status'] == original_status
    with manager.store.snapshot() as db:
        assert not db.execute('SELECT 1 FROM job_deliveries WHERE job_id=?', (job_id,)).fetchone()


def test_claim_survives_restart_and_observation_ack_prevents_redelivery(client, app):
    state, manager, job = terminal(client, app)
    with manager.store.transaction() as db:
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',
            ('consumer', state['task_id'], 1, 'report', 'running', now()))
    repo = StoreSessionRepository(manager.store)
    first = repo.claim_job_input(state['task_id'], 'consumer', 1)
    assert len(first) == 1 and first[0]['job_id'] == job['job_id']
    assert StoreSessionRepository(manager.store).claim_job_input(state['task_id'], 'consumer', 1) == first
    restarted = Jobs(manager.config, manager.store, manager.resources, ExecutorFixture())
    restarted.acknowledge_terminal(job['job_id'], state['task_id'], 'consumer')
    restarted.acknowledge_terminal(job['job_id'], state['task_id'], 'consumer')
    assert repo.claim_job_input(state['task_id'], 'consumer', 1) == []
    assert len([e for e in manager.store.events(state['task_id']) if e['type'] == 'job.delivery_consumed']) == 1


def test_paused_session_does_not_claim_terminal_input(client, app):
    state, manager, _ = terminal(client, app)
    with manager.store.transaction() as db:
        current = manager.store.read_task(db, state['task_id'])
        current['pause_requested'] = True
        manager.store.save_task(db, current, current['state_version'])
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',
            ('paused-consumer', state['task_id'], 1, 'report', 'running', now()))
    assert app.state.engine.sessions.claim_job_input(state['task_id'], 'paused-consumer', 1) == []


@pytest.mark.asyncio
async def test_terminal_fact_reaches_native_step_and_consumes_only_after_answer(client, app, monkeypatch):
    state, manager, job = terminal(client, app)
    engine = app.state.engine
    requests = []

    def model(messages, info):
        text = '\n'.join(p.content for m in messages if isinstance(m, ModelRequest)
            for p in m.parts if isinstance(p, UserPromptPart) and isinstance(p.content, str))
        assert text.count('[作业终态 ') == 1
        assert job['job_id'] in text
        with manager.store.snapshot() as db:
            assert db.execute('SELECT state FROM job_deliveries').fetchone()[0] == 'claimed'
        requests.append(text)
        return ModelResponse(parts=[TextPart('计算已结束，尚无现场精度证据。')])

    fixture = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original(*args, **kwargs)
        agent.model = fixture
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.submit(state['task_id'], '说明现有计算', 'completion-consumer')
    await engine.queue[state['task_id']]
    assert len(requests) == 1
    current = engine.store.task(state['task_id'])
    assert not current['blockers'] and current['messages'][-1]['role'] == 'assistant'
    with manager.store.snapshot() as db:
        assert db.execute('SELECT state FROM job_deliveries').fetchone()[0] == 'consumed'


@pytest.mark.asyncio
async def test_default_job_query_does_not_sleep_or_consume_running_job(client, app, monkeypatch):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    registry = ToolRegistry()
    register_job_tools(registry, engine=app.state.engine, jobs=manager,
        roles=SimpleNamespace(authorize=lambda *a, **k: None), store=manager.store,
        journal=lambda ctx, kind, args, op: {'kind': 'completed', **op(None)})
    wait = next(spec.func for spec in registry.specs() if spec.name == 'wait_job')

    async def forbidden_sleep(seconds):
        raise AssertionError('default query should not hold the turn')

    monkeypatch.setattr(asyncio, 'sleep', forbidden_sleep)
    result = await wait(SimpleNamespace(deps=SimpleNamespace(task_id=state['task_id'],
        turn_id='query', revision=1)), job_id)
    assert result['still_running'] and result['waited_seconds'] == 0
    assert manager.read(job_id)['status'] == 'queued'


def test_delete_removes_delivery_before_job_and_event(client, app):
    from test_session_deletion import erase
    state, manager, _ = terminal(client, app)
    current = manager.store.task(state['task_id'])
    result = erase(client, current)
    assert result.status_code == 200, result.text
    with manager.store.snapshot() as db:
        assert not db.execute('SELECT 1 FROM job_deliveries').fetchone()
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
