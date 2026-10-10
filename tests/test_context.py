import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    CachePoint,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.tool_output_limits import LocalFileStore
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.client import collect_events
from forestry_agent.context import TASK_FACT_PREFIX, deduplicate_objective, input_bound, utf8_window
from forestry_agent.contracts import Failure
from forestry_agent.store import encode, now


def test_evidence_export_follows_every_event_page(client, app):
    state, _ = task(client)
    with app.state.engine.store.transaction() as db:
        for index in range(170):
            app.state.engine.store.event(db, state["task_id"], "fixture.audit", {"index": index})
    events = collect_events(client, state["task_id"])
    fixture_events = [e for e in events if e["type"] == "fixture.audit"]
    assert [e["payload"]["index"] for e in fixture_events] == list(range(170))
    assert len({e["event_id"] for e in events}) == len(events)


def test_objective_reference_is_request_only_exact_and_keeps_cache_and_tool_content():
    objective = "完整约束🙂\n指定方法保持不变。"
    cache = CachePoint()
    initial = ModelRequest(parts=[UserPromptPart([objective, cache]),
        UserPromptPart(objective+"另有后续要求"), ToolReturnPart("read_document", objective, "source")])
    history = [initial]
    request = SimpleNamespace(messages=list(history))
    deduplicate_objective(request, objective)
    assert initial.parts[0].content == [objective, cache]
    assert request.messages[0] is not initial
    assert request.messages[0].parts[0].content[-1] is cache
    assert "完整原始目标见" in request.messages[0].parts[0].content[0]
    assert request.messages[0].parts[1] is initial.parts[1]
    assert request.messages[0].parts[2] is initial.parts[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["H0", "H1", "H2"])
async def test_exact_objective_is_complete_once_in_actual_enhanced_request_and_retained_in_history(client, app, mode):
    objective = "只读来源，不改变指定科学方法。🙂\n原始用户要求必须完整保留。"
    state, _ = task(client, objective=objective, harness_mode=mode)
    engine, turn_id = app.state.engine, "canonical-objective"
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (turn_id, state["task_id"], 1, objective, "running", now()))
    agent, connection = engine.make_agent("small", turn_id, state["config_snapshot"], state["task_id"])
    try:
        with agent.override(model=TestModel(call_tools=[], custom_output_args={"summary":"目标完整保留夹具", "artifact_ids":[]})):
            result = await agent.run(objective, deps=Deps(engine, state["task_id"], turn_id, 1))
        assert any(isinstance(p, UserPromptPart) and p.content == objective
            for m in result.all_messages() if isinstance(m, ModelRequest) for p in m.parts)
        with engine.store.transaction() as db:
            manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id=?", (turn_id,)).fetchone()[0])
        body = json.loads(Path(manifest["prepared_input_ref"]).read_bytes())
        prompt_parts = [p for m in body["messages"] for p in m.get("parts", []) if p.get("part_kind") == "user-prompt"]
        if mode == "H0":
            assert any(p["content"] == objective for p in prompt_parts)
        else:
            assert all(objective not in str(p["content"]) for p in prompt_parts)
            facts = next(json.loads(text.split("：",1)[1].split("\n",1)[0]) for text in body["instructions"] if text.startswith("当前权威任务事实"))
            assert facts["objective"] == objective and facts["current_turn_user_request_is_objective"] is True
            assert "\n".join(body["instructions"]).count(encode(objective)) == 1
    finally:
        await connection.aclose()


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
    signature = hashlib.sha256(encode(new_tool).encode()).hexdigest()
    matched, _ = input_bound(messages, params, new_tool, measured_tools_sha256=signature)
    assert with_schema-matched == len(encode(new_tool).encode())
    changed, _ = input_bound(messages, params, [{"name":"changed"}], measured_tools_sha256=signature)
    ordinary, _ = input_bound(messages, params, [{"name":"changed"}])
    assert changed == ordinary  # An old schema receipt cannot discount a changed tool.
    response.usage = RequestUsage()
    fallback, anchored = input_bound(messages, params, [])
    assert not anchored and fallback > 100000
    no_usage, _ = input_bound(messages, params, new_tool, measured_tools_sha256=signature)
    unbound, _ = input_bound(messages, params, new_tool)
    assert no_usage == unbound


