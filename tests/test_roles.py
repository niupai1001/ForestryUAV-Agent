
import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from test_core import task

from forestry_agent.agent import Deps, RoleProxy
from forestry_agent.contracts import Failure
from forestry_agent.roles import CURRENT_ASSIGNMENT, AssignmentRequest, AssignmentResult
from forestry_agent.store import now


class FixtureFunctionModel(FunctionModel):
    @property
    def system(self):
        return "test"


def stream_adapter(model):
    async def stream(messages, info):
        response = model(messages, info)
        for index, part in enumerate(response.parts):
            yield {index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args_as_dict()), tool_call_id=part.tool_call_id)}
    return stream


@pytest.mark.asyncio
async def test_native_two_delegations_are_isolated_scoped_and_do_not_finish_parent(client, app):
    source = app.state.engine.config.workspace_root.parent / "source.txt"
    source.write_text("软件夹具值为42，不是现场数据。", encoding="utf-8")
    grant = client.post("/resources/register", json={"request_id": "scope", "path": str(source)}).json()
    state, _ = task(client, objective="PARENT_PRIVATE_MARKER：独立数据准备与质量复核", input_refs=[grant["resource_id"]])
    engine, returns_seen, scopes_seen = app.state.engine, [], []
    private_product = engine.resources.write(state["task_id"], "parent-metadata", "PARENT_ARTIFACT_MARKER.md", "Private parent body")
    parent_read = engine.perform(Deps(engine, state["task_id"], "root-role-test", 1), "parent-read", "artifact.read",
        {"artifact_id":private_product["artifact_id"]}, lambda _: engine.resources.read_artifact(state["task_id"], private_product["artifact_id"]))
    with engine.store.transaction() as db:
        engine.store.event(db, state["task_id"], "run.reconciled", {"spec_revision": 1,
            "turn_id": "root-role-test", "private": "PARENT_RECOVERY_MARKER"})
    def model(messages, info):
        child = "委派的权威范围" in (info.instructions or "")
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        if child:
            assert "PARENT_PRIVATE_MARKER" not in (info.instructions or "") + str(messages)
            assert "PARENT_RECOVERY_MARKER" not in (info.instructions or "") + str(messages)
            assert "PARENT_ARTIFACT_MARKER" not in (info.instructions or "") + str(messages)
            assert parent_read["action_id"] not in (info.instructions or "") + str(messages)
            names = {t.name for t in info.function_tools}
            assert "delegate_task" not in names and "code_execute" not in names and "read_user_memory" not in names
            role = "quality" if '"role_id":"quality"' in info.instructions else "data"
            scopes_seen.append(role)
            if not returns:
                return ModelResponse(parts=[ToolCallPart("read_document", {"resource_id": grant["resource_id"]}, f"read-{role}")])
            if returns[-1].tool_name == "read_document":
                assert "42" in returns[-1].content["text"]
                return ModelResponse(parts=[ToolCallPart("write_file", {"relative_path": "report.md", "content": f"{role}: source observed 42"}, f"write-{role}")])
            artifact = next(p.content["artifact_id"] for p in returns if p.tool_name == "write_file")
            return ModelResponse(parts=[ToolCallPart("final_result", {"status": "succeeded", "kind": "quality_assessment" if role == "quality" else "dataset_manifest",
                "summary": f"{role}观察42", "artifact_ids": [artifact], "evidence_refs": [grant["resource_id"]],
                "checks": [{"criterion": "实际读取", "status": "passed", "observation": "42", "evidence_refs": [grant["resource_id"]]}],
                "limitations": ["软件夹具"]}, f"final-{role}")])
        assert "PARENT_RECOVERY_MARKER" in (info.instructions or "")
        delegated = [p for p in returns if p.tool_name == "delegate_task"]
        if len(delegated) < 2:
            role = "data" if not delegated else "quality"
            request = AssignmentRequest(objective="独立读取夹具并写报告", input_refs=[grant["resource_id"]])
            return ModelResponse(parts=[ToolCallPart("delegate_task", {"agent_name": role, "task": request.model_dump_json()}, f"delegate-{role}")])
        returns_seen.extend(delegated)
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "两个独立建议需父级整合", "artifact_ids": [], "limitations": ["尚未验收"]}, "root-final")])
    agent, connection = engine.make_agent("small", "root-role-test", state["config_snapshot"], state["task_id"])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            result = await agent.run(state["objective"], deps=Deps(engine, state["task_id"], "root-role-test", 1))
        assert result.output.summary == "两个独立建议需父级整合"
        assignments = engine.roles.list(state["task_id"])
        assert len(assignments) == 2 and all(a["status"] == "succeeded" for a in assignments)
        assert len({a["run_id"] for a in assignments}) == len({a["namespace"] for a in assignments}) == 2
        for assignment in assignments:
            assert assignment["model_requests"] == 3 and assignment["actions"] == 2
            artifact = engine.resources.artifact(assignment["result"]["artifact_ids"][0])
            assert artifact["relative_path"].startswith("assignments/" + assignment["assignment_id"] + "/")
            assert await engine.latest_checkpoint(assignment["run_id"])
        assert engine.store.task(state["task_id"])["status"] == "created"
        assert not engine.store.task(state["task_id"])["messages"]
        assert set(scopes_seen) == {"data", "quality"}
        assert len(returns_seen) == 2
        for returned in returns_seen:
            receipt = json.loads(returned.content) if isinstance(returned.content, str) else returned.content
            owned = next(a for a in assignments if a["assignment_id"] == receipt["assignment_id"])
            assert receipt == engine.roles.result_receipt(owned)
            assert receipt["role_id"] == owned["role_id"] and receipt["artifact_ids"] == owned["result"]["artifact_ids"]
        assert engine.store.task(state["task_id"])["model_requests"] == 9
    finally:
        await connection.aclose()


