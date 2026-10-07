import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai_harness.planning import PlanItem, SqlitePlanStore
from test_core import task
from test_roles import FixtureFunctionModel, prepare, stream_adapter

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.planning import ScopedPlanStore
from forestry_agent.store import encode, now


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,scope,offered", [("H0", None, False), ("H1", [], False),
    ("H1", ["task.plan"], True), ("H2", ["task.plan"], True)])
async def test_native_plan_scope_persistence_and_actual_input_reminder(client, app, mode, scope, offered):
    state, _ = task(client, harness_mode=mode, constraints={"allowed_capabilities": scope})
    engine = app.state.engine
    turn_id = "native-planning-fixture"
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (turn_id, state["task_id"], 1, state["objective"], "running", now()))
    requests = []
    def model(messages, info):
        names = {t.name for t in info.function_tools}
        assert {"write_plan", "read_plan"} <= names if offered else not ({"write_plan", "read_plan"} & names)
        requests.append(messages)
        if offered and len(requests) == 1:
            return ModelResponse(parts=[ToolCallPart("write_plan", {"items":[{
                "id":"evidence", "content":"PLAN_FIXTURE完成仅为自报", "status":"completed"}]}, "plan-once")])
        if offered:
            assert "<plan-reminder>" in str(messages) and "PLAN_FIXTURE" in str(messages)
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary":"计划协议夹具", "artifact_ids":[], "limitations":[]}, "answer")])
    agent, connection = engine.make_agent("small", turn_id, state["config_snapshot"], state["task_id"])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            result = await agent.run("保存计划并结束本协议夹具", deps=Deps(engine, state["task_id"], turn_id, 1))
        assert result.output.summary == "计划协议夹具"
    finally:
        await connection.aclose()
    value = client.get(f"/tasks/{state['task_id']}/plan").json()
    assert value["authority"] == "model_proposed" and value["acceptance"] == "not_assessed"
    assert len(value["items"]) == int(offered)
    assert engine.store.task(state["task_id"])["status"] == "created"  # No implicit acceptance/terminal commit.
    if offered:
        with engine.store.transaction() as db:
            rows = db.execute("SELECT payload FROM contexts WHERE turn_id=? ORDER BY rowid", (turn_id,)).fetchall()
            actions = db.execute("SELECT * FROM actions WHERE task_id=? AND kind='task.plan'", (state["task_id"],)).fetchall()
        body = json.loads(Path(json.loads(rows[-1][0])["prepared_input_ref"]).read_bytes())
        assert "<plan-reminder>" in str(body["messages"])  # Counted and privately snapshotted after native injection.
        assert "<plan-reminder>" not in str(result.all_messages())  # Ephemeral reminders do not accumulate in history.
        assert len(actions) == 1 and actions[0]["status"] == "confirmed"
        assert json.loads(actions[0]["result"])["authority"] == "model_proposed"
        native = SqlitePlanStore(str(engine.store.path), table="native_plan_items",
            session=encode([state["task_id"], 1, turn_id, None]))
        assert (await native.get_items())[0].id == "evidence"
        assert await ScopedPlanStore(engine, state["task_id"], 2, turn_id).get_items() == []
        assert await ScopedPlanStore(engine, state["task_id"], 1, "other-turn").get_items() == []
        assert await ScopedPlanStore(engine, state["task_id"], 1, turn_id, "other-assignment").get_items() == []
        foreign, _ = task(client, request_id="foreign-plan")
        assert client.get(f"/tasks/{foreign['task_id']}/plan").json()["items"] == []
    assert client.get("/tasks/unknown-task/plan").status_code == 404


@pytest.mark.asyncio
async def test_plan_mutation_bounds_control_and_current_revision(client, app):
    state, _ = task(client, constraints={"allowed_capabilities":["task.plan"]})
    engine = app.state.engine
    plans = ScopedPlanStore(engine, state["task_id"], 1, "control-plan")
    original = PlanItem(id="original", content="待实际执行", status="pending")
    await plans.set_items([original])
    for invalid in [[PlanItem(id=str(n), content="x") for n in range(17)],
        [PlanItem(content="x"*501)], [PlanItem(content="中文"*250) for _ in range(8)],
        [PlanItem(content="a", status="in_progress"), PlanItem(content="b", status="in_progress")]]:
        with pytest.raises(Failure, match="计划最多"):
            await plans.set_items(invalid)
        assert await plans.get_items() == [original]
    engine.store.change(state["task_id"], "fixture.pause", lambda s, _: s.update(pause_requested=True, status="paused"))
    with pytest.raises(Failure, match="控制"):
        await plans.set_items([])
    assert await plans.get_items() == [original]
    engine.store.change(state["task_id"], "fixture.resume", lambda s, _: s.update(pause_requested=False))
    assert client.post(f"/tasks/{state['task_id']}/amendments", json={"request_id":"disable-planning",
        "expected_spec_revision":1, "objective":state["objective"], "constraints":{"allowed_capabilities":[]}}).status_code == 200
    with pytest.raises(Failure, match="控制"):
        await plans.set_items([])
    engine.store.change(state["task_id"], "fixture.resume", lambda s, _: s.update(pause_requested=False))
    with pytest.raises(Failure, match="允许范围"):
        await ScopedPlanStore(engine, state["task_id"], 2, "control-plan").set_items([original])


