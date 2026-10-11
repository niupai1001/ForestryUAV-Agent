import copy
import hashlib
from pathlib import Path

import pytest
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.jobs import Docker, Jobs
from forestry_agent.resources import native_path


class ExecutorFixture:
    """Fault fixture only; passing these tests never proves real Docker execution."""
    def __init__(self):
        self.container = None
        self.commands = []
        self.lose_create_response = False
        self.refuse_stop = False

    def inspect(self, name):
        return copy.deepcopy(self.container)

    def call(self, *args):
        self.commands.append(args)
        if args[:2] == ("image", "inspect"):
            return {"code": 0, "stdout": "sha256:" + "a" * 64, "stderr": ""}
        if args[0] == "create":
            labels = [args[i+1].split("=", 1) for i, a in enumerate(args) if a == "--label"]
            self.container = {"Id": "container-a", "Config": {"Labels": dict(labels)},
                "State": {"Status": "created", "Running": False, "ExitCode": 0}}
            if self.lose_create_response:
                self.lose_create_response = False
                raise Failure("EXECUTOR_UNAVAILABLE", "response lost", 503)
        if args[0] == "start":
            self.container["State"].update(Status="running", Running=True)
        if args[0] == "kill" and not self.refuse_stop:
            self.container["State"].update(Status="exited", Running=False, ExitCode=137)
        return {"code": 0, "stdout": "container-a", "stderr": ""}


def submit(client, app, fake, input_refs=None):
    state, _ = task(client, input_refs=input_refs or [])
    manager = Jobs(app.state.engine.config, app.state.store, app.state.resources, fake)
    deps = Deps(app.state.engine, state["task_id"], "test-turn", 1)
    result = app.state.engine.perform(deps, "code-call", "code.execute", {"code": "print(42)"},
        lambda action: manager.submit(state["task_id"], 1, action, "print(42)", input_refs or [], 60))
    assert result["kind"] == "completed"
    return state, manager, result["job"]["job_id"]


def test_lost_create_response_is_reconciled_without_second_creation(client, app):
    fake = ExecutorFixture()
    fake.lose_create_response = True
    _, manager, job_id = submit(client, app, fake)
    manager.tick()
    assert manager.read(job_id)["status"] == "creating"
    # A new manager represents a restarted application, retaining the same store.
    restarted = Jobs(manager.config, manager.store, manager.resources, fake)
    restarted.tick()
    assert restarted.read(job_id)["status"] == "running"
    assert sum(c[0] == "create" for c in fake.commands) == 1
    create = next(c for c in fake.commands if c[0] == "create")
    assert create[create.index("--network") + 1] == "none"
    assert "--read-only" in create and "--cap-drop" in create
    assert create[create.index("--user") + 1] == "65534:65534"
    assert restarted.read(job_id)["container_id"] == "container-a"


def test_cancel_not_confirmed_while_executor_still_running(client, app):
    fake = ExecutorFixture()
    state, manager, job_id = submit(client, app, fake)
    manager.tick()
    current = app.state.store.task(state["task_id"])
    result = client.post(f"/tasks/{state['task_id']}/cancel", json={
        "request_id": "stop", "expected_state_version": current["state_version"]})
    assert result.json()["status"] == "paused"
    fake.refuse_stop = True
    manager.tick()
    assert manager.read(job_id)["status"] == "running"
    assert manager.read(job_id)["blocker"]["code"] == "RECONCILIATION_REQUIRED"
    assert app.state.store.task(state["task_id"])["status"] == "paused"
    fake.refuse_stop = False
    manager.tick()
    assert manager.read(job_id)["status"] == "cancelled"
    assert app.state.store.task(state["task_id"])["status"] == "cancelled"


def test_unrelated_container_cannot_be_started_or_killed(client, app):
    fake = ExecutorFixture()
    _, manager, job_id = submit(client, app, fake)
    manager.tick()
    fake.container["Config"]["Labels"]["forestry.job"] = "other-job"
    manager.request_cancel(job_id, manager.read(job_id)["task_id"])
    manager.tick()
    assert manager.read(job_id)["blocker"]["code"] == "RECONCILIATION_REQUIRED"
    assert not any(c[0] == "kill" for c in fake.commands)


def test_changed_input_does_not_launch_code(client, app, tmp_path):
    source = tmp_path / "heights.csv"
    source.write_text("height\n12\n")
    ref = client.post("/resources/register", json={"request_id": "source", "path": str(source)}).json()["resource_id"]
    fake = ExecutorFixture()
    _, manager, job_id = submit(client, app, fake, [ref])
    source.write_text("height\n13\n")
    manager.tick()
    assert manager.read(job_id)["status"] == "queued"
    assert manager.read(job_id)["blocker"]["code"] == "VERSION_CONFLICT"
    assert not any(c[0] == "create" for c in fake.commands)


