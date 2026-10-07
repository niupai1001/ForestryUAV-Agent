import json
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel
from test_core import task
from test_jobs import ExecutorFixture

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.environments import Environments, execution_code, preparation_code
from forestry_agent.jobs import Jobs
from forestry_agent.roles import AssignmentRequest


def wheel(client, tmp_path, name="fixture-1.0-py3-none-any.whl", request_id="wheel"):
    source = tmp_path/name
    source.write_bytes(b"wheel-validation-fixture-not-installable")
    reply = client.post("/resources/register", json={"request_id": request_id, "path": str(source)})
    assert reply.status_code == 200
    return source, reply.json()["resource_id"]


def environment_fixture(client, app, tmp_path, harness_mode="H2"):
    source, ref = wheel(client, tmp_path)
    state, _ = task(client, input_refs=[ref], harness_mode=harness_mode)
    engine, docker = app.state.engine, ExecutorFixture()
    jobs = Jobs(engine.config, engine.store, engine.resources, docker)
    environments = Environments(engine.config, engine.store, engine.resources, jobs)
    jobs.environment_resolver = environments.resolve
    deps = Deps(engine, state["task_id"], "environment-fixture", 1)
    result = engine.perform(deps, "prepare", "environment.prepare", {"input_refs": [ref]},
        lambda action: environments.prepare(state["task_id"], 1, action, [ref]))
    job_id = result["job"]["job_id"]
    jobs.tick()
    job = jobs.read(job_id)
    manifest = {"schema_version": 1, "kind": "python_environment", "image_reference": job["image_reference"],
        "wheels": job["environment_recipe"], "installation": "pip_offline_hash_checked", "installed": []}
    output = Path(job["directory"])/"output"
    (output/"environment-lock.json").write_text(json.dumps(manifest), encoding="utf-8")
    (output/"environment.zip").write_bytes(b"archive-boundary-fixture-not-an-installed-environment")
    docker.container["State"].update(Running=False, Status="exited", ExitCode=0)
    jobs.tick()
    assert jobs.read(job_id)["status"] == "succeeded"
    artifact = next(a for a in jobs.read(job_id)["artifact_ids"] if engine.resources.artifact(a)["name"] == "environment.zip")
    return state, deps, jobs, environments, artifact, source


def test_environment_preparation_is_scoped_and_has_no_host_installer(client, app, tmp_path):
    _, ref = wheel(client, tmp_path)
    state, _ = task(client, input_refs=[ref], constraints={"allowed_capabilities": ["code.execute"]})
    engine = app.state.engine
    with pytest.raises(Failure, match="允许范围"):
        engine.perform(Deps(engine, state["task_id"], "forbidden", 1), "prepare", "environment.prepare",
            {"input_refs": [ref]}, lambda _: pytest.fail("installer invoked without permission"))
    assert engine.store.task(state["task_id"])["action_count"] == 0
    with pytest.raises(Failure, match="未授权"):
        engine.environments.prepare(state["task_id"], 1, "unused", ["foreign-ref"])


@pytest.mark.parametrize("name", ["source.tar.gz", "bad wheel.whl", "bad;option.whl"])
def test_preparation_rejects_source_builds_and_unsafe_names(client, app, tmp_path, name):
    _, ref = wheel(client, tmp_path, name)
    state, _ = task(client, input_refs=[ref])
    with pytest.raises(Failure, match="whl"):
        app.state.engine.environments.prepare(state["task_id"], 1, "unused", [ref])
    with app.state.store.transaction() as db:
        assert not db.execute("SELECT 1 FROM jobs").fetchone()


