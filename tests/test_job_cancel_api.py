import pytest
from test_core import task
from test_jobs import ExecutorFixture, submit


def test_job_cancel_is_owned_idempotent_and_does_not_cancel_whole_activity(client, app):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    endpoint = f"/tasks/{state['task_id']}/jobs/{job_id}/cancel"
    payload = {'request_id': 'cancel-specific-job'}
    first = client.post(endpoint, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()['cancel_requested'] and first.json()['status'] == 'queued'
    assert not app.state.store.task(state['task_id'])['cancellation_requested']
    manager.tick()
    assert manager.read(job_id)['status'] == 'cancelled'
    assert client.post(endpoint, json=payload).json() == first.json()
    assert manager.read(job_id)['status'] == 'cancelled'
    events = app.state.store.events(state['task_id'])
    assert len([event for event in events if event['type'] == 'job.cancel_requested']) == 1


def test_job_cancel_rejects_foreign_task_and_request_payload_reuse(client, app):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    foreign, _ = task(client, request_id='foreign-cancel')
    payload = {'request_id': 'one-cancel'}
    assert client.post(f"/tasks/{foreign['task_id']}/jobs/{job_id}/cancel", json=payload).status_code == 403
    assert not manager.read(job_id)['cancel_requested']
    assert client.post(f"/tasks/{state['task_id']}/jobs/{job_id}/cancel", json=payload).status_code == 200
    response = client.post(f"/tasks/{state['task_id']}/jobs/another/cancel", json=payload)
    assert response.status_code == 409 and response.json()['error']['code'] == 'VERSION_CONFLICT'


@pytest.mark.parametrize('terminal', ['succeeded', 'failed', 'cancelled', 'timed_out'])
def test_cancel_does_not_rewrite_terminal_job_or_issue_new_cancel_event(client, app, terminal):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    job = manager.read(job_id)
    job['status'] = terminal
    manager.save(job, 'job.terminal')
    response = client.post(f"/tasks/{state['task_id']}/jobs/{job_id}/cancel", json={'request_id': 'cancel-settled'})
    assert response.status_code == 200 and response.json()['status'] == terminal
    assert not response.json()['cancel_requested']
    assert not any(event['type'] == 'job.cancel_requested' for event in app.state.store.events(state['task_id']))