def test_schema_discount_requires_exact_response_schema_task_revision_and_role(client, app):
    state, _ = task(client)
    foreign, _ = task(client, request_id="foreign-schema")
    engine = app.state.engine
    deps = Deps(engine, state["task_id"], "schema-receipt", 1)
    tool = SimpleNamespace(name="fixture", description="Bound schema fixture",
        parameters_json_schema={"type":"object", "description":"诊断记录"*100})
    params = SimpleNamespace(instruction_parts=[SimpleNamespace(content="fixed instructions", dynamic=False)],
        function_tools=[tool], output_tools=[], tool_defs={})
    first = ModelRequest(parts=[UserPromptPart("read")], instructions="fixed instructions")
    request = SimpleNamespace(messages=[first], model_request_parameters=params, model_settings={})
    profile = engine.config.models["small"]
    manifest_id = engine.contexts.compile(deps, request, profile)
    response = ModelResponse(parts=[], usage=RequestUsage(input_tokens=500, output_tokens=20))
    engine.contexts.measured(manifest_id, response.usage, response=response)
    # Native bookkeeping may be added after hooks without changing paid content.
    response.run_id = "new-bookkeeping-id"
    response.metadata = {"receipt":"framework bookkeeping"}
    request.messages = [first, response, ModelRequest(parts=[UserPromptPart("continue")], instructions="fixed instructions")]
    def compile_for(current_deps=deps):
        ref = engine.contexts.compile(current_deps, request, profile)
        with engine.store.transaction() as db:
            return json.loads(db.execute("SELECT payload FROM contexts WHERE id=?", (ref,)).fetchone()[0])
    paid = compile_for()
    assert paid["schema_cost_accounting"] == "bound_measured_schema"
    assert paid["schema_receipt_context_ref"] == manifest_id
    assert paid["schema_receipt_turn_id"] == deps.turn_id
    assert compile_for(Deps(engine, foreign["task_id"], deps.turn_id, 1))["schema_cost_accounting"] == "full_current_schema"
    continued = compile_for(Deps(engine, state["task_id"], "other-turn", 1))
    assert continued["schema_cost_accounting"] == "bound_measured_schema"
    assert continued["schema_receipt_context_ref"] == manifest_id
    assert continued["schema_receipt_turn_id"] == deps.turn_id
    assert continued["estimated_tokens_upper_bound"] == paid["estimated_tokens_upper_bound"]
    params.function_tools = [SimpleNamespace(name="changed", description=tool.description, parameters_json_schema=tool.parameters_json_schema)]
    changed = compile_for()
    assert changed["schema_cost_accounting"] == "full_current_schema" and changed["estimated_tokens_upper_bound"] > paid["estimated_tokens_upper_bound"]
    params.function_tools = [tool]
    request.messages[1] = replace(response, parts=[ToolCallPart("fixture", {}, "different-provider-content")])
    assert compile_for()["schema_cost_accounting"] == "full_current_schema"
    request.messages[1] = response
    engine.store.change(state["task_id"], "fixture.new-revision", lambda s, _: s.update(spec_revision=2))
    assert compile_for(Deps(engine, state["task_id"], deps.turn_id, 2))["schema_cost_accounting"] == "full_current_schema"
    engine.store.change(state["task_id"], "fixture.restore-revision", lambda s, _: s.update(spec_revision=1))
    with engine.store.transaction() as db:
        old = json.loads(db.execute("SELECT payload FROM contexts WHERE id=?", (manifest_id,)).fetchone()[0])
        old["assignment_id"] = "isolated-role"
        db.execute("UPDATE contexts SET payload=? WHERE id=?", (encode(old),manifest_id))
    assert compile_for()["schema_cost_accounting"] == "full_current_schema"
    with engine.store.transaction() as db:
        old["assignment_id"] = None
        old.pop("response_sha256")  # Legacy or unbound accounting must retain the full margin.
        db.execute("UPDATE contexts SET payload=? WHERE id=?", (encode(old),manifest_id))
    assert compile_for()["schema_cost_accounting"] == "full_current_schema"