def test_plans_do_not_reset_no_progress_or_bypass_action_permissions(client, app):
    state, _ = task(client, constraints={"allowed_capabilities":[]})
    engine = app.state.engine
    deps = Deps(engine, state["task_id"], "planning-only-loop", 1)
    with pytest.raises(Failure, match="允许范围"):
        engine.perform(deps, "unauthorized", "task.plan", {}, lambda _: {"native_result":"completed"})
    for n in range(3):
        if n < 2:
            engine.progress.record(deps, str(n), "write_plan", {}, {"native_result":f"new model claim {n}"})
        else:
            with pytest.raises(Failure, match="连续3"):
                engine.progress.record(deps, str(n), "read_plan", {}, {"native_result":"completed"})


@pytest.mark.asyncio
async def test_live_native_reminder_survives_compaction_without_accumulating(client, app):
    state, _ = task(client)
    engine, turn_id = app.state.engine, "planning-after-compaction"
    await ScopedPlanStore(engine, state["task_id"], 1, turn_id).set_items([
        PlanItem(id="live", content="LIVE_PLAN_PENDING", status="pending")])
    history = [ModelRequest(parts=[UserPromptPart("历史任务，只读源文件")])]
    for index in range(24):
        history.extend([ModelResponse(parts=[ToolCallPart("fixture_read", {}, f"old-{index}")]),
            ModelRequest(parts=[ToolReturnPart("fixture_read", "历史内容" * 800, f"old-{index}")])])
    requests = []
    def model(messages, info):
        reminders = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts
            if isinstance(p, UserPromptPart) and "<plan-reminder>" in str(p.content)]
        assert len(reminders) == 1
        assert len(messages) < len(history)
        requests.append(messages)
        if len(requests) == 1:
            assert "LIVE_PLAN_PENDING" in str(reminders[0].content)
            return ModelResponse(parts=[ToolCallPart("write_plan", {"items":[{
                "id":"live", "content":"LIVE_PLAN_CHANGED", "status":"completed"}]}, "update-live")])
        assert "LIVE_PLAN_CHANGED" in str(reminders[0].content)
        assert "LIVE_PLAN_PENDING" not in str(reminders[0].content)
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary":"压缩后仍收到当前计划", "artifact_ids":[]}, "done")])
    agent, connection = engine.make_agent("small", turn_id, state["config_snapshot"], state["task_id"])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            result = await agent.run("继续当前任务", message_history=history,
                deps=Deps(engine, state["task_id"], turn_id, 1))
        assert len(requests) == 2 and "<plan-reminder>" not in str(result.all_messages())
        with engine.store.transaction() as db:
            manifests = [json.loads(r[0]) for r in db.execute("SELECT payload FROM contexts WHERE turn_id=? ORDER BY rowid", (turn_id,))]
        for manifest in manifests:
            body = json.loads(Path(manifest["prepared_input_ref"]).read_bytes())
            prompts = [p for m in body["messages"] for p in m.get("parts", [])
                if p.get("part_kind") == "user-prompt" and "<plan-reminder>" in str(p.get("content"))]
            assert len(prompts) == 1
            assert manifest["estimated_tokens_upper_bound"] <= manifest["context_limit"]-manifest["output_reserve"]
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_child_plan_mutation_is_isolated_and_wrong_run_cannot_commit(client, app):
    state, _ = task(client)
    engine = app.state.engine
    root = ScopedPlanStore(engine, state["task_id"], 1, "root-boundary")
    await root.set_items([PlanItem(id="root", content="PARENT_PRIVATE_PLAN")])
    handle = prepare(engine, state)
    child = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    scoped = ScopedPlanStore(engine, state["task_id"], 1, child["run_id"], child["assignment_id"])
    assert await scoped.get_items() == []
    await scoped.set_items([PlanItem(id="child", content="独立子计划")])
    assert (await root.get_items())[0].content == "PARENT_PRIVATE_PLAN"
    invalid = ScopedPlanStore(engine, state["task_id"], 1, "wrong-run", child["assignment_id"])
    with pytest.raises(Failure, match="当前委派"):
        await invalid.set_items([PlanItem(content="覆盖父计划")])
    assert await invalid.get_items() == [] and (await scoped.get_items())[0].id == "child"
