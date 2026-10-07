import hashlib
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from test_core import task
from test_jobs import ExecutorFixture
from test_network import dns, resolve
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.network import PublicHTTP
from forestry_agent.progress import observation
from forestry_agent.store import now

WHEEL = {"name": "six", "version": "1.17.0", "filename": "six-1.17.0-py2.py3-none-any.whl"}


class ReleaseFixture:
    def __init__(self):
        self.calls = []
        self.data = b"bounded-download-fixture-never-installed"
        self.entry = {"filename": WHEEL["filename"], "packagetype": "bdist_wheel", "yanked": False,
            "url": "https://files.pythonhosted.org/packages/ab/" + WHEEL["filename"],
            "size": len(self.data), "digests": {"sha256": hashlib.sha256(self.data).hexdigest()}}
        self.after_response = lambda: None

    def get(self, url, **kwargs):
        kwargs["guard"](url)
        self.calls.append(url)
        body = json.dumps({"info": {"name": "six", "version": "1.17.0"}, "urls": [self.entry]}).encode() if url.endswith("/json") else self.data
        self.after_response()
        return {"body": body, "url": url, "hops": [{"url": url, "address": "8.8.8.8", "status": 200}], "observed_at": now()}


def acquire(engine, state, call="download", wheels=None, source="pypi", approval=None):
    deps = Deps(engine, state["task_id"], "acquisition-test", state["spec_revision"])
    return engine.perform(deps, call, "environment.acquire", {"wheels": wheels or [WHEEL], "source": source, "approval_id": approval},
        lambda action: engine.acquisition.acquire(deps, action, wheels or [WHEEL], source, approval))


def public_task(client, **kwargs):
    return task(client, context_classification="public", **kwargs)[0]


def test_private_acquisition_requires_exact_approval_and_replays_without_network(client, app):
    state, _ = task(client, objective="SYNTHETIC_PRIVATE_SOURCE")
    engine, fake = app.state.engine, ReleaseFixture()
    engine.acquisition.http = fake
    proposed = acquire(engine, state)
    assert proposed["kind"] == "approval_required" and fake.calls == []
    record = engine.network.list(state["task_id"])[0]
    assert record["request"]["kind"] == "environment.acquire"
    assert "SYNTHETIC_PRIVATE_SOURCE" not in json.dumps(record["request"])
    assert resolve(client, state, record).status_code == 200
    mismatch = acquire(engine, state, "mirror-changed", source="tuna", approval=record["approval_id"])
    assert mismatch["error"]["code"] == "VERSION_CONFLICT" and not fake.calls
    result = acquire(engine, state, "approved", approval=record["approval_id"])
    assert result["kind"] == "completed" and len(fake.calls) == 2
    assert acquire(engine, state, "approved", approval=record["approval_id"]) == result and len(fake.calls) == 2
    wheel = engine.environments.acquired_wheel(state["task_id"], 1, result["wheel_artifact_ids"][0])
    assert Path(wheel["locator"]).read_bytes() == fake.data
    lock = engine.resources.read_artifact(state["task_id"], result["lock_artifact_id"])
    assert json.loads(lock["text"])["wheels"][0]["metadata_sha256"]
    denied = acquire(engine, state, "reuse", approval=record["approval_id"])
    assert denied["error"]["code"] == "PERMISSION_DENIED" and len(fake.calls) == 2


@pytest.mark.parametrize("condition", ["wrong_sha", "wrong_size", "yanked", "private_url", "query", "fragment", "sdist", "wrong_version"])
def test_invalid_metadata_or_download_never_becomes_an_installable_wheel(client, app, condition):
    state = public_task(client)
    engine, fake = app.state.engine, ReleaseFixture()
    engine.acquisition.http = fake
    if condition == "wrong_sha":
        fake.entry["digests"]["sha256"] = "0" * 64
    elif condition == "wrong_size":
        fake.entry["size"] += 1
    elif condition == "yanked":
        fake.entry["yanked"] = True
    elif condition == "private_url":
        fake.entry["url"] = "https://127.0.0.1/packages/" + WHEEL["filename"]
    elif condition in {"query", "fragment"}:
        fake.entry["url"] += "?secret=x" if condition == "query" else "#secret"
    elif condition == "sdist":
        fake.entry["packagetype"] = "sdist"
    result = acquire(engine, state, wheels=[{**WHEEL, "version": "0.0.1"}] if condition == "wrong_version" else None)
    assert result["kind"] == "failed"
    with engine.store.transaction() as db:
        assert not db.execute("SELECT 1 FROM jobs").fetchone()
        assert not any(json.loads(r[0])["name"].endswith(".whl") for r in db.execute("SELECT payload FROM artifacts"))