def test_output_recollection_is_idempotent_and_immutable(client, app):
    fake = ExecutorFixture()
    _, manager, job_id = submit(client, app, fake)
    manager.tick()
    output = Path(manager.read(job_id)["directory"]) / "output" / "result.bin"
    output.write_bytes(b"x" * (3 * 1024 * 1024))
    fake.container["State"].update(Status="exited", Running=False, ExitCode=0)
    manager.tick()
    job = manager.read(job_id)
    assert job["status"] == "succeeded" and len(job["artifact_ids"]) == 1
    artifact = manager.resources.artifact(job["artifact_ids"][0])
    assert artifact["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    # Simulate a crash after file sealing, before the final job update.
    job["status"] = "collecting"
    manager.save(job, "fixture.crash_window")
    manager.tick()
    assert manager.read(job_id)["artifact_ids"] == job["artifact_ids"]
    output.write_bytes(b"changed working output")
    assert manager.resources.artifact(artifact["artifact_id"])["sha256"] == artifact["sha256"]


def test_long_output_filename_is_collected_and_sealed(client, app):
    fake = ExecutorFixture()
    _, manager, job_id = submit(client, app, fake)
    manager.tick()
    output = Path(manager.read(job_id)["directory"])/"output"/("x"*180+".whl")
    assert len(str(output)) > 260
    native_path(output).write_bytes(b"long-path-output-fixture")
    fake.container["State"].update(Status="exited", Running=False, ExitCode=0)
    manager.tick()
    job = manager.read(job_id)
    assert job["status"] == "succeeded" and len(job["artifact_ids"]) == 1
    artifact = manager.resources.artifact(job["artifact_ids"][0])
    assert artifact["name"] == output.name and artifact["size_bytes"] == 24
    assert artifact["sha256"] == hashlib.sha256(b"long-path-output-fixture").hexdigest()


def test_job_scope_and_pause_before_dispatch(client, app):
    fake = ExecutorFixture()
    state, manager, job_id = submit(client, app, fake)
    app.state.store.change(state["task_id"], "pause", lambda s, _: s.update(pause_requested=True))
    manager.tick()
    assert manager.read(job_id)["status"] == "queued" and not fake.commands
    with pytest.raises(Failure):
        manager.read(job_id, "another-task")


def test_terminal_observation_provides_actual_diagnostics_and_keeps_identity_checks(client, app):
    fake = ExecutorFixture()
    state, manager, job_id = submit(client, app, fake)
    assert "logs" not in manager.observation(job_id, state["task_id"])
    manager.tick()
    fake.container["State"].update(Status="exited", Running=False, ExitCode=1)
    manager.tick()
    original = fake.call
    def call(*args):
        if args[0] == "logs":
            return {"code": 0, "stdout": "before failure", "stderr": "NameError: heights is not defined"}
        return original(*args)
    fake.call = call
    result = manager.observation(job_id, state["task_id"])
    assert result["job"]["status"] == "failed" and result["job"]["exit_code"] == 1
    assert result["logs"] == {"text": "before failure", "stderr": "NameError: heights is not defined", "bounded": True,
                              "window": "tail", "stdout_truncated": False, "stderr_truncated": False}
    with pytest.raises(Failure):
        manager.observation(job_id, "other-task")
    fake.container["Id"] = "replacement"
    with pytest.raises(Failure, match="容器 ID"):
        manager.observation(job_id, state["task_id"])


def test_failed_output_collection_recovers_without_reexecution_or_success(client, app, monkeypatch):
    fake = ExecutorFixture()
    state, manager, job_id = submit(client, app, fake)
    manager.tick()
    output = Path(manager.read(job_id)['directory']) / 'output'
    (output / 'result.json').write_bytes(b'{"count":3,"mean":233.333333}')
    (output / 'calculation.py').write_bytes(b'# actual generated source')
    fake.container['State'].update(Status='exited', Running=False, ExitCode=1)
    original_import = manager.resources.import_file
    def lost_copy_response(*args):
        original_import(*args)
        raise Failure('EXECUTOR_UNAVAILABLE', 'copy receipt lost', 503)
    monkeypatch.setattr(manager.resources, 'import_file', lost_copy_response)
    manager.tick()
    interrupted = manager.read(job_id)
    assert interrupted['status'] == 'collecting' and interrupted['collection_outcome'] == 'failed'
    monkeypatch.setattr(manager.resources, 'import_file', original_import)
    recovered = Jobs(manager.config, manager.store, manager.resources, fake)
    recovered.tick()
    job = recovered.read(job_id)
    assert job['status'] == 'failed' and job['exit_code'] == 1 and job['partial_outputs']
    assert len(job['artifact_ids']) == 2
    observed = recovered.observation(job_id, state['task_id'])
    assert observed['output_review']['answers_task'] == 'not_assessed'
    actual = {recovered.resources.artifact(ref)['name']: ref for ref in job['artifact_ids']}
    assert recovered.resources.read_artifact(state['task_id'], actual['result.json'])['text'] == '{"count":3,"mean":233.333333}'
    (output / 'result.json').write_bytes(b'changed working bytes')
    assert recovered.resources.read_artifact(state['task_id'], actual['result.json'])['text'] == '{"count":3,"mean":233.333333}'
    with pytest.raises(Failure):
        recovered.observation(job_id, 'other-task')
    assert len(app.state.store.task(state['task_id'])['artifact_ids']) == 2
    recovered.tick()
    assert recovered.read(job_id)['artifact_ids'] == job['artifact_ids']
    assert sum(c[0] == 'create' for c in fake.commands) == sum(c[0] == 'start' for c in fake.commands) == 1


def test_docker_logs_keep_bounded_exception_tail(app, monkeypatch):
    from types import SimpleNamespace
    stderr = b'large code frame\n' * 500 + b"NameError: name 'json' is not defined\n"
    stdout = b'earlier output\n' * 6000 + b'CALCULATION_ALREADY_FINISHED\n'
    def run(command, *, stdout: object, stderr: object, **kwargs):
        stdout.write(log_stdout)
        stderr.write(log_stderr)
        return SimpleNamespace(returncode=0)
    log_stdout, log_stderr = stdout, stderr
    monkeypatch.setattr('forestry_agent.jobs.subprocess.run', run)
    result = Docker(app.state.engine.config).call('logs', '--tail', '200', 'container-a')
    assert result['stdout'].endswith('CALCULATION_ALREADY_FINISHED\n')
    assert result['stderr'].endswith("NameError: name 'json' is not defined\n")
    assert len(result['stdout'].encode()) <= 65536 and len(result['stderr'].encode()) <= 4096
    assert result['stdout_truncated'] and result['stderr_truncated'] and result['window'] == 'tail'
