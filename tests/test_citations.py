import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from test_core import task
from test_network import HTTPFixture, read

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.resources import native_path
from forestry_agent.roles import AssignmentRequest


def source(client, app):
    state, _ = task(client, context_classification="public")
    app.state.engine.network.http = HTTPFixture()
    result = read(app.state.engine, state)
    return state, result


@pytest.mark.asyncio
async def test_native_write_copies_full_citation_and_reads_actual_file(client, app):
    state, result = source(client, app)
    engine = app.state.engine
    agent, connection = engine.make_agent("small", "cited-write", task_id=state["task_id"])
    outputs = {}
    def model(messages, info):
        returns = [part for message in messages if isinstance(message, ModelRequest) for part in message.parts
                   if isinstance(part, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("inspect_action", {"action_id": result["action_id"], "view": "citation"}, "metadata")])
        last = returns[-1]
        if last.tool_name == "inspect_action":
            assert last.content["citation"] == result["citation"] and "text" not in last.content
            return ModelResponse(parts=[ToolCallPart("write_file", {"relative_path": "facts.json", "content": '{"value":42}',
                "citation_actions": {"citation": result["action_id"]}}, "write")])
        if last.tool_name == "write_file":
            assert last.content["kind"] == "completed"
            outputs["id"] = last.content["artifact_id"]
            return ModelResponse(parts=[ToolCallPart("read_artifact", {"artifact_id": outputs["id"]}, "read-back")])
        assert json.loads(last.content["text"]) == {"value": 42, "citation": result["citation"]}
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "来源逐字段匹配", "artifact_ids": [outputs["id"]]}, "final")])
    class FixtureModel(FunctionModel):
        @property
        def system(self):
            return "test"
    try:
        with agent.override(model=FixtureModel(model)):
            await agent.run("保存真实引用", deps=Deps(engine, state["task_id"], "cited-write", 1))
        artifact = engine.resources.artifact(outputs["id"], state["task_id"])
        assert json.loads(native_path(Path(artifact["locator"])).read_text(encoding="utf-8"))["citation"] == result["citation"]
    finally:
        await connection.aclose()


def test_citation_render_does_not_change_facts_or_mislabel_verification(client, app):
    state, result = source(client, app)
    engine = app.state.engine
    deps = Deps(engine, state["task_id"], "render", 1)
    bindings = {"citation": result["action_id"]}
    text = engine.cited_content(deps, "report.md", "# 报告\n实际观测为42，示例未运行。", bindings)
    assert text.startswith("# 报告\n实际观测为42，示例未运行。")
    assert result["citation"]["source_sha256"] in text and result["citation"]["snapshot_artifact_id"] in text
    assert "scientific_accuracy" not in text and "externally_validated" not in text
    for name, content, mapping in [("file.py", "print(42)", bindings), ("facts.json", "[]", bindings),
            ("facts.json", '{"citation":{"url":"invented"}}', bindings), ("facts.json", "{}", {"bad-key": result["action_id"]})]:
        with pytest.raises(Failure):
            engine.cited_content(deps, name, content, mapping)


def test_cross_task_stale_failed_and_tampered_citations_are_rejected(client, app):
    state, result = source(client, app)
    engine = app.state.engine
    second, _ = task(client, request_id="another-task", context_classification="public")
    with pytest.raises(Failure, match="本任务"):
        engine.cited_content(Deps(engine, second["task_id"], "cross", 1), "facts.json", "{}", {"citation": result["action_id"]})
    with pytest.raises(Failure, match="规范"):
        engine.cited_content(Deps(engine, state["task_id"], "stale", 2), "facts.json", "{}", {"citation": result["action_id"]})
    failed = engine.perform(Deps(engine, state["task_id"], "failure", 1), "failed", "web.read", {},
                            lambda _: (_ for _ in ()).throw(Failure("EXECUTION_FAILED", "fixture failed")))
    with pytest.raises(Failure, match="本任务"):
        engine.cited_content(Deps(engine, state["task_id"], "failed", 1), "facts.json", "{}", {"citation": failed["action_id"]})
    raw = engine.resources.artifact(result["citation"]["snapshot_artifact_id"])
    native_path(Path(raw["locator"])).write_bytes(b"tampered")
    with pytest.raises(Failure, match="校验和"):
        engine.cited_content(Deps(engine, state["task_id"], "tampered", 1), "facts.json", "{}", {"citation": result["action_id"]})


def test_citation_copy_requires_action_read_permission_without_double_charging_budget(client, app):
    state, result = source(client, app)
    engine = app.state.engine
    deps = Deps(engine, state["task_id"], "last-permitted-write", 1)
    def restrict(s, db):
        role = next(r for r in s["config_snapshot"]["role_catalog"]["roles"] if r["role_id"] == "coordinator")
        role["max_actions"] = 1
    engine.store.change(state["task_id"], "fixture.limit", restrict)
    written = engine.perform(deps, "write", "fs.write_workspace", {}, lambda action_id:
        engine.resources.write(deps.task_id, action_id, "last.json",
            engine.cited_content(deps, "last.json", "{}", {"citation": result["action_id"]})))
    assert written["kind"] == "completed"
    def deny(s, db):
        role = next(r for r in s["config_snapshot"]["role_catalog"]["roles"] if r["role_id"] == "coordinator")
        role["allowed_capabilities"].remove("action.inspect")
    engine.store.change(state["task_id"], "fixture.deny", deny)
    with pytest.raises(Failure, match="上限"):
        engine.cited_content(deps, "facts.json", "{}", {"citation": result["action_id"]})