@pytest.mark.parametrize("condition", ["revoked", "paused", "amended", "changed_input"])
def test_control_and_scope_are_rechecked_between_metadata_and_file(client, app, tmp_path, condition):
    source = tmp_path / "private.csv"
    source.write_text("synthetic fixture")
    ref = client.post("/resources/register", json={"request_id": "private", "path": str(source)}).json()["resource_id"]
    state, _ = task(client, input_refs=[ref])
    engine, fake = app.state.engine, ReleaseFixture()
    engine.acquisition.http = fake
    acquire(engine, state)
    record = engine.network.list(state["task_id"])[0]
    assert resolve(client, state, record).status_code == 200
    def changed():
        if condition == "changed_input":
            source.write_text("changed private input version")
        else:
            with engine.store.transaction() as db:
                if condition == "revoked":
                    grant = json.loads(db.execute("SELECT payload FROM approvals WHERE id=?", (record["approval_id"],)).fetchone()[0])
                    grant["status"] = "revoked"
                    db.execute("UPDATE approvals SET status='revoked',payload=? WHERE id=?", (json.dumps(grant), record["approval_id"]))
                else:
                    current = engine.store.read_task(db, state["task_id"])
                    current["pause_requested"] = condition == "paused"
                    current["spec_revision"] += int(condition == "amended")
                    engine.store.save_task(db, current, current["state_version"])
    fake.after_response = changed
    result = acquire(engine, state, "approved", approval=record["approval_id"])
    assert result["kind"] == "failed" and len(fake.calls) == 1


def test_acquired_wheels_bind_revision_producer_and_readonly_job_mounts(client, app):
    state = public_task(client)
    engine = app.state.engine
    engine.acquisition.http = ReleaseFixture()
    result = acquire(engine, state)
    ref = result["wheel_artifact_ids"][0]
    deps = Deps(engine, state["task_id"], "acquisition-test", 1)
    prepared = engine.perform(deps, "prepare", "environment.prepare", {"wheel_artifact_ids": [ref]},
        lambda action: engine.environments.prepare(state["task_id"], 1, action, [], wheel_artifact_ids=[ref]))
    docker = ExecutorFixture()
    engine.jobs.docker = docker
    engine.jobs.tick()
    job = engine.jobs.read(prepared["job"]["job_id"])
    assert job["status"] == "running" and job["environment_recipe"][0]["source_kind"] == "acquired_artifact"
    create = next(args for args in docker.commands if args[0] == "create")
    assert any(f"dst=/inputs/{ref},readonly" in arg for arg in create)
    assert create[create.index("--network")+1] == "none"
    with pytest.raises(Failure, match="当前修订"):
        engine.environments.acquired_wheel(state["task_id"], 2, ref)
    forged = engine.resources.write(state["task_id"], "fake", WHEEL["filename"], "not acquired")
    with pytest.raises(Failure, match="获取动作"):
        engine.environments.acquired_wheel(state["task_id"], 1, forged["artifact_id"])
    Path(engine.resources.artifact(ref)["locator"]).write_bytes(b"tampered")
    with pytest.raises(Failure, match="校验和"):
        engine.jobs.may_start(job)


def test_binary_http_budget_keeps_the_default_reader_limit_and_checks_redirects():
    body = b"x" * (2 * 1024 * 1024 + 1)
    http = PublicHTTP(dns(["8.8.8.8"]), lambda *args: (200, {}, body))
    with pytest.raises(Failure, match="2 MiB"):
        http.get("https://files.pythonhosted.org/file")
    assert http.get("https://files.pythonhosted.org/file", max_bytes=len(body))["body"] == body
    http.exchange = lambda *args: (302, {"location": "https://localhost/file"}, b"")
    with pytest.raises(Failure):
        http.get("https://files.pythonhosted.org/file", max_bytes=len(body), allowed_origins=["https://files.pythonhosted.org"])