def prepare(engine, state, role="data", request=None, call_id="delegate-boundary"):
    deps = Deps(engine, state["task_id"], "root-boundary", state["spec_revision"])
    request = request or AssignmentRequest(objective="独立检查")
    return engine.perform(deps, call_id, "role.delegate", {"role": role, "request": request.model_dump()},
        lambda action: engine.roles.prepare(deps, action, role, request))


def test_effective_scope_rejects_foreign_input_escape_and_recursive_delegation(client, app):
    state, _ = task(client)
    engine = app.state.engine
    denied = prepare(engine, state, request=AssignmentRequest(objective="注入授权", input_refs=["forged-resource"]))
    assert denied["error"]["code"] == "PERMISSION_DENIED" and not engine.roles.list(state["task_id"])
    denied = prepare(engine, state, role="coordinator", call_id="not-root")
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    handle = prepare(engine, state, call_id="valid")
    scope = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    child = Deps(engine, state["task_id"], scope["run_id"], 1, assignment_id=scope["assignment_id"])
    for kind, args in [("fs.read", {"resource_id": "unassigned"}), ("code.execute", {"input_refs": []}), ("role.delegate", {})]:
        with pytest.raises(Failure, match="范围|权限交集"):
            engine.perform(child, kind, kind, args, lambda _: pytest.fail("unauthorized operation ran"))
    with pytest.raises(Failure, match="超出"):
        engine.roles.output_path(child, "../../parent.md")
    with engine.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM actions WHERE turn_id=?", (scope["run_id"],)).fetchone()[0] == 0


def test_assignments_block_acceptance_until_stopped_and_cancel_waiting_is_idempotent(client, app):
    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state)
    engine.store.change(state["task_id"], "fixture.answer", lambda s, _: s.update(status="waiting_user"))
    with pytest.raises(Failure, match="未确认结束"):
        app.state.delivery.candidate(state["task_id"])
    state = engine.store.task(state["task_id"])
    response = client.post(f"/tasks/{state['task_id']}/cancel", json={"request_id": "cancel-role", "expected_state_version": state["state_version"]})
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert engine.roles.read(handle["assignment_id"])["status"] == "cancelled"


@pytest.mark.parametrize("outcome,turn_status", [("failed", "stopped"), ("waiting", "paused"), ("cancelled", "cancelled")])
def test_child_stop_synchronizes_its_turn_without_closing_parent(client, app, outcome, turn_status):
    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state)
    assignment = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (assignment["run_id"], state["task_id"], 1, "child", "running", now()))
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", ("parent-pending", state["task_id"], 1, "parent", "queued", now()))
    engine.roles.stop(assignment["assignment_id"], outcome, blocker={"code": "fixture_failure"})
    with engine.store.transaction() as db:
        assert db.execute("SELECT status FROM turns WHERE id=?", (assignment["run_id"],)).fetchone()[0] == turn_status
        assert db.execute("SELECT status FROM turns WHERE id='parent-pending'").fetchone()[0] == "queued"
    engine.store.change(state["task_id"], "fixture.answer", lambda s, _: s.update(status="waiting_user"))
    with pytest.raises(Failure, match="未处理"):
        app.state.delivery.candidate(state["task_id"])