def test_environment_reuse_preserves_lock_and_does_not_reinstall(client, app, tmp_path):
    state, deps, jobs, environments, artifact, _ = environment_fixture(client, app, tmp_path)
    env = environments.resolve(state["task_id"], 1, artifact)
    assert env["image_id"].startswith("sha256:")
    result = app.state.engine.perform(deps, "use", "code.execute", {"environment_artifact_id": artifact},
        lambda action: jobs.submit(state["task_id"], 1, action, "print(42)", [], environment_artifact_id=artifact))
    assert "source" not in result["job"]["environment"]
    jobs.docker.container = None
    jobs.tick()
    job = jobs.read(result["job"]["job_id"])
    assert job["status"] == "running" and job["environment"]["sha256"] == env["sha256"]
    create = next(c for c in reversed(jobs.docker.commands) if c[0] == "create")
    assert "dst=/environment.zip,readonly" in " ".join(create)
    assert create[create.index("--network")+1] == "none"
    assert "/environment:rw,exec,nosuid,nodev,mode=1777,size=1073741824" in create
    assert "/tmp:rw,noexec,nosuid,size=67108864" in create
    script = (Path(job["directory"])/"run.py").read_text(encoding="utf-8")
    assert "Unsafe environment archive member" in script and "'..' in path.parts" in script
    assert "-m pip" not in script and "subprocess" not in script
    compile(script, "environment-use", "exec")  # Syntax only; package code is never run by this test.


def test_native_environment_observation_keeps_ready_identity_without_duplicate_recipes(client, app, tmp_path):
    state, _, jobs, environments, artifact, _ = environment_fixture(client, app, tmp_path)
    with jobs.store.transaction() as db:
        job_id = db.execute("SELECT id FROM jobs WHERE task_id=?", (state["task_id"],)).fetchone()["id"]
    result = jobs.observation(job_id, state["task_id"])
    assert result["prepared_environment"]["artifact_id"] == artifact
    assert result["prepared_environment"]["lock_artifact_id"] != artifact
    assert result["prepared_environment"]["sha256"] == environments.resolve(state["task_id"], 1, artifact)["sha256"]
    assert "environment_recipe" not in result["job"] and "manifest" not in result["prepared_environment"]
    assert jobs.read(job_id)["environment_recipe"]  # Persisted evidence is retained.
    with pytest.raises(Failure, match="不属于当前任务"):
        jobs.observation(job_id, "foreign-task")


def test_environment_forgery_foreign_scope_revision_and_tamper_are_rejected(client, app, tmp_path):
    state, _, _, environments, artifact, _ = environment_fixture(client, app, tmp_path)
    forged = app.state.resources.write(state["task_id"], "fake-write", "environment.zip", "not a prepared environment")
    with pytest.raises(Failure, match="准备作业"):
        environments.resolve(state["task_id"], 1, forged["artifact_id"])
    foreign, _ = task(client, request_id="other-environment")
    with pytest.raises(Failure, match="不属于当前任务"):
        environments.resolve(foreign["task_id"], 1, artifact)
    with pytest.raises(Failure, match="当前修订"):
        environments.resolve(state["task_id"], 2, artifact)
    path = Path(app.state.resources.artifact(artifact)["locator"])
    path.write_bytes(b"changed archive")
    with pytest.raises(Failure, match="校验和"):
        environments.resolve(state["task_id"], 1, artifact)


def test_environment_changed_runtime_stops_before_container_creation(client, app, tmp_path):
    state, deps, jobs, _, artifact, _ = environment_fixture(client, app, tmp_path)
    result = app.state.engine.perform(deps, "use", "code.execute", {},
        lambda action: jobs.submit(state["task_id"], 1, action, "print(42)", [], environment_artifact_id=artifact))
    original = jobs.docker.call
    def changed(*args):
        if args[:2] == ("image", "inspect"):
            return {"code": 0, "stdout": "sha256:"+"b"*64, "stderr": ""}
        return original(*args)
    jobs.docker.call = changed
    before = len(jobs.docker.commands)
    jobs.tick()
    job = jobs.read(result["job"]["job_id"])
    assert job["status"] == "queued" and job["blocker"]["code"] == "VERSION_CONFLICT"
    assert not any(c[0] == "create" for c in jobs.docker.commands[before:])


