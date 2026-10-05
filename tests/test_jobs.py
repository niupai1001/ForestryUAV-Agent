import copy
import hashlib
from pathlib import Path

import pytest
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.jobs import Jobs


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


def test_job_scope_and_pause_before_dispatch(client, app):
    fake = ExecutorFixture()
    state, manager, job_id = submit(client, app, fake)
    app.state.store.change(state["task_id"], "pause", lambda s, _: s.update(pause_requested=True))
    manager.tick()
    assert manager.read(job_id)["status"] == "queued" and not fake.commands
    with pytest.raises(Failure):
        manager.read(job_id, "another-task")