def test_reconcile_closes_legacy_failed_child_turn_once_and_preserves_failure(client, app):
    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state)
    assignment = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    blocker = {"code": "NO_PROGRESS", "message": "original failure retained"}
    engine.roles.stop(assignment["assignment_id"], "failed", blocker=blocker)
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (assignment["run_id"], state["task_id"], 1, "legacy-child", "running", now()))
    artifact = engine.resources.write(state["task_id"], "parent-report", "report.md", "实际报告，子失败保留。")
    def answer(s, db):
        s.update(status="waiting_user")
        s["messages"].append({"role": "assistant", "spec_revision": 1, "turn_id": "completed-parent",
            "text": "报告完成，原子角色失败已处理。", "artifact_ids": [artifact["artifact_id"]], "limitations": ["软件夹具"]})
    engine.store.change(state["task_id"], "fixture.answer", answer)
    with pytest.raises(Failure, match="未处理"):
        app.state.delivery.candidate(state["task_id"])
    engine.roles.reconcile()
    engine.roles.reconcile()
    assert app.state.delivery.candidate(state["task_id"])["candidate"]["outputs"][0]["artifact_id"] == artifact["artifact_id"]
    assert engine.roles.read(assignment["assignment_id"])["blocker"] == blocker
    with engine.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='assignment.turn_reconciled'").fetchone()[0] == 1
        assert db.execute("SELECT status FROM turns WHERE id=?", (assignment["run_id"],)).fetchone()[0] == "stopped"


def test_harness_modes_freeze_catalog_and_only_h2_has_delegation(client, app):
    for mode in ["H0", "H1", "H2"]:
        state, _ = task(client, request_id=mode, harness_mode=mode)
        assert state["config_snapshot"]["harness_mode"] == mode
        assert len(state["config_snapshot"]["role_catalog"]["roles"]) == 5
        if mode != "H2":
            handle = prepare(app.state.engine, state)
            assert handle["error"]["code"] == "PERMISSION_DENIED"
    assert len(client.get("/roles").json()["roles"]) == 5
    assert "AssignmentResult" in client.get("/roles/schemas").json()


def test_restart_resume_keeps_scope_run_deadline_and_cumulative_assignment_limit(client, app):
    state, _ = task(client)
    engine = app.state.engine
    handles = [prepare(engine, state, call_id=f"limit-{index}") for index in range(8)]
    current = engine.roles.start(engine.roles.read(handles[0]["assignment_id"]))
    engine.roles.reconcile()
    assert engine.roles.read(current["assignment_id"])["status"] == "waiting"
    request = AssignmentRequest(objective="独立检查", resume_assignment_id=current["assignment_id"])
    handle = prepare(engine, state, request=request, call_id="resume-limit")
    restarted = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    assert restarted["run_id"] == current["run_id"] and restarted["deadline_at"] == current["deadline_at"]
    assert restarted["effective_input_refs"] == current["effective_input_refs"]
    denied = prepare(engine, state, request=request.model_copy(update={"objective": "替换目标"}), call_id="change-resume")
    assert denied["error"]["code"] == "VERSION_CONFLICT"
    denied = prepare(engine, state, call_id="ninth")
    assert denied["error"]["code"] == "RESOURCE_EXHAUSTED"
    assert len(engine.roles.list(state["task_id"])) == 8


def test_parent_and_child_requests_share_persistent_budget(client, app):
    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state)
    assignment = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    root = Deps(engine, state["task_id"], "root-boundary", 1)
    child = Deps(engine, state["task_id"], assignment["run_id"], 1, assignment_id=assignment["assignment_id"])
    with engine.store.transaction() as db:
        current = engine.store.read_task(db, state["task_id"])
        current["config_snapshot"]["generation"]["max_model_requests_per_turn"] = 3
        engine.store.save_task(db, current, current["state_version"])
    for deps in [root, child, root]:
        with engine.store.transaction() as db:
            engine.roles.before_request(db, deps)
            engine.store.event(db, state["task_id"], "model.request_started", {"turn_id": deps.turn_id})
    engine.roles.reconcile()
    engine.roles.start(engine.roles.read(assignment["assignment_id"]))
    for deps in [root, child]:
        with engine.store.transaction() as db, pytest.raises(Failure, match="累计请求预算"):
            engine.roles.before_request(db, deps)
    assert engine.roles.read(assignment["assignment_id"])["model_requests"] == 1


