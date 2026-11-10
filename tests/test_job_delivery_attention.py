import asyncio

import pytest
from test_core import task
from test_job_delivery import terminal

from forestry_agent.contracts import Failure
from forestry_agent.store import now


def control(client, state, job, action, request_id):
    return client.post(f"/tasks/{state['task_id']}/jobs/{job['job_id']}/delivery",
                       json={'request_id': request_id, 'action': action})


def test_presenting_is_not_consumption_and_still_allows_completion_dispatch(client, app, monkeypatch):
    state, manager, job = terminal(client, app)
    manager.configure_completion(job['job_id'], state['task_id'], '解释已有结果')
    result = control(client, state, job, 'present', 'present-once')
    assert result.status_code == 200 and result.json()['state'] == 'presented'
    assert control(client, state, job, 'present', 'present-once').json() == result.json()
    starts = []
    monkeypatch.setattr(app.state.engine, 'start_queued', lambda task_id: starts.append(task_id))
    app.state.engine.process_job_completions()
    assert starts == [state['task_id']]
    assert manager.delivery(job['job_id'], state['task_id'])['state'] == 'scheduled'
    assert app.state.store.task(state['task_id'])['model_requests'] == 0


@pytest.mark.asyncio
async def test_skip_wins_before_queued_dispatch_and_late_observation_does_not_undo_it(client, app, monkeypatch):
    state, manager, job = terminal(client, app)
    manager.configure_completion(job['job_id'], state['task_id'], '解释已有结果')
    engine = app.state.engine
    monkeypatch.setattr(engine, 'start_queued', lambda task_id: None)
    engine.process_job_completions()
    delivery = manager.delivery(job['job_id'], state['task_id'])
    result = control(client, state, job, 'skip', 'skip-once')
    assert result.status_code == 200 and result.json()['state'] == 'skipped'
    await engine.run(state['task_id'], delivery['consumer_turn_id'])
    engine.process_job_completions()
    manager.acknowledge_terminal(job['job_id'], state['task_id'], 'late-observation')
    assert manager.delivery(job['job_id'], state['task_id'])['state'] == 'skipped'
    assert engine.store.task(state['task_id'])['model_requests'] == 0
    assert not engine.queue
    with engine.store.snapshot() as db:
        assert db.execute('SELECT status FROM turns WHERE id=?', (delivery['consumer_turn_id'],)).fetchone()[0] == 'stopped'
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1


def test_dispatch_failure_is_durable_visible_and_not_automatically_retried(client, app, monkeypatch):
    state, manager, job = terminal(client, app)
    manager.configure_completion(job['job_id'], state['task_id'], '解释已有结果')
    starts = []
    def fail(task_id):
        starts.append(task_id)
        raise Failure('CONTROLLED_DISPATCH_FAILURE', 'queued dispatch unavailable')
    monkeypatch.setattr(app.state.engine, 'start_queued', fail)
    app.state.engine.process_job_completions()
    app.state.engine.process_job_completions()
    assert starts == [state['task_id']]
    listing = client.get(f"/tasks/{state['task_id']}/jobs").json()['jobs'][0]
    assert listing['delivery']['state'] == 'needs_attention'
    assert listing['delivery']['failure']['code'] == 'CONTROLLED_DISPATCH_FAILURE'
    assert manager.read(job['job_id'])['status'] == 'succeeded'
    assert app.state.store.task(state['task_id'])['model_requests'] == 0


@pytest.mark.asyncio
async def test_claimed_result_failure_can_be_read_by_later_writer_without_new_job(client, app, monkeypatch):
    state, manager, job = terminal(client, app)
    manager.configure_completion(job['job_id'], state['task_id'], '解释已有结果')
    engine = app.state.engine
    def fail(*args, **kwargs):
        owner = manager.delivery(job['job_id'], state['task_id'])['consumer_turn_id']
        assert len(engine.sessions.claim_job_input(state['task_id'], owner, 1)) == 1
        raise Failure('MODEL_UNAVAILABLE', 'controlled model adapter failure')
    monkeypatch.setattr(engine, 'make_agent', fail)
    engine.process_job_completions()
    await engine.queue[state['task_id']]
    await asyncio.sleep(0)
    value = manager.delivery(job['job_id'], state['task_id'])
    assert value['state'] == 'needs_attention' and value['failure']['code'] == 'MODEL_UNAVAILABLE'
    with engine.store.transaction() as db:
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)', ('human-continued', state['task_id'], 1, '读取原结果', 'running', now()))
    recovered = engine.sessions.claim_job_input(state['task_id'], 'human-continued', 1)
    assert len(recovered) == 1 and recovered[0]['event_id'] == value['event_id']
    with engine.store.transaction() as db:
        engine.sessions.settle_job_input(db, 'human-continued')
    assert manager.delivery(job['job_id'], state['task_id'])['state'] == 'consumed'
    assert engine.store.task(state['task_id'])['model_requests'] == 0


def test_skip_cannot_consume_a_running_writers_result_or_foreign_task(client, app):
    state, manager, job = terminal(client, app)
    with manager.store.transaction() as db:
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)', ('running-consumer', state['task_id'], 1, '读取结果', 'running', now()))
    app.state.engine.sessions.claim_job_input(state['task_id'], 'running-consumer', 1)
    assert control(client, state, job, 'skip', 'skip-running').status_code == 409
    foreign, _ = task(client, request_id='foreign-delivery')
    assert control(client, foreign, job, 'skip', 'skip-foreign').status_code == 404
    assert manager.delivery(job['job_id'], state['task_id'])['state'] == 'claimed'


def test_failed_followup_does_not_unconsume_already_observed_fact(client, app):
    state, manager, job = terminal(client, app)
    with manager.store.transaction() as db:
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)', ('observing-consumer', state['task_id'], 1, '读取结果', 'paused', now()))
    manager.acknowledge_terminal(job['job_id'], state['task_id'], 'observing-consumer')
    with manager.store.transaction() as db:
        app.state.engine.sessions.fail_job_input(db, state['task_id'], 'observing-consumer', {'code':'REPORT_FAILED','message':'report incomplete'})
    value = manager.delivery(job['job_id'], state['task_id'])
    assert value['state'] == 'consumed' and value['failure']['code'] == 'REPORT_FAILED'
