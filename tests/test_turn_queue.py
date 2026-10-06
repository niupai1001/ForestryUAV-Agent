import asyncio
import json

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from test_core import task
from test_network import HTTPFixture, read, resolve
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.contracts import Failure
from forestry_agent.store import now


def rows(engine, task_id):
    with engine.store.transaction() as db:
        return [dict(row) for row in db.execute("SELECT * FROM turns WHERE task_id=? ORDER BY rowid", (task_id,))]


def test_message_replay_has_one_durable_turn_and_does_not_unpause(client, app):
    state, _ = task(client)
    engine = app.state.engine
    engine.store.change(state["task_id"], "fixture.pause", lambda s, _: s.update(status="paused", pause_requested=True))
    payload = {"request_id": "message-1", "expected_spec_revision": 1, "text": "请解释进度"}
    response = client.post(f"/tasks/{state['task_id']}/messages", json=payload)
    assert response.status_code == 200 and response.json()["status"] == "paused"
    assert client.post(f"/tasks/{state['task_id']}/messages", json=payload).json() == response.json()
    assert len(rows(engine, state["task_id"])) == 1
    assert rows(engine, state["task_id"])[0]["status"] == "queued"
    assert client.post(f"/tasks/{state['task_id']}/messages", json={**payload, "text": "changed"}).status_code == 409
    with engine.store.transaction() as db:
        association = db.execute("SELECT * FROM turn_requests WHERE request_id='message-1'").fetchone()
        event = db.execute("SELECT payload FROM user_events WHERE id=?", (association["user_event_id"],)).fetchone()
        assert json.loads(event[0])["text"] == payload["text"]


def test_queue_waits_for_live_run_then_dispatches_fifo_and_stops_on_pause(client, app, monkeypatch):
    state, _ = task(client)
    engine = app.state.engine
    task_id = state["task_id"]
    async def exercise():
        release = asyncio.Event()
        order = []
        async def run(_, turn_id):
            order.append(turn_id)
            if turn_id == "initial":
                await release.wait()
            with engine.store.transaction() as db:
                db.execute("UPDATE turns SET status='completed' WHERE id=?", (turn_id,))
        monkeypatch.setattr(engine, "run", run)
        engine.submit(task_id, "initial", "initial")
        await asyncio.sleep(0)
        for index in range(2):
            payload = {"request_id": f"queued-{index}", "expected_spec_revision": 1, "text": f"supplement {index}"}
            # The API accepts input while the prior model turn is alive.
            response = await asyncio.to_thread(client.post, f"/tasks/{task_id}/messages", json=payload)
            assert response.status_code == 200 and response.json()["status"] == "active"
        queued = rows(engine, task_id)[1:]
        assert engine.start_queued(task_id) is None and order == ["initial"]
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert order == ["initial", queued[0]["id"], queued[1]["id"]]
        assert all(row["status"] == "completed" for row in rows(engine, task_id))
        engine.store.change(task_id, "fixture.pause", lambda s, _: s.update(status="paused", pause_requested=True))
        assert engine.start_queued(task_id) is None
    asyncio.run(exercise())


def test_approval_replay_queues_one_correlated_continuation_and_restart_requires_resume(client, app):
    state, _ = task(client)
    engine = app.state.engine
    engine.network.http = HTTPFixture()
    read(engine, state)
    record = engine.network.list(state["task_id"])[0]
    response = resolve(client, state, record)
    assert response.status_code == 200
    assert resolve(client, state, record).json() == response.json()
    queued = rows(engine, state["task_id"])
    assert len(queued) == 1 and queued[0]["id"] == response.json()["continuation_turn_id"]
    assert record["approval_id"] in queued[0]["prompt"]
    engine.store.change(state["task_id"], "fixture.finish", lambda s, _: s.update(status="waiting_user"))
    with pytest.raises(Failure, match="未处理"):
        app.state.delivery.candidate(state["task_id"])
    engine.reconcile_startup()
    assert engine.store.task(state["task_id"])["status"] == "paused"
    assert queued[0] == rows(engine, state["task_id"])[0]