def test_coordinator_ceiling_intersects_child_and_unregistered_caps_are_rejected(client, app):
    state, _ = task(client)
    engine = app.state.engine
    with engine.store.transaction() as db:
        current = engine.store.read_task(db, state["task_id"])
        coordinator = next(r for r in current["config_snapshot"]["role_catalog"]["roles"] if r["role_id"] == "coordinator")
        coordinator["allowed_capabilities"].remove("fs.read")
        coordinator["permission_ceiling"]["workspace_write"] = False
        engine.store.save_task(db, current, current["state_version"])
    handle = prepare(engine, state)
    assignment = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    assert "fs.read" not in assignment["effective_capabilities"]
    child = Deps(engine, state["task_id"], assignment["run_id"], 1, assignment_id=assignment["assignment_id"])
    root = Deps(engine, state["task_id"], "root-boundary", 1)
    for deps, kind in [(child, "fs.read"), (child, "fs.write_workspace"), (root, "fs.write_workspace"), (root, "unregistered")]:
        with pytest.raises(Failure, match="上限|权限交集"):
            engine.perform(deps, kind, kind, {}, lambda _: pytest.fail("forbidden effect ran"))


def test_coordinator_budget_is_a_real_ceiling_and_cached_actions_do_not_recharge(client, app):
    state, _ = task(client)
    engine = app.state.engine
    with engine.store.transaction() as db:
        current = engine.store.read_task(db, state["task_id"])
        role = next(r for r in current["config_snapshot"]["role_catalog"]["roles"] if r["role_id"] == "coordinator")
        role["max_actions"], role["max_requests"] = 1, 1
        engine.store.save_task(db, current, current["state_version"])
    root = Deps(engine, state["task_id"], "ceiling-root", 1)
    one = engine.perform(root, "read", "fs.inputs", {}, lambda _: {"resources": []})
    assert engine.perform(root, "read", "fs.inputs", {}, lambda _: pytest.fail("replayed effect")) == one
    with pytest.raises(Failure, match="动作预算"):
        engine.perform(root, "read-two", "fs.inputs", {}, lambda _: pytest.fail("exceeded effect"))
    with engine.store.transaction() as db:
        engine.roles.before_request(db, root)
        engine.store.event(db, state["task_id"], "model.request_started", {"turn_id": root.turn_id})
    with engine.store.transaction() as db, pytest.raises(Failure, match="累计请求预算"):
        engine.roles.before_request(db, root)


def test_changed_source_and_fabricated_results_are_rejected(client, app):
    source = app.state.engine.config.workspace_root.parent / "changed.txt"
    source.write_text("before", encoding="utf-8")
    grant = client.post("/resources/register", json={"request_id": "changed", "path": str(source)}).json()
    state, _ = task(client, input_refs=[grant["resource_id"]])
    engine = app.state.engine
    handle = prepare(engine, state, request=AssignmentRequest(objective="读取", input_refs=[grant["resource_id"]]))
    assignment = engine.roles.read(handle["assignment_id"])
    source.write_text("changed source", encoding="utf-8")
    with pytest.raises(Failure, match="发生变化"):
        engine.roles.start(assignment)
    result = AssignmentResult(status="succeeded", kind="dataset_manifest", summary="claim")
    for update, message in [({"artifact_ids": ["forged"]}, "范围外"), ({"evidence_refs": ["forged"]}, "真实"), ({"kind": "quality_assessment"}, "种类")]:
        with pytest.raises(Failure, match=message):
            engine.roles.validate_result(assignment, result.model_copy(update=update))


def test_resumed_child_charges_new_parent_turn_without_resetting_its_usage(client, app):
    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state)
    assignment = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    with engine.store.transaction() as db:
        current = engine.store.read_task(db, state["task_id"])
        current["config_snapshot"]["generation"]["max_model_requests_per_turn"] = 2
        engine.store.save_task(db, current, current["state_version"])
        engine.store.event(db, state["task_id"], "model.request_started", {"turn_id": assignment["run_id"]})
        engine.store.event(db, state["task_id"], "model.request_started", {"turn_id": "resumed-root"})
    engine.roles.reconcile()
    root = Deps(engine, state["task_id"], "resumed-root", 1)
    request = AssignmentRequest(objective="独立检查", resume_assignment_id=assignment["assignment_id"])
    handle = engine.perform(root, "resume-new-root", "role.delegate", {}, lambda action: engine.roles.prepare(root, action, "data", request))
    value = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    assert value["parent_run_id"] == "root-boundary" and value["budget_parent_run_id"] == "resumed-root"
    child = Deps(engine, state["task_id"], value["run_id"], 1, assignment_id=value["assignment_id"])
    for deps in [root, child]:
        with engine.store.transaction() as db, pytest.raises(Failure, match="累计请求预算"):
            engine.roles.before_request(db, deps)