def test_changed_request_components_keep_paid_usage_and_charge_full_new_occurrences():
    first = ModelRequest(parts=[UserPromptPart("source")], instructions="paid instructions")
    response = ModelResponse(parts=[], usage=RequestUsage(input_tokens=4000, output_tokens=30))
    tail = ModelRequest(parts=[UserPromptPart("continue")], instructions="paid instructions")
    old_tool = {"name":"old", "schema":{"description":"unchanged"*500}}
    changed_tool = {"name":"new", "schema":{"description":"changed"*20}}
    params = SimpleNamespace(instruction_parts=[SimpleNamespace(content=p) for p in ["paid instructions", "new constraint", "new constraint"]], tool_defs={})
    tools = [old_tool, changed_tool]
    base, _ = input_bound([first,response,tail], params, tools, measured_tools=[old_tool],
        measured_instructions=["paid instructions", "new constraint"])
    # A second occurrence is not free; changed schemas pay their whole current
    # definition, without subtracting any old provider cost or compacted text.
    duplicate, _ = input_bound([first,response,tail], params, tools, measured_tools=[old_tool],
        measured_instructions=["paid instructions"])
    assert duplicate-base == len(b"new constraint")
    assert base > 4030 + len(encode([changed_tool]).encode())
    assert tail.instructions == "paid instructions"
    without_receipt, _ = input_bound([first,response,tail], params, tools)
    assert without_receipt > base + 4000


def test_canonical_task_fact_accounting_keeps_complete_objective_and_pays_changed_or_duplicate_fields():
    objective = '完整目标及所有验收要求'*5000
    paid = TASK_FACT_PREFIX+encode({'objective':objective,'counter':1})+'\n固定指导\n'
    current = TASK_FACT_PREFIX+encode({'objective':objective,'counter':2})+'\n固定指导\n新增指导\n'
    history = [ModelRequest(parts=[UserPromptPart('continue')],instructions=paid),
        ModelResponse(parts=[],usage=RequestUsage(input_tokens=5000,output_tokens=30)),ModelRequest(parts=[])]
    params = SimpleNamespace(instruction_parts=[SimpleNamespace(content=current)],tool_defs={})
    bound,_ = input_bound(history,params,[],measured_instructions=[paid])
    assert 5030 < bound < 6000
    assert params.instruction_parts[0].content == current and history[0].instructions == paid
    changed = TASK_FACT_PREFIX+encode({'objective':objective+'增加要求','counter':2})+'\n固定指导\n'
    params.instruction_parts = [SimpleNamespace(content=changed)]
    new_objective,_ = input_bound(history,params,[],measured_instructions=[paid])
    assert new_objective > len((objective+'增加要求').encode())+5030
    params.instruction_parts = [SimpleNamespace(content=current),SimpleNamespace(content=current)]
    duplicate,_ = input_bound(history,params,[],measured_instructions=[paid])
    assert duplicate > len(objective.encode())+5030
    # Similar source-like text is not a canonical facts envelope.
    params.instruction_parts = [SimpleNamespace(content=TASK_FACT_PREFIX+json.dumps({'objective':objective,'counter':2},ensure_ascii=False))]
    noncanonical,_ = input_bound(history,params,[],measured_instructions=[paid])
    assert noncanonical > len(objective.encode())+5030