def test_role_must_receive_environment_artifact_explicitly(client, app, tmp_path):
    state, deps, _, _, artifact, _ = environment_fixture(client, app, tmp_path)
    engine = app.state.engine
    handle = engine.perform(deps, "delegate", "role.delegate", {},
        lambda action: engine.roles.prepare(deps, action, "code_experiment", AssignmentRequest(objective="scope")))
    value = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    child = Deps(engine, state["task_id"], value["run_id"], 1, assignment_id=value["assignment_id"])
    with engine.store.transaction() as db, pytest.raises(Failure, match="依赖环境"):
        engine.roles.authorize(db, child, "code.execute", {"environment_artifact_id": artifact})


def test_preparation_recipe_requires_hashes_and_complete_offline_resolution():
    code = preparation_code([{"resource_id": "wheel", "filename": "example-1-py3-none-any.whl",
        "sha256": "a"*64, "size_bytes": 1, "source_version": "stat:1"}], "python@sha256:"+"b"*64)
    compile(code, "environment-prepare", "exec")
    assert "--require-hashes" in code and "--only-binary" in code and "--ignore-installed" in code
    assert "--no-index" in code and "--no-deps" not in code
    # JSON with non-Python literals must remain data in the generated launcher.
    compile(execution_code("print(42)", {"sha256": "a"*64, "manifest": {"value": None}}), "environment-use", "exec")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["H0", "H1", "H2"])
async def test_ready_archive_fact_requires_confirmed_observation_and_real_producer(client, app, tmp_path, mode):
    state, deps, jobs, _, artifact, _ = environment_fixture(client, app, tmp_path, mode)
    engine = app.state.engine
    with engine.store.transaction() as db:
        job_id = db.execute("SELECT id FROM jobs WHERE task_id=?", (state["task_id"],)).fetchone()[0]
    for observed in [False, True]:
        if observed:
            receipt = engine.perform(deps, "observe-ready", "job.observe", {"job_id":job_id},
                lambda _: jobs.observation(job_id, state["task_id"]))
            forged = engine.resources.write(state["task_id"], "wrong-producer", "environment.zip", "not an environment")
            bad = {"kind":"completed", "job":{"job_id":job_id, "status":"succeeded"},
                "prepared_environment":{"artifact_id":forged["artifact_id"], "sha256":forged["sha256"]}}
            with engine.store.transaction() as db:
                db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)", ("forged-ready-receipt", state["task_id"], deps.turn_id,
                    "forged-ready", 1, "job.observe", "{}", "confirmed", json.dumps(bad)))
        turn_id = f"ready-facts-{observed}"
        agent, connection = engine.make_agent("small", turn_id, state["config_snapshot"], state["task_id"])
        try:
            with agent.override(model=TestModel(call_tools=[], custom_output_args={"summary":"已观察环境标识夹具", "artifact_ids":[]})):
                await agent.run("检查已记录的环境标识", deps=Deps(engine, state["task_id"], turn_id, 1))
            with engine.store.transaction() as db:
                manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id=? ORDER BY rowid DESC LIMIT 1", (turn_id,)).fetchone()[0])
            body = json.loads(Path(manifest["prepared_input_ref"]).read_bytes())
            instructions = "\n".join(body["instructions"])
            if mode == "H0":
                assert '"recent_jobs"' not in instructions
            else:
                facts = next(json.loads(text.split("：",1)[1].split("\n",1)[0]) for text in body["instructions"] if text.startswith("当前权威任务事实"))
                recorded = next(j for j in facts["recent_jobs"] if j["job_id"] == job_id)
                assert recorded["environment_artifact_id"] == (artifact if observed else None)
                if observed:
                    assert recorded["environment_observation_action_id"] == receipt["action_id"]
                else:
                    assert "environment_observation_action_id" not in recorded
        finally:
            await connection.aclose()