def test_child_jobs_have_own_namespace_and_unfinished_jobs_block_result(client, app):
    from test_jobs import ExecutorFixture

    from forestry_agent.jobs import Jobs
    from forestry_agent.resources import native_path

    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state, role="code_experiment")
    value = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    child = Deps(engine, state["task_id"], value["run_id"], 1, assignment_id=value["assignment_id"])
    fake = ExecutorFixture()
    jobs = Jobs(engine.config, engine.store, engine.resources, fake)
    submitted = engine.perform(child, "child-code", "code.execute", {"input_refs": []},
        lambda action: jobs.submit(state["task_id"], 1, action, "print(42)", [], 60))
    job = jobs.read(submitted["job"]["job_id"])
    result = AssignmentResult(status="succeeded", kind="experiment_result", summary="fixture", job_ids=[job["job_id"]])
    with pytest.raises(Failure, match="未确认结束"):
        engine.roles.validate_result(value, result)
    jobs.tick()
    native_path(Path(job["directory"]) / "output/result.txt").write_text("42", encoding="utf-8")
    fake.container["State"].update(Status="exited", Running=False, ExitCode=0)
    jobs.tick()
    job = jobs.read(job["job_id"])
    assert job["status"] == "succeeded" and job["assignment_id"] == value["assignment_id"]
    artifact = engine.resources.artifact(job["artifact_ids"][0])
    assert artifact["relative_path"].startswith(f"assignments/{value['assignment_id']}/jobs/")
    engine.roles.validate_result(value, result.model_copy(update={"artifact_ids": job["artifact_ids"]}))


@pytest.mark.asyncio
async def test_child_resumes_its_checkpoint_without_repeating_confirmed_write(client, app):
    state, _ = task(client, objective="PARENT_PRIVATE_RECOVERY_MARKER")
    engine = app.state.engine
    handle = prepare(engine, state)
    role = engine.roles.configurations(state)["data"]
    interrupted = False
    def model(messages, info):
        nonlocal interrupted
        assert "PARENT_PRIVATE_RECOVERY_MARKER" not in str(messages) + (info.instructions or "")
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "write_file"]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("write_file", {"relative_path": "resume.md", "content": "confirmed once"}, "durable-write")])
        if not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        return ModelResponse(parts=[ToolCallPart("final_result", {"kind": "dataset_manifest", "status": "succeeded",
            "summary": "恢复同一个已确认产物", "artifact_ids": [returns[-1].content["artifact_id"]]}, "recovered-output")])
    root = Deps(engine, state["task_id"], "root-boundary", 1)
    token = CURRENT_ASSIGNMENT.set(handle["assignment_id"])
    try:
        with pytest.raises(asyncio.CancelledError):
            await RoleProxy(engine, role).run(deps=root, model=FixtureFunctionModel(model))
        before = engine.roles.read(handle["assignment_id"])
        checkpoint = await engine.latest_checkpoint(before["run_id"])
        assert before["status"] == "waiting" and checkpoint
        result = await RoleProxy(engine, role).run(deps=root, model=FixtureFunctionModel(model))
        after = engine.roles.read(handle["assignment_id"])
        assert result.output.summary == "恢复同一个已确认产物" and after["status"] == "succeeded"
        assert after["run_id"] == before["run_id"] and after["deadline_at"] == before["deadline_at"]
        assert after["actions"] == 1 and engine.store.task(state["task_id"])["status"] == "created"
        with engine.store.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM actions WHERE turn_id=? AND kind='fs.write_workspace'", (before["run_id"],)).fetchone()[0] == 1
            runs = [r["id"] for r in db.execute("SELECT id FROM agent_runs WHERE turn_id=?", (before["run_id"],))]
            assert len(set(runs)) == 2
        assert (await engine.latest_checkpoint(after["run_id"])).run_id != checkpoint.run_id
    finally:
        CURRENT_ASSIGNMENT.reset(token)