@pytest.mark.parametrize("operation", ["cancel", "amendments"])
def test_cancel_or_amendment_invalidates_queued_turns(client, app, operation):
    state, _ = task(client)
    client.post(f"/tasks/{state['task_id']}/messages", json={"request_id": "queued", "expected_spec_revision": 1, "text": "later"})
    state = app.state.store.task(state["task_id"])
    payload = {"request_id": operation, "expected_state_version": state["state_version"]} if operation == "cancel" else {
        "request_id": operation, "expected_spec_revision": 1, "objective": "new objective", "input_refs": []}
    assert client.post(f"/tasks/{state['task_id']}/{operation}", json=payload).status_code == 200
    assert rows(app.state.engine, state["task_id"])[0]["status"] == "cancelled"


def test_restart_preserves_interrupted_and_later_queued_input_without_unpausing(client, app):
    state, _ = task(client)
    with app.state.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", ("interrupted", state["task_id"], 1, "old", "running", now()))
    app.state.engine.reconcile_startup()
    client.post(f"/tasks/{state['task_id']}/messages", json={"request_id": "later", "expected_spec_revision": 1, "text": "new"})
    assert rows(app.state.engine, state["task_id"])[0]["status"] == "running"
    assert app.state.store.task(state["task_id"])["status"] == "paused"


def test_approval_rechecks_source_before_decision(client, app):
    source = app.state.engine.config.workspace_root.parent / "approval-source.txt"
    source.write_text("original", encoding="utf-8")
    ref = client.post("/resources/register", json={"request_id": "source", "path": str(source)}).json()["resource_id"]
    state, _ = task(client, input_refs=[ref])
    engine = app.state.engine
    engine.network.http = HTTPFixture()
    read(engine, state)
    record = engine.network.list(state["task_id"])[0]
    source.write_text("changed source", encoding="utf-8")
    assert resolve(client, state, record).status_code == 409
    assert not rows(engine, state["task_id"]) and not engine.network.http.requests


@pytest.mark.asyncio
async def test_native_continuation_uses_exact_approval_and_prior_checkpoint_once(client, app, monkeypatch):
    state, _ = task(client)
    engine = app.state.engine
    engine.network.http = HTTPFixture()
    url = "https://safe.example/page"
    observed = []
    def model(messages, info):
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        current = engine.network.list(state["task_id"])
        if not current:
            return ModelResponse(parts=[ToolCallPart("read_web_page", {"url": url}, "propose")])
        if current[0]["status"] == "approved":
            assert any(p.tool_name == "read_web_page" and p.content["kind"] == "approval_required" for p in returns)
            return ModelResponse(parts=[ToolCallPart("read_web_page", {"url": url, "approval_id": current[0]["approval_id"]}, "dispatch")])
        observed.append(current[0]["status"])
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "按真实工具结果返回", "artifact_ids": []}, "answer-"+current[0]["status"])])
    fixture = FixtureFunctionModel(model, stream_function=stream_adapter(model))
    original = engine.make_agent
    def make_agent(*args, **kwargs):
        agent, connection = original(*args, **kwargs)
        agent.model = fixture
        return agent, connection
    monkeypatch.setattr(engine, "make_agent", make_agent)
    initial = engine.submit(state["task_id"], "读取公开软件夹具地址，并等待范围审批", "initial-approval")
    await engine.queue[state["task_id"]]
    assert initial == "initial-approval" and not engine.network.http.requests
    assert engine.store.task(state["task_id"])["blockers"][0]["code"] == "APPROVAL_REQUIRED"
    record = engine.network.list(state["task_id"])[0]
    reply = await asyncio.to_thread(resolve, client, state, record)
    assert reply.status_code == 200
    continuation = engine.start_queued(state["task_id"])
    assert continuation == reply.json()["continuation_turn_id"]
    await engine.queue[state["task_id"]]
    await asyncio.sleep(0)
    assert engine.network.http.requests == [url] and observed == ["pending", "consumed"]
    assert all(row["status"] == "completed" for row in rows(engine, state["task_id"]))
    assert engine.store.task(state["task_id"])["status"] == "waiting_user"