def test_changed_schema_and_dynamic_instructions_fit_original_budget_with_verified_receipt(client, app):
    state, _ = task(client)
    engine = app.state.engine
    deps = Deps(engine, state["task_id"], "component-budget", 1)
    profile = engine.config.models["small"].model_copy(update={"context_limit":18000, "output_limit":2000})
    stable = SimpleNamespace(name="stable", description="already measured",
        parameters_json_schema={"description":"固定定义"*500})
    evolving = SimpleNamespace(name="evolving", description="old", parameters_json_schema={"type":"object"})
    paid_instructions = ["固定完整约束"*400, "旧任务事实"]
    params = SimpleNamespace(instruction_parts=[SimpleNamespace(content=p, dynamic=False) for p in paid_instructions],
        function_tools=[stable,evolving], output_tools=[], tool_defs={})
    first = ModelRequest(parts=[UserPromptPart("complete objective")], instructions="\n".join(paid_instructions))
    request = SimpleNamespace(messages=[first], model_request_parameters=params, model_settings={})
    receipt = engine.contexts.compile(deps, request, profile)
    response = ModelResponse(parts=[], usage=RequestUsage(input_tokens=4500, output_tokens=30))
    engine.contexts.measured(receipt, response.usage, response=response)
    evolving.description = "new parameter guidance"
    evolving.parameters_json_schema = {"type":"object", "properties":{"refresh":{"type":"boolean"}}}
    params.instruction_parts[-1] = SimpleNamespace(content="新任务事实与原目标", dynamic=True)
    request.messages = [first,response,ModelRequest(parts=[UserPromptPart("continue original task")])]
    tools = [{"name":t.name,"description":t.description,"schema":t.parameters_json_schema} for t in params.function_tools]
    legacy, _ = input_bound(request.messages, params, tools)
    assert legacy > profile.context_limit-profile.output_limit
    ref = engine.contexts.compile(deps, request, profile)
    with engine.store.transaction() as db:
        manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE id=?", (ref,)).fetchone()[0])
        original = json.loads(db.execute("SELECT payload FROM contexts WHERE id=?", (receipt,)).fetchone()[0])
    assert manifest["estimated_tokens_upper_bound"] < profile.context_limit-profile.output_limit
    assert manifest["estimated_tokens_upper_bound"] > response.usage.input_tokens+response.usage.output_tokens
    assert manifest["schema_cost_accounting"] == "bound_measured_components"
    assert manifest["unpaid_tool_names"] == ["evolving"]
    assert manifest["schema_receipt_context_ref"] == receipt
    assert request.messages[0].instructions == "\n".join(paid_instructions)
    snapshot = Path(original["prepared_input_ref"])
    data = snapshot.read_bytes()
    try:
        snapshot.write_bytes(data+b" ")  # Valid JSON, invalid byte provenance.
        with pytest.raises(Failure, match="编译输入保守估算"):
            engine.contexts.compile(deps, request, profile)
    finally:
        snapshot.write_bytes(data)


def test_compacted_history_without_response_retains_verified_prior_usage_and_pays_all_current_messages(client,app):
    state,_ = task(client)
    engine = app.state.engine
    deps = Deps(engine,state['task_id'],'response-free-budget',1)
    profile = engine.config.models['small'].model_copy(update={'context_limit':18000,'output_limit':2000})
    tool = SimpleNamespace(name='fixture',description='stable definition',parameters_json_schema={'description':'定义'*1000})
    params = SimpleNamespace(instruction_parts=[SimpleNamespace(content='完整固定约束'*350,dynamic=False)],
        function_tools=[tool],output_tools=[],tool_defs={})
    request = SimpleNamespace(messages=[ModelRequest(parts=[UserPromptPart('first')])],
        model_request_parameters=params,model_settings={})
    receipt = engine.contexts.compile(deps,request,profile)
    response = ModelResponse(parts=[],usage=RequestUsage(input_tokens=4500,output_tokens=30))
    engine.contexts.measured(receipt,response.usage,response=response)
    request.messages = [ModelRequest(parts=[UserPromptPart('new message '*650)])]
    tools = [{'name':tool.name,'description':tool.description,'schema':tool.parameters_json_schema}]
    raw,_ = input_bound(request.messages,params,tools)
    assert raw > profile.context_limit-profile.output_limit
    ref = engine.contexts.compile(deps,request,profile)
    with engine.store.transaction() as db:
        manifest = json.loads(db.execute('SELECT payload FROM contexts WHERE id=?',(ref,)).fetchone()[0])
        original = json.loads(db.execute('SELECT payload FROM contexts WHERE id=?',(receipt,)).fetchone()[0])
    assert manifest['uses_provider_usage_anchor'] is False
    assert manifest['usage_baseline_mode'] == 'verified_prior_request'
    assert manifest['usage_baseline_tokens'] == [4500,30]
    assert manifest['estimated_tokens_upper_bound'] > 4530+len(('new message '*650).encode())
    assert manifest['estimated_tokens_upper_bound'] < profile.context_limit-profile.output_limit
    assert manifest['schema_receipt_context_ref'] == receipt
    foreign,_ = task(client,request_id='foreign-response-free')
    with pytest.raises(Failure,match='编译输入保守估算'):
        engine.contexts.compile(Deps(engine,foreign['task_id'],deps.turn_id,1),request,profile)
    snapshot = Path(original['prepared_input_ref'])
    saved = snapshot.read_bytes()
    try:
        snapshot.write_bytes(saved+b' ')
        with pytest.raises(Failure,match='编译输入保守估算'):
            engine.contexts.compile(deps,request,profile)
    finally:
        snapshot.write_bytes(saved)