@pytest.mark.asyncio
async def test_parent_resumes_with_new_native_run_and_same_journal(client, app, monkeypatch):
    state, _ = task(client)
    engine = app.state.engine
    turn_id = "root-resume-protocol"
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (turn_id, state["task_id"], 1, "write once", "queued", now()))
    interrupted = False
    def model(messages, info):
        nonlocal interrupted
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == "write_file"]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("write_file", {"relative_path": "parent.md", "content": "once"}, "parent-write")])
        if not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "恢复根回合", "artifact_ids": [returns[-1].content["artifact_id"]]}, "root-recovered")])
    original = engine.make_agent
    def fixture_agent(*args, **kwargs):
        agent, connection = original(*args, **kwargs)
        run = agent.run
        async def invoke(*a, **kw):
            with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
                return await run(*a, **kw)
        agent.run = invoke
        return agent, connection
    monkeypatch.setattr(engine, "make_agent", fixture_agent)
    with pytest.raises(asyncio.CancelledError):
        await engine.run(state["task_id"], turn_id)
    assert engine.store.task(state["task_id"])["status"] == "paused"
    await engine.run(state["task_id"], turn_id)
    final = engine.store.task(state["task_id"])
    assert final["status"] == "waiting_user" and final["action_count"] == 1 and len(final["artifact_ids"]) == 1
    with engine.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_runs WHERE turn_id=?", (turn_id,)).fetchone()[0] == 2


@pytest.mark.asyncio
async def test_child_total_deadline_stops_inflight_model_not_only_next_request(client, app):
    state, _ = task(client)
    engine = app.state.engine
    with engine.store.transaction() as db:
        current = engine.store.read_task(db, state["task_id"])
        next(r for r in current["config_snapshot"]["role_catalog"]["roles"] if r["role_id"] == "data")["seconds"] = 1
        engine.store.save_task(db, current, current["state_version"])
    handle = prepare(engine, state)
    async def model(messages, info):
        await asyncio.sleep(3)
        pytest.fail("expired model was allowed to finish")
    token = CURRENT_ASSIGNMENT.set(handle["assignment_id"])
    try:
        role = engine.roles.configurations(engine.store.task(state["task_id"]))["data"]
        with pytest.raises(Failure, match="墙钟预算"):
            await RoleProxy(engine, role).run(deps=Deps(engine, state["task_id"], "root-boundary", 1), model=FixtureFunctionModel(model))
        value = engine.roles.read(handle["assignment_id"])
        assert value["status"] == "failed" and value["blocker"]["code"] == "ROLE_TIME_BUDGET_EXHAUSTED"
        assert engine.store.task(state["task_id"])["status"] == "created"
    finally:
        CURRENT_ASSIGNMENT.reset(token)