def test_repeat_downloads_do_not_become_progress_through_new_artifact_ids(client, app):
    state = public_task(client)
    engine = app.state.engine
    engine.acquisition.http = ReleaseFixture()
    first, repeated = acquire(engine, state), acquire(engine, state, "repeat")
    assert first["wheel_artifact_ids"] != repeated["wheel_artifact_ids"]
    assert observation("acquire_dependencies", first) == observation("acquire_dependencies", repeated)


def test_long_acquired_wheel_path_is_supported_without_executing_the_file(client, app):
    state = public_task(client)
    engine, fake = app.state.engine, ReleaseFixture()
    filename = "six-1.17.0-cp312-cp312-" + "manylinux_2_17_x86_64." * 8 + "manylinux2014_x86_64.whl"
    fake.entry["filename"] = filename
    fake.entry["url"] = "https://files.pythonhosted.org/packages/ab/" + filename
    engine.acquisition.http = fake
    downloaded = acquire(engine, state, wheels=[{**WHEEL, "filename": filename}])
    assert downloaded["kind"] == "completed"
    refs = downloaded["wheel_artifact_ids"]
    assert len(engine.resources.artifact(refs[0])["locator"]) > 260
    prepared = engine.perform(Deps(engine, state["task_id"], "acquisition-test", 1), "prepare-long", "environment.prepare", {"wheel_artifact_ids": refs},
        lambda action: engine.environments.prepare(state["task_id"], 1, action, [], wheel_artifact_ids=refs))
    assert prepared["kind"] == "completed" and prepared["job"]["status"] == "queued"


@pytest.mark.asyncio
async def test_native_acquisition_tool_is_hidden_outside_frozen_scope(client, app):
    for caps in [["environment.acquire"], ["environment.prepare"], []]:
        state = public_task(client, request_id=str(caps), constraints={"allowed_capabilities": caps})
        engine = app.state.engine
        turn_id = "native-acquisition-" + state["task_id"]
        agent, connection = engine.make_agent("small", turn_id, state["config_snapshot"], state["task_id"])
        try:
            seen = []
            def model(messages, info, seen=seen):
                seen.append({t.name for t in info.function_tools})
                return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "范围夹具", "artifact_ids": []}, "done")])
            with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
                await agent.run("检查工具范围", deps=Deps(engine, state["task_id"], turn_id, 1))
            assert all(("acquire_dependencies" in tools) == ("environment.acquire" in caps) for tools in seen)
        finally:
            await connection.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["H0", "H1", "H2"])
async def test_native_typed_acquisition_call_returns_real_journalled_artifacts(client, app, mode):
    state = public_task(client, harness_mode=mode, constraints={"allowed_capabilities": ["environment.acquire"]})
    engine = app.state.engine
    fake = ReleaseFixture()
    engine.acquisition.http = fake
    turn_id = "typed-native-acquisition"
    agent, connection = engine.make_agent("small", turn_id, state["config_snapshot"], state["task_id"])
    def model(messages, info):
        returns = [p.content for m in messages if isinstance(m, ModelRequest) for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_name == "acquire_dependencies"]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("acquire_dependencies", {"wheels": [WHEEL]}, "get-wheel")])
        result = returns[-1]
        assert result["kind"] == "completed" and result["package_execution"] is False
        return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "仅下载夹具，未安装", "artifact_ids": result["wheel_artifact_ids"]}, "done")])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            result = await agent.run("获取明确公开的软件夹具", deps=Deps(engine, state["task_id"], turn_id, 1))
        assert len(fake.calls) == 2 and len(result.output.artifact_ids) == 1
        with engine.store.transaction() as db:
            args = json.loads(db.execute("SELECT args FROM actions WHERE task_id=? AND kind='environment.acquire'", (state["task_id"],)).fetchone()[0])
            assert args["wheels"] == [WHEEL]
    finally:
        await connection.aclose()


def test_acquisition_is_denied_before_network_when_capability_or_role_ceiling_disallows(client, app):
    state = public_task(client, constraints={"allowed_capabilities": ["environment.prepare"]})
    with pytest.raises(Failure, match="允许范围"):
        acquire(app.state.engine, state)
    state = public_task(client, request_id="no-network")
    with app.state.store.transaction() as db:
        current = app.state.store.read_task(db, state["task_id"])
        current["config_snapshot"]["role_catalog"]["roles"][0]["permission_ceiling"]["network"] = "none"
        app.state.store.save_task(db, current, current["state_version"])
    with pytest.raises(Failure, match="禁止网络"):
        acquire(app.state.engine, state)