def test_mixed_artifact_and_source_citations_identify_the_invalid_entry(client, app):
    state, source_read = source(client, app)
    engine = app.state.engine
    deps = Deps(engine, state["task_id"], "mixed-citations", 1)
    artifact = engine.resources.write(state["task_id"], "candidate", "candidate.json", '{"claim":16}')
    read_back = engine.perform(deps, "candidate-read", "artifact.read", {"artifact_id": artifact["artifact_id"]},
        lambda _: engine.resources.read_artifact(state["task_id"], artifact["artifact_id"]))
    with pytest.raises(Failure) as caught:
        engine.cited_content(deps, "report.md", "候选主张待验证。",
            {"source": source_read["action_id"], "candidate": read_back["action_id"]})
    message = str(caught.value)
    assert "candidate" in message and read_back["action_id"] in message
    assert "artifact_id" in message and "sha256" in message and "移除" in message
    corrected = engine.cited_content(deps, "report.md", f"候选 {artifact['artifact_id']} SHA {artifact['sha256']} 待验证。",
                                    {"source": source_read["action_id"]})
    assert source_read["citation"]["source_sha256"] in corrected
    assert artifact["sha256"] in corrected


def test_child_with_action_read_capability_cannot_copy_parent_citation(client, app):
    state, result = source(client, app)
    engine = app.state.engine
    def enable(s, db):
        s["config_snapshot"]["harness_mode"] = "H2"
    engine.store.change(state["task_id"], "fixture.child-capability", enable)
    parent = Deps(engine, state["task_id"], "parent", 1)
    handle = engine.perform(parent, "delegation-call", "role.delegate", {}, lambda action_id:
        engine.roles.prepare(parent, action_id, "data", AssignmentRequest(objective="isolated fixture")))
    assignment = engine.roles.read(handle["assignment_id"])
    assignment["status"] = "running"
    with engine.store.transaction() as db:
        engine.roles.save(db, assignment)
    child = Deps(engine, state["task_id"], assignment["run_id"], 1, assignment_id=assignment["assignment_id"])
    with pytest.raises(Failure, match="当前委派"):
        engine.cited_content(child, "child.json", "{}", {"citation": result["action_id"]})


@pytest.mark.parametrize("role_id", ["environment", "data", "code_experiment", "quality"])
def test_default_child_can_copy_own_citation_but_not_parent_or_sibling(client, app, tmp_path, role_id):
    source_file = tmp_path / "record.txt"
    source_file.write_text("软件夹具值为42。", encoding="utf-8")
    grant = client.post("/resources/register", json={"request_id": "child-citation-source", "path": str(source_file)}).json()
    state, _ = task(client, harness_mode="H2", input_refs=[grant["resource_id"]])
    engine = app.state.engine
    parent = Deps(engine, state["task_id"], "citation-root", 1)
    def read(deps, call_id):
        return engine.perform(deps, call_id, "document.read", {"resource_id": grant["resource_id"]},
            lambda _: engine.documents.read(state["task_id"], grant["resource_id"]))
    parent_read = read(parent, "root-read")
    children = []
    for index in range(2):
        request = AssignmentRequest(objective=f"独立来源检查{index}", input_refs=[grant["resource_id"]])
        handle = engine.perform(parent, f"child-{index}", "role.delegate", {},
            lambda action_id, request=request: engine.roles.prepare(parent, action_id, role_id, request))
        scope = engine.roles.start(engine.roles.read(handle["assignment_id"]))
        children.append(Deps(engine, state["task_id"], scope["run_id"], 1, assignment_id=scope["assignment_id"]))
    own_read = read(children[0], "own-read")
    sibling_read = read(children[1], "sibling-read")
    written = engine.perform(children[0], "write-own-source", "fs.write_workspace", {},
        lambda action_id: engine.resources.write(state["task_id"], action_id,
            engine.roles.output_path(children[0], "report.md"),
            engine.cited_content(children[0], "report.md", "观察值为42。", {"source": own_read["action_id"]})))
    assert written["kind"] == "completed"
    body = engine.resources.read_artifact(state["task_id"], written["artifact_id"])["text"]
    assert own_read["citation"]["source_sha256"] in body
    assert written["relative_path"].startswith(f"assignments/{children[0].assignment_id}/")
    for other in (parent_read, sibling_read):
        with pytest.raises(Failure, match="当前委派"):
            engine.cited_content(children[0], "report.md", "42", {"source": other["action_id"]})
