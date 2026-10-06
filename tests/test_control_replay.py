import pytest
from fastapi.testclient import TestClient
from test_core import task

from forestry_agent.app import create_app


class StopProbe:
    def __init__(self):
        self.stops = 0

    def cancel(self):
        self.stops += 1


@pytest.mark.parametrize("operation", ["amendments", "pause"])
def test_old_control_receipt_cannot_cancel_a_newer_run(client, app, operation):
    state, _ = task(client)
    task_id, engine = state["task_id"], app.state.engine
    original, newer = StopProbe(), StopProbe()
    engine.tokens[task_id] = original
    payload = {"request_id": "control-once", "expected_spec_revision": 1, "objective": "修订目标"} if operation == "amendments" else {
        "request_id": "control-once", "expected_state_version": state["state_version"]}
    first = client.post(f"/tasks/{task_id}/{operation}", json=payload)
    assert first.status_code == 200 and original.stops == 1
    engine.tokens[task_id] = newer
    current = engine.store.task(task_id)
    resumed = client.post(f"/tasks/{task_id}/resume", json={"request_id": "newer-resume", "expected_state_version": current["state_version"]})
    assert resumed.status_code == 200
    before = engine.store.task(task_id)
    assert client.post(f"/tasks/{task_id}/{operation}", json=payload).json() == first.json()
    assert newer.stops == 0 and engine.store.task(task_id) == before
    engine.tokens.clear()


def test_resume_receipt_does_not_start_an_extra_model_turn(app, monkeypatch):
    async def identity(*args, **kwargs):
        return {"status": "software_test_fixture"}
    monkeypatch.setattr("forestry_agent.app.model_identity", identity)
    runtime = create_app(app.state.engine.config, enable_agent=True)
    submissions = []
    runtime.state.engine.submit = lambda *args: submissions.append(args)
    with TestClient(runtime, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}) as client:
        client.get("/session")
        state, _ = task(client)
        task_id = state["task_id"]
        paused = client.post(f"/tasks/{task_id}/pause", json={"request_id": "pause-first", "expected_state_version": state["state_version"]}).json()
        payload = {"request_id": "resume-once", "expected_state_version": paused["state_version"]}
        first = client.post(f"/tasks/{task_id}/resume", json=payload)
        assert first.status_code == 200 and len(submissions) == 2
        runtime.state.store.change(task_id, "fixture.settled", lambda state, db: state.update(status="waiting_user"))
        before = runtime.state.store.task(task_id)
        assert client.post(f"/tasks/{task_id}/resume", json=payload).json() == first.json()
        assert len(submissions) == 2 and runtime.state.store.task(task_id) == before
