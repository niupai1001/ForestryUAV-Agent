import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.tool_output_limits import LocalFileStore
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.client import collect_events
from forestry_agent.context import input_bound, utf8_window
from forestry_agent.contracts import Failure
from forestry_agent.store import now


def test_evidence_export_follows_every_event_page(client, app):
    state, _ = task(client)
    with app.state.engine.store.transaction() as db:
        for index in range(170):
            app.state.engine.store.event(db, state["task_id"], "fixture.audit", {"index": index})
    events = collect_events(client, state["task_id"])
    fixture_events = [e for e in events if e["type"] == "fixture.audit"]
    assert [e["payload"]["index"] for e in fixture_events] == list(range(170))
    assert len({e["event_id"] for e in events}) == len(events)


@pytest.mark.asyncio
async def test_large_tool_return_is_losslessly_spilled_and_read_back(client, app):
    state, _ = task(client)
    engine = app.state.engine
    turn_id = "spill-readback"
    agent, connection = engine.make_agent("small", turn_id, task_id=state["task_id"])
    full = "ordinary line\n" * 1500 + "DECISIVE: fixture-value-42\n"
    @agent.tool_plain
    def fixture_payload() -> str:
        return full
    observed = {}
    def model(messages, info):
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("fixture_payload", {}, "call-one")])
        if returns[-1].tool_name == "fixture_payload":
            observed["handle"] = returns[-1].metadata["overflow_handle"]
            assert len(str(returns[-1].content)) < 6000
            return ModelResponse(parts=[ToolCallPart("read_tool_result", {
                "handle": observed["handle"], "pattern": "DECISIVE", "limit": 3}, "call-two")])
        observed["readback"] = str(returns[-1].content)
        assert "fixture-value-42" in observed["readback"]
        return ModelResponse(parts=[ToolCallPart("final_result", {
            "summary": "协议夹具读取完成", "artifact_ids": [], "limitations": ["非真实模型"]}, "output")])
    try:
        # Keep the real native loop; skip only local weight identity checks for this fixture model.
        class FixtureFunctionModel(FunctionModel):
            @property
            def system(self):
                return "test"
        function = FixtureFunctionModel(model)
        with agent.override(model=function):
            result = await agent.run("验证外置工具结果按句柄读回", deps=Deps(engine, state["task_id"], turn_id, 1))
        assert result.output.summary == "协议夹具读取完成"
        spill = LocalFileStore(base_dir=engine.config.workspace_root / ".forestry" / "tool-results" / state["task_id"])
        assert (await spill.read(observed["handle"])).decode() == full
        foreign = LocalFileStore(base_dir=engine.config.workspace_root / ".forestry" / "tool-results" / "other-task")
        with pytest.raises((FileNotFoundError, PermissionError)):
            await foreign.read(observed["handle"])
        assert engine.store.task(state["task_id"])["model_requests"] == 3
    finally:
        await connection.aclose()


def test_native_utf8_window_cursors_reconstruct_every_byte_and_bound_tail():
    data = ("这是长单行中文结果" * 3000 + "\nEND_SOURCE_42").encode()
    recovered, cursor = [], 0
    while cursor < len(data):
        window = utf8_window(data, cursor, 3999)
        assert len(window["text"].encode()) <= 3999 and window["next"] > cursor
        recovered.append(window["text"])
        cursor = window["next"]
    assert "".join(recovered).encode() == data
    tail = utf8_window(data, 0, 4000, True)
    assert tail["text"].endswith("END_SOURCE_42") and len(tail["text"].encode()) <= 4000
    with pytest.raises(Failure):
        utf8_window(data, 1)
    with pytest.raises(Failure):
        utf8_window(data, limit=5000)
    with pytest.raises(Failure, match="增大limit"):
        utf8_window("中文".encode(), limit=1, from_end=True)
    with pytest.raises(Failure):
        utf8_window(data, offset=True)