def test_approval_continuation_keeps_budget_without_charging_paid_schema_twice(client, app):
    state, _ = task(client)
    engine = app.state.engine
    profile = engine.config.models["small"].model_copy(update={"context_limit":8500, "output_limit":1000})
    tool = SimpleNamespace(name="fixture", description="Approval continuation fixture",
        parameters_json_schema={"type":"object", "description":"诊断记录"*450})
    params = SimpleNamespace(instruction_parts=[SimpleNamespace(content="完整目标与权限保留", dynamic=False)],
        function_tools=[tool], output_tools=[], tool_defs={})
    first = ModelRequest(parts=[UserPromptPart("目标：读取源文件并报告，不修改输入")], instructions="完整目标与权限保留")
    request = SimpleNamespace(messages=[first], model_request_parameters=params, model_settings={})
    original = Deps(engine, state["task_id"], "awaiting-approval", 1)
    ref = engine.contexts.compile(original, request, profile)
    response = ModelResponse(parts=[ToolCallPart("fixture", {}, "approved-operation")],
        usage=RequestUsage(input_tokens=5000, output_tokens=20))
    engine.contexts.measured(ref, response.usage, response=response)
    request.messages = [first, response, ModelRequest(parts=[ToolReturnPart("fixture", "审批已批准", "approved-operation")],
        instructions=first.instructions)]
    tools = [{"name":tool.name,"description":tool.description,"schema":tool.parameters_json_schema}]
    unbound, _ = input_bound(request.messages, params, tools)
    assert unbound > profile.context_limit-profile.output_limit
    continued = Deps(engine, state["task_id"], "resume-approved-operation", 1)
    compiled = engine.contexts.compile(continued, request, profile)
    with engine.store.transaction() as db:
        value = json.loads(db.execute("SELECT payload FROM contexts WHERE id=?", (compiled,)).fetchone()[0])
    assert value["estimated_tokens_upper_bound"] <= profile.context_limit-profile.output_limit
    assert value["context_limit"] == 8500 and value["output_reserve"] == 1000
    assert value["schema_receipt_context_ref"] == ref and value["schema_receipt_turn_id"] == original.turn_id
    assert request.messages[0] is first and request.messages[1] is response
    # Without a bound, covered measurement the same input must still stop.
    with engine.store.transaction() as db:
        old = json.loads(db.execute("SELECT payload FROM contexts WHERE id=?", (ref,)).fetchone()[0])
        old["estimate_covers_observed_usage"] = False
        db.execute("UPDATE contexts SET payload=? WHERE id=?", (encode(old),ref))
    with pytest.raises(Failure, match="超过预留输出"):
        engine.contexts.compile(continued, request, profile)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["H0", "H1"])
async def test_native_history_continuation_binds_prior_turn_schema_receipt(client, app, mode):
    objective = "输入只读，保留全部目标与真实证据。"
    state, _ = task(client, objective=objective, harness_mode=mode)
    engine = app.state.engine
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", ("prior-native-turn",state["task_id"],1,objective,"completed",now()))
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", ("continued-native-turn",state["task_id"],1,"继续原任务","running",now()))
    history = None
    for turn, prompt in [("prior-native-turn",objective), ("continued-native-turn","继续原任务")]:
        agent, connection = engine.make_agent("small",turn,state["config_snapshot"],state["task_id"])
        try:
            with agent.override(model=TestModel(call_tools=[], custom_output_args={"summary":"原生历史续接夹具", "artifact_ids":[]})):
                result = await agent.run(prompt, deps=Deps(engine,state["task_id"],turn,1), message_history=history)
            history = result.all_messages()
        finally:
            await connection.aclose()
    with engine.store.transaction() as db:
        first = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id='prior-native-turn'").fetchone()[0])
        continued = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id='continued-native-turn'").fetchone()[0])
    assert first["actual_input_tokens"] > 0 and first["estimate_covers_observed_usage"] is True
    assert continued["schema_cost_accounting"] == "bound_measured_schema"
    assert continued["schema_receipt_context_ref"] == first["context_manifest_id"]
    assert continued["schema_receipt_turn_id"] == "prior-native-turn"
    assert continued["tool_schema_sha256"] == first["tool_schema_sha256"]
    assert any(isinstance(p,UserPromptPart) and p.content==objective for m in history if isinstance(m,ModelRequest) for p in m.parts)


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