@pytest.mark.asyncio
async def test_first_parent_delegation_resumes_own_child_checkpoint_without_repeating_write(client, app, monkeypatch):
    state, _ = task(client, objective="PARENT_PRIVATE_DELEGATION_RESTART")
    engine = app.state.engine
    turn_id = "first-parent-delegation"
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (turn_id, state["task_id"], 1, state["objective"], "queued", now()))
    interrupted = False

    def model(messages, info):
        nonlocal interrupted
        returns = [part for message in messages if isinstance(message, ModelRequest)
                   for part in message.parts if isinstance(part, ToolReturnPart)]
        if "委派的权威范围" in (info.instructions or ""):
            assert "PARENT_PRIVATE_DELEGATION_RESTART" not in str(messages) + (info.instructions or "")
            written = [part for part in returns if part.tool_name == "write_file"]
            if not written:
                return ModelResponse(parts=[ToolCallPart("write_file", {"relative_path": "once.md", "content": "one confirmed child write"}, "child-write-once")])
            if not interrupted:
                interrupted = True
                raise asyncio.CancelledError()
            return ModelResponse(parts=[ToolCallPart("final_result", {"status": "succeeded", "kind": "dataset_manifest",
                "summary": "同一个子角色恢复已确认文件", "artifact_ids": [written[0].content["artifact_id"]]}, "child-result")])
        if not any(part.tool_name == "delegate_task" for part in returns):
            request = AssignmentRequest(objective="独立写一次文件，恢复时复用已确认文件")
            return ModelResponse(parts=[ToolCallPart("delegate_task", {"agent_name": "data", "task": request.model_dump_json()}, "first-delegate")])
        assignment = engine.roles.list(state["task_id"])[0]
        assert assignment["status"] == "succeeded"
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "父子检查点恢复完成",
            "artifact_ids": assignment["result"]["artifact_ids"]}, "parent-result")])

    original = engine.make_agent

    def fixture_agent(*args, **kwargs):
        agent, connection = original(*args, **kwargs)
        run = agent.run

        async def invoke(*a, **kw):
            with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
                return await run(*a, **kw)

        agent.run = invoke
        return agent, connection

    monkeypatch.setattr(engine, "make_agent", fixture_agent)
    with pytest.raises(asyncio.CancelledError):
        await engine.run(state["task_id"], turn_id)
    old = engine.roles.list(state["task_id"])[0]
    assert old["status"] == "waiting"
    snapshot = await engine.latest_checkpoint(turn_id)
    assert snapshot and snapshot.state == "interrupted"
    assert [call.tool_call_id for call in engine.open_tool_calls(snapshot.messages)] == ["first-delegate"]
    with engine.store.transaction() as db:
        # Unconfirmed or altered effects cannot grant interrupted replay.
        db.execute("UPDATE actions SET status='pending' WHERE turn_id=? AND kind='role.delegate'", (turn_id,))
    assert await engine.latest_checkpoint(turn_id) is None
    with engine.store.transaction() as db:
        db.execute("UPDATE actions SET status='confirmed' WHERE turn_id=? AND kind='role.delegate'", (turn_id,))
        row = db.execute("SELECT id,args FROM actions WHERE turn_id=? AND kind='role.delegate'", (turn_id,)).fetchone()
        original_args = row["args"]
        changed = {**json.loads(original_args), "agent_name": "quality"}
        db.execute("UPDATE actions SET args=? WHERE id=?", (json.dumps(changed), row["id"]))
    assert await engine.latest_checkpoint(turn_id) is None
    with engine.store.transaction() as db:
        db.execute("UPDATE actions SET args=? WHERE id=?", (original_args, row["id"]))
    paused = engine.store.task(state["task_id"])
    assert client.post(f"/tasks/{state['task_id']}/resume", json={"request_id": "resume-first-delegate",
        "expected_state_version": paused["state_version"]}).status_code == 200
    await engine.run(state["task_id"], turn_id)
    current = engine.roles.list(state["task_id"])
    assert len(current) == 1 and current[0]["status"] == "succeeded"
    assert current[0]["run_id"] == old["run_id"] and current[0]["deadline_at"] == old["deadline_at"]
    assert engine.store.task(state["task_id"])["status"] == "waiting_user"
    with engine.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM actions WHERE turn_id=? AND kind='role.delegate'", (turn_id,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM actions WHERE turn_id=? AND kind='fs.write_workspace'", (old["run_id"],)).fetchone()[0] == 1


def test_task_cancel_settles_waiting_assignment_after_its_job_stops(client, app):
    from test_jobs import ExecutorFixture

    from forestry_agent.jobs import Jobs

    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state, role="code_experiment")
    value = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    child = Deps(engine, state["task_id"], value["run_id"], 1, assignment_id=value["assignment_id"])
    jobs = Jobs(engine.config, engine.store, engine.resources, ExecutorFixture())
    jobs.cancel_assignments = engine.roles.cancel_waiting
    submitted = engine.perform(child, "cancel-code", "code.execute", {}, lambda action: jobs.submit(state["task_id"], 1, action, "print(42)", [], 60))
    engine.roles.stop(value["assignment_id"], "waiting", blocker={"code": "JOB_PENDING", "message": "fixture"})
    engine.store.change(state["task_id"], "fixture.cancel", lambda s, _: s.update(status="paused", cancellation_requested=True))
    engine.finalize_cancel(state["task_id"])
    assert engine.store.task(state["task_id"])["status"] == "paused"
    jobs.tick()
    assert jobs.read(submitted["job"]["job_id"])["status"] == "cancelled"
    assert engine.roles.read(value["assignment_id"])["status"] == "cancelled"
    assert engine.store.task(state["task_id"])["status"] == "cancelled"


