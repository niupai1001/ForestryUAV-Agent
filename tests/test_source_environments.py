"""Authority/recovery fixtures; real archive/build execution is container-only."""
import json
from pathlib import Path

import pytest
from test_core import task
from test_environments import wheel
from test_jobs import ExecutorFixture

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.environments import Environments, preparation_code
from forestry_agent.jobs import Jobs
from forestry_agent.roles import AssignmentRequest


def source_job(client, app, tmp_path, *, build_only=False, inspect_build=None):
    source, ref = wheel(client, tmp_path, "fixture.tar.gz", "source")
    state, _ = task(client, input_refs=[ref])
    engine, docker = app.state.engine, ExecutorFixture()
    jobs = Jobs(engine.config, engine.store, engine.resources, docker)
    environments = Environments(engine.config, engine.store, engine.resources, jobs)
    jobs.environment_resolver = environments.resolve
    jobs.source_build_resolver = environments.built
    jobs.source_requirements_resolver = environments.requirements
    jobs.input_artifact_validator = environments.acquired_package
    deps = Deps(engine, state["task_id"], "source-fixture", 1)
    reply = engine.perform(deps, "build", "environment.prepare", {"input_refs":[ref], "source_refs":[ref], 'build_only':build_only},
        lambda action: environments.prepare(state["task_id"], 1, action, [], source_refs=[ref],
            build_only=build_only, inspect_build=inspect_build))
    assert reply["kind"] == "completed"
    return source, ref, state, deps, jobs, environments, reply["job"]["job_id"]


@pytest.mark.parametrize('inspect_build',[None,'static','dynamic'])
def test_source_build_requires_both_capabilities_before_any_action(client, app, tmp_path,inspect_build):
    _, ref = wheel(client, tmp_path, "fixture.zip", "source")
    state, _ = task(client, input_refs=[ref], constraints={"allowed_capabilities":["environment.prepare"]})
    engine = app.state.engine
    with pytest.raises(Failure, match="允许范围"):
        engine.perform(Deps(engine, state["task_id"], "denied-build", 1), "build", "environment.prepare",
            {"input_refs":[ref], "source_refs":[ref]}, lambda _: pytest.fail("unauthorized source build"))
    with pytest.raises(Failure, match="允许范围"):
        engine.environments.prepare(state["task_id"], 1, "direct-build", [], source_refs=[ref],inspect_build=inspect_build)
    # The lower Jobs boundary independently protects callers bypassing tools.
    with pytest.raises(Failure, match="允许范围"):
        engine.jobs.submit(state["task_id"], 1, "bypass-build", "print(42)", [ref],
            job_kind="environment.prepare", source_recipe=[{"resource_id":ref}],source_inspection=inspect_build)
    assert engine.store.task(state["task_id"])["action_count"] == 0
    with engine.store.transaction() as db:
        assert not db.execute("SELECT 1 FROM jobs").fetchone()


def test_source_build_recovery_keeps_one_container_and_bounded_exec_mount(client, app, tmp_path):
    _, ref, _, _, jobs, _, job_id = source_job(client, app, tmp_path)
    jobs.docker.lose_create_response = True
    jobs.tick()
    assert jobs.read(job_id)["status"] == "creating"
    restarted = Jobs(jobs.config, jobs.store, jobs.resources, jobs.docker)
    restarted.tick()
    job = restarted.read(job_id)
    assert job["status"] == "running" and job["source_recipe"][0]["resource_id"] == ref
    assert sum(c[0] == "create" for c in jobs.docker.commands) == 1
    create = next(c for c in jobs.docker.commands if c[0] == "create")
    assert "/build:rw,exec,nosuid,nodev,mode=1777,size=1073741824" in create
    assert "/tmp:rw,noexec,nosuid,size=1073741824" in create
    assert create[create.index("--network")+1] == "none"
    assert create[create.index("--user")+1] == "65534:65534"
    code = (Path(job["directory"])/"run.py").read_text(encoding="utf-8")
    compile(code, "source-worker", "exec")  # Never execute this worker on the host.
    assert "source_recipe" not in Jobs.model_receipt({"job":Jobs.public(job)})["job"]
    assert "source_recipe" not in jobs.observation(job_id, job["task_id"])["job"]


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize('inspect_build',[None,'static','dynamic'])
def test_revoked_execution_permission_cancels_source_jobs_after_restart(client, app, tmp_path, running,inspect_build):
    _, _, state, _, jobs, _, job_id = source_job(client, app, tmp_path,inspect_build=inspect_build)
    if running:
        jobs.tick()
        assert jobs.read(job_id)["status"] == "running"
    # Simulate policy revocation without relying on the spec-revision stop path.
    with jobs.store.transaction() as db:
        row = db.execute("SELECT payload FROM specs WHERE task_id=? AND revision=1", (state["task_id"],)).fetchone()
        spec = json.loads(row[0])
        spec["constraints"]["allowed_capabilities"] = ["environment.prepare"]
        db.execute("UPDATE specs SET payload=? WHERE task_id=? AND revision=1", (json.dumps(spec), state["task_id"]))
    restarted = Jobs(jobs.config, jobs.store, jobs.resources, jobs.docker)
    restarted.tick()
    assert restarted.read(job_id)["status"] == "cancelled"
    assert sum(c[0] == "kill" for c in jobs.docker.commands) == int(running)
    if not running:
        assert not jobs.docker.commands


