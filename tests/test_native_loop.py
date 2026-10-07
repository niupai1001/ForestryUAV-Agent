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
@pytest.mark.parametrize("mode", ["H0", "H1", "H2"])
async def test_confirmed_readback_windows_remain_scoped_without_copying_body(client, app, mode):
    state, _ = task(client, harness_mode=mode)
    engine, turn_id = app.state.engine, "readback-facts"
    deps = Deps(engine, state["task_id"], turn_id, 1)
    products = [engine.resources.write(state["task_id"], f"write-{n}", f"read-{n}.md", "READBACK_PRIVATE_BODY"*300)
        for n in range(10)]
    receipts = []
    for n, product in enumerate(products):
        receipts.append(engine.perform(deps, f"read-{n}", "artifact.read", {"artifact_id":product["artifact_id"], "offset":0},
            lambda _, product=product: engine.resources.read_artifact(state["task_id"], product["artifact_id"])))
    latest = engine.perform(deps, "tail", "artifact.read", {"artifact_id":products[-1]["artifact_id"], "offset":4000},
        lambda _: engine.resources.read_artifact(state["task_id"], products[-1]["artifact_id"], 4000))
    # A later failed read must not replace confirmed metadata; neither may an old
    # turn, another revision or a receipt with a different immutable fingerprint.
    with engine.store.transaction() as db:
        for label, turn, revision, status, sha in [
            ("FAILED_READ_MARKER", turn_id, 1, "failed", products[-1]["sha256"]),
            ("OTHER_TURN_READ_MARKER", "other-turn", 1, "confirmed", products[-1]["sha256"]),
            ("OLD_REVISION_READ_MARKER", turn_id, 2, "confirmed", products[-1]["sha256"]),
            ("HASH_MISMATCH_MARKER", turn_id, 1, "confirmed", "0"*64)]:
            result = {"kind":"completed", "artifact_id":products[-1]["artifact_id"], "sha256":sha,
                "offset":0, "next":1, "has_more":False, "text":label}
            db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)", (label, state["task_id"], turn, label,
                revision, "artifact.read", "{}", status, json.dumps(result)))
    agent, connection = engine.make_agent("small", turn_id, state["config_snapshot"], state["task_id"])
    try:
        with agent.override(model=TestModel(call_tools=[], custom_output_args={"summary":"读回记录边界夹具", "artifact_ids":[]})):
            await agent.run("检查已读窗口", deps=deps)
        with engine.store.transaction() as db:
            manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id=? ORDER BY rowid DESC LIMIT 1", (turn_id,)).fetchone()[0])
        body = json.loads(Path(manifest["prepared_input_ref"]).read_bytes())
        instructions = "\n".join(body["instructions"])
        assert "READBACK_PRIVATE_BODY" not in instructions
        if mode == "H0":
            assert '"recent_artifact_readbacks"' not in instructions
        else:
            facts = next(json.loads(text.split("：",1)[1].split("\n",1)[0]) for text in body["instructions"] if text.startswith("当前权威任务事实"))
            observed = facts["recent_artifact_readbacks"]
            assert len(observed) == 8 and len({r["artifact_id"] for r in observed}) == 8
            assert observed[0] == {"source_action_id":latest["action_id"], "artifact_id":products[-1]["artifact_id"],
                "sha256":products[-1]["sha256"], "offset":4000, "next":latest["next"], "has_more":False}
            assert observed[-1]["source_action_id"] == receipts[2]["action_id"]
            assert not {"FAILED_READ_MARKER","OTHER_TURN_READ_MARKER","OLD_REVISION_READ_MARKER","HASH_MISMATCH_MARKER"} & {
                r["source_action_id"] for r in observed}
            assert "offset>0不证明前文已读" in instructions and "记录不能证明内容正确" in instructions
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
        agent, connection = engine.make_agent("small", observed_turn, state["config_snapshot"], task_id=state["task_id"])
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["H0", "H1", "H2"])
async def test_named_artifacts_and_recent_job_records_are_scoped_bounded_native_facts(client, app, mode):
    state, _ = task(client, harness_mode=mode)
    foreign, _ = task(client, request_id="foreign-artifact-facts", harness_mode=mode)
    engine = app.state.engine
    original = engine.resources.write(state["task_id"], "old-write", "old-product.md", "OLD_PRIVATE_CONTENT")
    products = [engine.resources.write(state["task_id"], "write-"+str(n), f"product-{n}.json", "PRIVATE_BODY_MARKER")
        for n in range(25)]
    hidden = engine.resources.write(foreign["task_id"], "foreign-write", "FOREIGN_ARTIFACT_MARKER.md", "FOREIGN_BODY")
    with engine.store.transaction() as db:
        for index in range(10):
            job_id = f"fixture-job-{index}"
            payload = {"job_id":job_id, "status":"failed" if index == 9 else "succeeded", "kind":"code.execute",
                "artifact_ids":[p["artifact_id"] for p in products], "code":"PRIVATE_CODE_MARKER",
                "environment":{"artifact_id":products[0]["artifact_id"], "manifest":{"private":"PRIVATE_MANIFEST"}}}
            db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)",
                (f"fixture-action-{index}", state["task_id"], "fixture-turn", str(index), 1, "code.execute", "{}", "confirmed", "{}"))
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
                (job_id, state["task_id"], f"fixture-action-{index}", 1, payload["status"], json.dumps(payload), now()))
        receipt = {"kind":"completed", "job":{"job_id":"fixture-job-9", "status":"failed"},
            "logs":{"stderr":"HIDDEN_DIAGNOSTIC_PREFIX_"+"诊断"*200+"\nModuleNotFoundError: no example"}}
        db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)", ("fixture-observation", state["task_id"], "fixture-turn",
            "observe-9", 1, "job.observe", "{}", "confirmed", json.dumps(receipt)))
        for task_id, revision, marker in [(foreign["task_id"], 1, "FOREIGN_JOB_MARKER"),
                                         (state["task_id"], 2, "OTHER_REVISION_JOB_MARKER")]:
            db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)",
                (marker, task_id, "fixture-turn", marker, revision, "code.execute", "{}", "confirmed", "{}"))
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
                (marker, task_id, marker, revision, "succeeded", json.dumps({"job_id":marker}), now()))
    turn_id = "bounded-recorded-facts"
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (turn_id, state["task_id"], 1, state["objective"], "running", now()))
    agent, connection = engine.make_agent("small", turn_id, state["config_snapshot"], task_id=state["task_id"])
    try:
        with agent.override(model=TestModel(call_tools=[], custom_output_args={
            "summary":"元数据协议夹具，不是执行证据", "artifact_ids":[], "limitations":[]})):
            await agent.run("读取当前产物标识", deps=Deps(engine, state["task_id"], turn_id, 1))
        with engine.store.transaction() as db:
            manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id=? ORDER BY rowid DESC LIMIT 1",
                (turn_id,)).fetchone()[0])
        body = json.loads(Path(manifest["prepared_input_ref"]).read_bytes())
        instructions = "\n".join(body["instructions"])
        assert all(marker not in instructions for marker in ["PRIVATE_BODY_MARKER", "PRIVATE_CODE_MARKER",
            "PRIVATE_MANIFEST", hidden["artifact_id"], "FOREIGN_ARTIFACT_MARKER", "FOREIGN_JOB_MARKER", "OTHER_REVISION_JOB_MARKER"])
        if mode == "H0":
            assert all(key not in instructions for key in ['"recent_artifacts"', '"recent_jobs"', '"recent_artifact_readbacks"'])
        else:
            facts = next(json.loads(text.split("：",1)[1].split("\n",1)[0]) for text in body["instructions"]
                if text.startswith("当前权威任务事实"))
            assert facts["current_turn_user_request_is_objective"] is True and facts["current_turn_user_request"] is None
            assert instructions.count(state["objective"]) == 1
            assert len(facts["recent_artifacts"]) == 24
            assert original["artifact_id"] not in str(facts["recent_artifacts"])
            assert facts["recent_artifacts"][-1] == {"artifact_id":products[-1]["artifact_id"], "name":"product-24.json"}
            assert len(facts["recent_jobs"]) == 8 and facts["recent_jobs"][0]["job_id"] == "fixture-job-9"
            assert facts["recent_jobs"][0]["recorded_status"] == "failed"
            diagnostic = facts["recent_jobs"][0]["diagnostic"]
            assert diagnostic["source_action_id"] == "fixture-observation" and diagnostic["truncated"] is True
            assert len(diagnostic["text"].encode("utf-8")) <= 512 and "ModuleNotFoundError: no example" in diagnostic["text"]
            assert "HIDDEN_DIAGNOSTIC_PREFIX" not in instructions
            assert all(j["artifact_count"] == 25 and
                j["artifact_ids"] == [p["artifact_id"] for p in products[-8:]] for j in facts["recent_jobs"])
            assert "记录状态不是新的执行观察或验收" in instructions
    finally:
        await connection.aclose()
