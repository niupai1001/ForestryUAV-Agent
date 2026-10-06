import json
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.store import now, uid


@pytest.mark.asyncio
async def test_native_hooks_output_schema_and_checkpoint_compatible(client, app):
    state, _ = task(client)
    engine = app.state.engine
    agent, connection = engine.make_agent("small", "native-test-turn")
    try:
        model = TestModel(call_tools=[], custom_output_args={
            "summary": "框架协议夹具", "artifact_ids": [], "limitations": ["非真实模型试次"]})
        with agent.override(model=model):
            result = await agent.run("验证协议", deps=Deps(engine, state["task_id"], "native-test-turn", 1))
        assert result.output.summary == "框架协议夹具"
        assert engine.store.task(state["task_id"])["model_requests"] == 1
        events = engine.store.events(state["task_id"])
        response = next(e for e in events if e["type"] == "model.response_observed")
        assert response["payload"]["tool_names"] == ["final_result"]
        assert response["payload"]["output_calls"][0]["arguments"]
        assert await engine.steps.latest_snapshot(run_id="native-test-turn") is not None
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_changed_weight_digest_stops_before_model_request(client, app, monkeypatch):
    state, _ = task(client)
    engine = app.state.engine
    def freeze(s, db):
        s["config_snapshot"]["model_identity"] = {"digest": "original-weight"}
    engine.store.change(state["task_id"], "fixture.freeze", freeze)
    async def changed_identity(config, profile):
        return {"digest": "different-weight"}
    monkeypatch.setattr("forestry_agent.agent.model_identity", changed_identity)
    agent, connection = engine.make_agent("small", "changed-identity-turn")
    try:
        with pytest.raises(Failure, match="权重或精度"):
            await agent.run("不应该发送到模型", deps=Deps(engine, state["task_id"], "changed-identity-turn", 1))
        assert engine.store.task(state["task_id"])["model_requests"] == 0
        assert not any(e["type"] == "model.request_started" for e in engine.store.events(state["task_id"]))
    finally:
        await connection.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["H0", "H1", "H2"])
async def test_recovery_control_evidence_survives_resume_but_is_scoped_to_turn(client, app, mode):
    state, _ = task(client, harness_mode=mode)
    engine = app.state.engine
    turn_id = uid()
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)",
            (turn_id, state["task_id"], 1, "恢复中的请求", "running", now()))
    engine.store.change(state["task_id"], "fixture.started", lambda s, _: s.update(status="waiting_job"))
    engine.reconcile_startup()
    event = next(e for e in engine.store.events(state["task_id"]) if e["type"] == "run.reconciled")
    assert event["payload"] == {"status": "paused", "prior_status": "waiting_job", "spec_revision": 1,
        "turn_id": turn_id, "blocker_code": "SERVICE_RESTARTED"}
    paused = engine.store.task(state["task_id"])
    reply = client.post(f"/tasks/{state['task_id']}/resume", json={"request_id": "resume-recovery",
        "expected_state_version": paused["state_version"]})
    assert reply.status_code == 200 and reply.json()["blockers"] == []
    foreign, _ = task(client, request_id="foreign-recovery", harness_mode=mode)
    with engine.store.transaction() as db:
        engine.store.event(db, foreign["task_id"], "run.reconciled",
            {"spec_revision": 1, "turn_id": turn_id, "private": "FOREIGN_RECOVERY_MARKER"})
        engine.store.event(db, state["task_id"], "run.reconciled",
            {"spec_revision": 2, "turn_id": turn_id, "private": "OTHER_REVISION_MARKER"})
    for observed_turn, expected in [(turn_id, True), ("different-turn", False)]:
        agent, connection = engine.make_agent("small", observed_turn, task_id=state["task_id"])
        try:
            with agent.override(model=TestModel(call_tools=[], custom_output_args={
                "summary": "恢复协议夹具", "artifact_ids": [], "limitations": []})):
                await agent.run("观察恢复事实", deps=Deps(engine, state["task_id"], observed_turn, 1))
            with engine.store.transaction() as db:
                manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id=? ORDER BY rowid DESC LIMIT 1",
                    (observed_turn,)).fetchone()[0])
            body = json.loads(Path(manifest["prepared_input_ref"]).read_bytes())
            instructions = "\n".join(body["instructions"])
            assert (event["event_id"] in instructions) is expected
            assert "FOREIGN_RECOVERY_MARKER" not in instructions and "OTHER_REVISION_MARKER" not in instructions
            if expected:
                assert "SERVICE_RESTARTED" in instructions and "不单独证明进程崩溃" in instructions
        finally:
            await connection.aclose()