def test_source_changed_before_start_and_foreign_scope_are_rejected(client, app, tmp_path):
    source, ref, state, _, jobs, environments, job_id = source_job(client, app, tmp_path)
    foreign, _ = task(client, request_id="foreign-source")
    with pytest.raises(Failure, match="输入范围"):
        environments.prepare(foreign["task_id"], 1, "foreign-build", [], source_refs=[ref])
    source.write_bytes(b"changed source")
    jobs.tick()
    assert jobs.read(job_id)["blocker"]["code"] == "VERSION_CONFLICT"
    assert not any(c[0] == "create" for c in jobs.docker.commands)
    assert jobs.read(job_id)["task_id"] == state["task_id"]


def test_source_role_cannot_bypass_effective_execution_intersection(client, app, tmp_path):
    _, ref, state, deps, _, _, _ = source_job(client, app, tmp_path)
    engine = app.state.engine
    handle = engine.perform(deps, "delegate", "role.delegate", {}, lambda action:
        engine.roles.prepare(deps, action, "code_experiment", AssignmentRequest(objective="scope", input_refs=[ref])))
    assignment = engine.roles.start(engine.roles.read(handle["assignment_id"]))
    assignment["effective_capabilities"] = ["environment.prepare"]
    with engine.store.transaction() as db:
        engine.roles.save(db, assignment)
    child = Deps(engine, state["task_id"], assignment["run_id"], 1, assignment_id=assignment["assignment_id"])
    with engine.store.transaction() as db, pytest.raises(Failure, match="权限交集"):
        engine.roles.authorize(db, child, "environment.prepare", {"source_refs":[ref], "input_refs":[ref]})


def test_source_manifest_requires_exact_sources_and_actual_built_wheel(client, app, tmp_path):
    _, _, state, _, jobs, environments, job_id = source_job(client, app, tmp_path)
    jobs.tick()
    job = jobs.read(job_id)
    output = Path(job["directory"])/"output"
    manifest = {"schema_version":1, "kind":"python_environment", "image_reference":job["image_reference"],
        "wheels":[], "installation":"pip_offline_hash_checked", "sources":job["source_recipe"],
        "build_frontend":{"isolation":"native_pep517"}, "built_wheels":[{
            "source_resource_id":job["source_recipe"][0]["resource_id"],
            "source_sha256":job["source_recipe"][0]["sha256"], "filename":"missing.whl", "sha256":"a"*64, "size_bytes":1}]}
    (output/"environment-lock.json").write_text(json.dumps(manifest), encoding="utf-8")
    (output/"environment.zip").write_bytes(b"protocol fixture only")
    jobs.docker.container["State"].update(Running=False, Status="exited", ExitCode=0)
    jobs.tick()
    archive = next(a for a in jobs.read(job_id)["artifact_ids"] if jobs.resources.artifact(a)["name"] == "environment.zip")
    with pytest.raises(Failure, match="登记产物"):
        environments.resolve(state["task_id"], 1, archive)


def test_source_worker_keeps_native_isolation_and_full_final_dependency_check():
    code = preparation_code([], "python@sha256:"+"b"*64, [{"resource_id":"source", "filename":"fixture.zip",
        "sha256":"a"*64, "size_bytes":1, "source_version":"stat:1"}])
    compile(code, "source-worker", "exec")
    assert "--no-build-isolation" not in code
    assert code.count("--no-deps") == 1  # wheel conversion only, never final install.
    assert "--require-hashes" in code and "--only-binary" in code


@pytest.mark.parametrize("name", ['中文 源码.zip', 'source;safe.tar.gz'])
def test_local_source_names_remain_data_without_ascii_or_shell_restriction(client, app, tmp_path, name):
    _, ref = wheel(client, tmp_path, name, "source-name")
    state, _ = task(client, input_refs=[ref])
    engine = app.state.engine
    reply = engine.perform(Deps(engine, state["task_id"], "source-name", 1), "source-name", "environment.prepare",
        {"input_refs":[ref], "source_refs":[ref]}, lambda action:
        engine.environments.prepare(state["task_id"], 1, action, [], source_refs=[ref]))
    assert reply["kind"] == "completed"
    job = engine.jobs.read(reply["job"]["job_id"])
    assert job["source_recipe"][0]["filename"] == name
    assert job["mounts"][0]["target"] == "/inputs/"+ref
    compile(job["code"], "source-worker-name", "exec")  # Only compile trusted launcher text.