def test_amendment_settles_obsolete_idle_child_once_and_keeps_current_assignment(client, app):
    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state)
    old = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (old["run_id"], state["task_id"], 1, "child", "running", now()))
    engine.roles.stop(old["assignment_id"], "waiting", blocker={"code": "SERVICE_RESTARTED"})
    payload = {"request_id": "amend-obsolete", "expected_spec_revision": 1, "objective": "只交付新目标报告"}
    amended = client.post(f"/tasks/{state['task_id']}/amendments", json=payload)
    assert amended.status_code == 200
    assert engine.roles.read(old["assignment_id"])["status"] == "cancelled"
    assert engine.roles.read(old["assignment_id"])["blocker"]["code"] == "SPEC_SUPERSEDED"
    resumed = client.post(f"/tasks/{state['task_id']}/resume", json={"request_id": "resume-current-assignment", "expected_state_version": amended.json()["state_version"]})
    assert resumed.status_code == 200
    current = prepare(engine, resumed.json(), call_id="current-assignment")
    engine.roles.cancel_obsolete(state["task_id"])
    assert engine.roles.read(current["assignment_id"])["status"] == "queued"
    assert client.post(f"/tasks/{state['task_id']}/amendments", json=payload).json() == amended.json()
    with engine.store.transaction() as db:
        assert db.execute("SELECT status FROM turns WHERE id=?", (old["run_id"],)).fetchone()[0] == "cancelled"
        assert db.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='assignment.cancelled'", (state["task_id"],)).fetchone()[0] == 1
    with pytest.raises(Failure, match="修订|控制"):
        engine.roles.start(old)


def test_obsolete_waiting_child_stays_unsettled_until_pending_effect_is_reconciled(client, app):
    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state)
    old = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    with engine.store.transaction() as db:
        db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,NULL)", ("pending-child", state["task_id"], old["run_id"], "pending", 1, "fs.write_workspace", "{}", "pending"))
    engine.roles.stop(old["assignment_id"], "waiting", blocker={"code": "RECONCILIATION_REQUIRED"})
    assert client.post(f"/tasks/{state['task_id']}/amendments", json={"request_id": "amend-pending-child", "expected_spec_revision": 1, "objective": "新修订"}).status_code == 200
    engine.roles.cancel_obsolete(state["task_id"])
    assert engine.roles.read(old["assignment_id"])["status"] == "waiting"
    current_state = engine.store.task(state["task_id"])
    reply = client.post(f"/tasks/{state['task_id']}/resume", json={"request_id": "resume-pending-child", "expected_state_version": current_state["state_version"]})
    assert reply.status_code == 409 and reply.json()["error"]["code"] == "RECONCILIATION_REQUIRED"
    with engine.store.transaction() as db:
        db.execute("UPDATE actions SET status='failed',result=? WHERE id='pending-child'", ('{"kind":"failed"}',))
    engine.roles.cancel_obsolete(state["task_id"])
    assert engine.roles.read(old["assignment_id"])["status"] == "cancelled"


def test_revision_revocation_waits_for_actual_worker_stop_and_settles_after_parent_resumes(client, app):
    from test_jobs import ExecutorFixture

    from forestry_agent.jobs import Jobs

    state, _ = task(client)
    engine = app.state.engine
    handle = prepare(engine, state, role="code_experiment")
    old = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    child = Deps(engine, state["task_id"], old["run_id"], 1, assignment_id=old["assignment_id"])
    fake = ExecutorFixture()
    jobs = Jobs(engine.config, engine.store, engine.resources, fake)
    jobs.settle_obsolete_assignments = engine.roles.cancel_obsolete
    submitted = engine.perform(child, "obsolete-code", "code.execute", {}, lambda action: jobs.submit(state["task_id"], 1, action, "print(42)", [], 60))
    jobs.tick()
    assert fake.container["State"]["Running"]
    engine.roles.stop(old["assignment_id"], "waiting", blocker={"code": "JOB_PENDING"})
    amended = client.post(f"/tasks/{state['task_id']}/amendments", json={"request_id": "revoke-code", "expected_spec_revision": 1,
        "objective": "只核对撤销，不再运行代码", "constraints": {"allowed_capabilities": ["job.observe"]}}).json()
    assert engine.roles.read(old["assignment_id"])["status"] == "waiting"
    resumed = client.post(f"/tasks/{state['task_id']}/resume", json={"request_id": "resume-revoked", "expected_state_version": amended["state_version"]})
    assert resumed.status_code == 200 and resumed.json()["status"] == "created"
    fake.refuse_stop = True
    jobs.tick()
    assert jobs.read(submitted["job"]["job_id"])["status"] == "running"
    assert engine.roles.read(old["assignment_id"])["status"] == "waiting"
    with pytest.raises(Failure), engine.store.transaction() as db:
        engine.roles.authorize(db, child, "code.execute", {})
    fake.refuse_stop = False
    jobs.tick()
    assert jobs.read(submitted["job"]["job_id"])["status"] == "cancelled"
    assert engine.roles.read(old["assignment_id"])["status"] == "cancelled"
    assert engine.store.task(state["task_id"])["cancellation_requested"] is False