def test_prepared_budget_anchors_measured_tokens_and_counts_new_instructions_and_schemas():
    original = ModelRequest(parts=[UserPromptPart("中文长来源" * 10000)], instructions="old instructions")
    response = ModelResponse(parts=[], usage=RequestUsage(input_tokens=5000, output_tokens=200))
    tail = ModelRequest(parts=[UserPromptPart("新增正文42")], instructions="old instructions")
    messages = [original, response, tail]
    params = SimpleNamespace(instruction_parts=[SimpleNamespace(content="当前约束" * 50)], tool_defs={})
    bound, anchored = input_bound(messages, params, [])
    assert anchored and 5200 < bound < 7000
    assert tail.instructions == "old instructions"  # no mutation of framework history
    new_tool = [{"name": "new", "schema": {"description": "中文新工具" * 50}}]
    with_schema, _ = input_bound(messages, params, new_tool)
    assert with_schema > bound + 500
    response.usage = RequestUsage()
    fallback, anchored = input_bound(messages, params, [])
    assert not anchored and fallback > 100000


@pytest.mark.asyncio
async def test_large_native_line_read_redirects_to_bounded_same_store_reader(client, app):
    state, _ = task(client)
    engine = app.state.engine
    agent, connection = engine.make_agent("small", "native-byte-window", task_id=state["task_id"])
    full = "原生存储中的单行结果" * 4000 + "END_SOURCE_42"
    @agent.tool_plain
    def long_line_fixture() -> str:
        return full
    seen = {}
    def model(messages, info):
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("long_line_fixture", {}, "produce")])
        last = returns[-1]
        if last.tool_name == "long_line_fixture":
            seen["handle"] = last.metadata["overflow_handle"]
            return ModelResponse(parts=[ToolCallPart("read_tool_result", {"handle": seen["handle"]}, "oversized-lines")])
        if last.tool_name == "read_tool_result":
            assert last.content["kind"] == "window_required"
            return ModelResponse(parts=[ToolCallPart("read_tool_result_window", {"handle": seen["handle"], "limit": 1}, "invalid-tiny-window")])
        if last.tool_name == "read_tool_result_window" and last.content.get("kind") == "failed":
            assert last.content["error"]["code"] == "SCHEMA_INVALID"
            return ModelResponse(parts=[ToolCallPart("read_tool_result_window", {"handle": seen["handle"], "from_end": True}, "bounded-tail")])
        assert last.tool_name == "read_tool_result_window" and last.content["text"].endswith("END_SOURCE_42")
        assert len(last.content["text"].encode()) <= 4000 and not last.metadata
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "完整原文保留，按窗口找到来源", "artifact_ids": []}, "verified")])
    class FixtureModel(FunctionModel):
        @property
        def system(self):
            return "test"
    try:
        with agent.override(model=FixtureModel(model)):
            await agent.run("核验中文单行读回与预算", deps=Deps(engine, state["task_id"], "native-byte-window", 1))
        native = LocalFileStore(base_dir=engine.config.workspace_root / ".forestry" / "tool-results" / state["task_id"])
        assert (await native.read(seen["handle"])).decode() == full
        with engine.store.transaction() as db:
            manifests = [json.loads(row[0]) for row in db.execute("SELECT payload FROM contexts WHERE turn_id='native-byte-window'")]
        assert all(m["estimated_tokens_upper_bound"] <= m["context_limit"]-m["output_reserve"] for m in manifests)
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_compaction_preserves_tool_pairing_authoritative_facts_and_private_manifest(client, app):
    state, _ = task(client)
    engine = app.state.engine
    history = [ModelRequest(parts=[UserPromptPart("最初目标：输入只读，保留可核查证据。")])]
    for index in range(24):
        history.extend([ModelResponse(parts=[ToolCallPart("fixture_read", {}, f"call-{index}")]),
            ModelRequest(parts=[ToolReturnPart("fixture_read", "历史内容" * 800, f"call-{index}")])])
    agent, connection = engine.make_agent("small", "long-history", task_id=state["task_id"])
    try:
        with agent.override(model=TestModel(call_tools=[], custom_output_args={
            "summary": "压缩协议夹具", "artifact_ids": [], "limitations": []})):
            await agent.run("继续当前目标", message_history=history,
                deps=Deps(engine, state["task_id"], "long-history", 1))
        with engine.store.transaction() as db:
            manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id='long-history'").fetchone()[0])
        raw = Path(manifest["prepared_input_ref"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == manifest["prepared_input_sha256"]
        body = json.loads(raw)
        assert len(body["messages"]) < len(history)
        assert state["objective"] in "\n".join(body["instructions"])
        assert '"source_policy":"read_only"' in "\n".join(body["instructions"])
        calls, returns = set(), set()
        for message in body["messages"]:
            for part in message["parts"]:
                if part["part_kind"] == "tool-call":
                    calls.add(part["tool_call_id"])
                if part["part_kind"] == "tool-return":
                    returns.add(part["tool_call_id"])
        assert calls == returns
        assert manifest["actual_input_tokens"] is not None
        public = next(e for e in engine.store.events(state["task_id"]) if e["type"] == "context.compiled")["payload"]
        assert "prepared_input_ref" not in public and "messages" not in public
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_oversized_user_prompt_is_rejected_without_silent_rewrite_or_model_call(client, app):
    state, _ = task(client)
    engine = app.state.engine
    agent, connection = engine.make_agent("small", "oversized-prompt")
    try:
        with agent.override(model=TestModel(call_tools=[])), pytest.raises(Failure, match="编译输入保守估算"):
            await agent.run("不能静默改写的约束" * 6000,
                deps=Deps(engine, state["task_id"], "oversized-prompt", 1))
        assert engine.store.task(state["task_id"])["model_requests"] == 0
    finally:
        await connection.aclose()


def test_artifact_windows_preserve_utf8_and_reject_foreign_task(client, app):
    state, _ = task(client)
    foreign, _ = task(client, request_id="foreign-task")
    resources = app.state.engine.resources
    content = "a" * 3999 + "树冠" * 30
    saved = resources.write(state["task_id"], "artifact-window", "result.md", content)
    first = resources.read_artifact(state["task_id"], saved["artifact_id"])
    assert first["next"] == 3999 and first["has_more"]
    second = resources.read_artifact(state["task_id"], saved["artifact_id"], first["next"])
    assert first["text"] + second["text"] == content
    assert not second["has_more"]
    with pytest.raises(Failure, match="不属于当前任务"):
        resources.read_artifact(foreign["task_id"], saved["artifact_id"])
    with pytest.raises(Failure, match="不能为负"):
        resources.read_artifact(state["task_id"], saved["artifact_id"], -1)


@pytest.mark.asyncio
async def test_artifact_pages_reach_native_model_without_another_spill(client, app):
    state, _ = task(client)
    engine = app.state.engine
    content = "来源事实中文🙂\n" * 700
    saved = engine.resources.write(state["task_id"], "bounded-source", "source.txt", content)
    agent, connection = engine.make_agent("small", "direct-artifact-pages", task_id=state["task_id"])
    reconstructed = []
    def model(messages, info):
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts
                   if isinstance(p, ToolReturnPart) and p.tool_name == "read_artifact"]
        if not returns:
            offset = 0
        else:
            last = returns[-1]
            assert not last.metadata or not last.metadata.get("overflow_handle")
            assert last.content["kind"] == "completed"
            assert len(last.content["text"].encode("utf-8")) <= 4000
            reconstructed.append(last.content["text"])
            if not last.content["has_more"]:
                return ModelResponse(parts=[ToolCallPart("final_result", {
                    "summary": "按字节窗口直接读完来源", "artifact_ids": [saved["artifact_id"]]}, "done")])
            offset = last.content["next"]
        return ModelResponse(parts=[ToolCallPart("read_artifact", {
            "artifact_id": saved["artifact_id"], "offset": offset}, f"read-{offset}")])
    class FixtureModel(FunctionModel):
        @property
        def system(self):
            return "test"
    try:
        with agent.override(model=FixtureModel(model)):
            await agent.run("读取整个来源并核对文件", deps=Deps(engine, state["task_id"], "direct-artifact-pages", 1))
        assert "".join(reconstructed) == content
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_action_readback_is_revision_scoped_and_does_not_repeat_effect(client, app):
    state, _ = task(client)
    engine = app.state.engine
    original = Deps(engine, state["task_id"], "original-action", 1)
    engine.perform(original, "read-one", "fs.read", {}, lambda _: {"decisive": "42"})
    with engine.store.transaction() as db:
        action_id = db.execute("SELECT id FROM actions WHERE turn_id='original-action'").fetchone()[0]
    agent, connection = engine.make_agent("small", "recover-action", task_id=state["task_id"])
    observed = []
    def model(messages, info):
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts
                   if isinstance(p, ToolReturnPart) and p.tool_name == "inspect_action"]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("inspect_action", {"action_id": action_id}, "recover")])
        observed.append(returns[-1].content)
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "恢复结果", "artifact_ids": [], "limitations": []}, "final")])
    class FixtureFunctionModel(FunctionModel):
        @property
        def system(self):
            return "test"
    try:
        with agent.override(model=FixtureFunctionModel(model)):
            await agent.run("按动作引用恢复", deps=Deps(engine, state["task_id"], "recover-action", 1))
        assert observed[0]["result"]["decisive"] == "42"
        engine.store.change(state["task_id"], "fixture.revision", lambda s, _: s.update(spec_revision=2))
        with engine.store.transaction() as db:
            db.execute("INSERT INTO specs SELECT task_id,2,payload FROM specs WHERE task_id=? AND revision=1", (state["task_id"],))
        agent2, connection2 = engine.make_agent("small", "reject-old-action", task_id=state["task_id"])
        try:
            with agent2.override(model=FixtureFunctionModel(model)):
                await agent2.run("旧动作不得恢复", deps=Deps(engine, state["task_id"], "reject-old-action", 2))
            assert observed[1]["kind"] == "failed"
            assert observed[1]["error"]["code"] == "PERMISSION_DENIED"
        finally:
            await connection2.aclose()
        with engine.store.transaction() as db:
            assert db.execute("SELECT COUNT(*) FROM actions WHERE kind='fs.read'").fetchone()[0] == 1
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_current_turn_request_survives_native_history_reduction(client, app):
    state, _ = task(client)
    engine = app.state.engine
    current = "现在是第二回合：从第13份开始，结束后必须检索早期来源。"
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", ("current-request", state["task_id"], 1, current, "running", now()))
    agent, connection = engine.make_agent("small", "current-request", task_id=state["task_id"])
    @agent.tool_plain
    def fixture_tick(index: int) -> str:
        return "fixture data " * 150 + str(index)
    calls = 0
    def model(messages, info):
        nonlocal calls
        calls += 1
        if calls <= 13:
            return ModelResponse(parts=[ToolCallPart("fixture_tick", {"index": calls}, f"tick-{calls}")])
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "协议夹具", "artifact_ids": [], "limitations": []}, "final")])
    class FixtureFunctionModel(FunctionModel):
        @property
        def system(self):
            return "test"
    try:
        with agent.override(model=FixtureFunctionModel(model)):
            await agent.run(current, deps=Deps(engine, state["task_id"], "current-request", 1))
        with engine.store.transaction() as db:
            manifests = [json.loads(r[0]) for r in db.execute("SELECT payload FROM contexts WHERE turn_id='current-request' ORDER BY rowid")]
        assert any(m["history_message_count_reduced"] for m in manifests)
        for manifest in manifests:
            body = json.loads(Path(manifest["prepared_input_ref"]).read_text(encoding="utf-8"))
            assert current in "\n".join(body["instructions"])
        # Missing provider usage is not treated as a successful zero-token calibration.
        engine.contexts.measured(manifests[-1]["context_manifest_id"], RequestUsage())
        with engine.store.transaction() as db:
            updated = json.loads(db.execute("SELECT payload FROM contexts WHERE id=?", (manifests[-1]["context_manifest_id"],)).fetchone()[0])
        assert updated["usage_measurement_status"] == "unavailable"
        assert updated["actual_input_tokens"] is None and updated["estimate_covers_observed_usage"] is None
        with pytest.raises(Failure, match="实测输入用量超过"):
            engine.contexts.measured(manifests[-1]["context_manifest_id"], RequestUsage(input_tokens=100000))
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_continuous_stream_cannot_extend_model_wall_clock_budget(client, app):
    state, _ = task(client)
    engine = app.state.engine
    snapshot = dict(state["config_snapshot"])
    snapshot["generation"] = {"max_model_request_seconds": 1}
    agent, connection = engine.make_agent("small", "slow-stream", snapshot, state["task_id"])
    deps = Deps(engine, state["task_id"], "slow-stream", 1)
    closed = False
    async def stream_model(messages, info):
        nonlocal closed
        try:
            for _ in range(100):
                yield "stream fixture "
                await asyncio.sleep(0.05)
        finally:
            closed = True
    class FixtureFunctionModel(FunctionModel):
        @property
        def system(self):
            return "test"
    async def events(ctx, stream):
        await engine.model_events(deps, stream)
    try:
        with agent.override(model=FixtureFunctionModel(stream_function=stream_model)), pytest.raises(Failure, match="墙钟时间预算"):
            await asyncio.wait_for(agent.run("验证单次流式总时限", deps=deps, event_stream_handler=events), timeout=3)
        assert closed
        assert engine.store.task(state["task_id"])["model_requests"] == 1
    finally:
        await connection.aclose()
