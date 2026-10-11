"""New scientific route only; no Docker, model, generic workflow or science accuracy claim."""
import ast
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from test_cloud_inputs import cloud

from forestry_agent.app import create_app
from forestry_agent.config import Config, ModelProfile

IMAGE = "sha256:" + "a" * 64


def setup(tmp_path, *, z_semantics="elevation", crs=2154):
    config = Config(workspace_root=tmp_path / "runtime", science_image=IMAGE,
                    models={"small": ModelProfile(repository="Qwen/Qwen3.5-4B", served_name="qwen3.5:4b")})
    app = create_app(config, enable_agent=False)
    path = cloud(tmp_path, crs=crs)
    declared = tmp_path / "declared.json"
    declared.write_text(json.dumps({"source_note": "explicit operator fixture, not surveyed truth",
                                   "z_unit": "m", "vertical_ref": "EGM96", "z_semantics": z_semantics}))
    client = TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
    client.get("/session")
    refs = [client.post("/resources/register", json={"request_id": str(uuid4()), "path": str(p),
                                                   "read_only": True}).json()["resource_id"] for p in (path, declared)]
    state = client.post("/tasks", json={"request_id": str(uuid4()), "objective": "真实任务指定方法保持",
        "requested_method_id": "lidar.smrf_idw.v1", "input_refs": refs,
        "constraints": {"allowed_capabilities": ["code.execute", "job.observe"]}}).json()
    payload = {"request_id": "one-terrain", "expected_spec_revision": 1,
               "cloud_ref": refs[0], "declaration_ref": refs[1]}
    return config, app, client, state, payload, path


def test_science_image_is_task_frozen_and_replay_does_not_recompute(tmp_path):
    config, app, client, state, payload, path = setup(tmp_path)
    before = path.read_bytes()
    config.science_image = "sha256:" + "b" * 64
    url = f"/tasks/{state['task_id']}/lidar/terrain"
    result = client.post(url, json=payload).json()
    assert result["kind"] == "completed"
    assert result["job"]["image_reference"] == IMAGE
    replay = client.post(url, json=payload).json()
    assert replay["job"]["job_id"] == result["job"]["job_id"]
    assert len(app.state.engine.jobs.list(state["task_id"])["jobs"]) == 1
    job = app.state.engine.jobs.read(result["job"]["job_id"])
    compile(job["code"], "rendered-terrain", "exec")
    assert path.read_bytes() == before
    client.close()


def test_already_normalized_cloud_cannot_be_normalized_again(tmp_path):
    _, app, client, state, payload, _ = setup(tmp_path, z_semantics="height_above_ground")
    result = client.post(f"/tasks/{state['task_id']}/lidar/terrain", json=payload).json()
    assert result["error"]["code"] == "PRECONDITION_MISSING"
    assert not app.state.engine.jobs.list(state["task_id"])["jobs"]
    client.close()


def test_unknown_crs_and_infeasible_method_are_not_silently_replaced(tmp_path):
    _, app, client, state, payload, _ = setup(tmp_path, crs=None)
    result = client.post(f"/tasks/{state['task_id']}/lidar/terrain", json=payload).json()
    assert result["error"]["code"] == "PRECONDITION_MISSING"
    rejected = client.post("/tasks", json={"request_id": "other-method", "objective": "保持指定方法",
                                         "requested_method_id": "lidar.other.v1"})
    assert rejected.status_code == 422
    assert not app.state.engine.jobs.list(state["task_id"])["jobs"]
    client.close()


def test_science_processing_respects_existing_task_input_grants(tmp_path):
    _, app, client, state, payload, path = setup(tmp_path)
    other = client.post("/resources/register", json={"request_id": "extra", "path": str(path),
                                                    "read_only": True}).json()["resource_id"]
    result = client.post(f"/tasks/{state['task_id']}/lidar/terrain", json={**payload,"cloud_ref": other}).json()
    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert not app.state.engine.jobs.list(state["task_id"])["jobs"]
    client.close()


def test_resume_cannot_replace_prior_job_artifacts_with_other_granted_inputs(tmp_path):
    _, app, client, state, payload, _ = setup(tmp_path)
    url = f"/tasks/{state['task_id']}/lidar/terrain"
    first = client.post(url, json=payload).json()
    jobs = app.state.engine.jobs
    job = jobs.read(first["job"]["job_id"])
    # Contract fixture only: no container or scientific calculation.
    request = ast.literal_eval(job["code"].splitlines()[0].split("=", 1)[1].strip())
    artifact = app.state.engine.resources.write(state["task_id"], job["action_id"],
                                               "request.json", json.dumps(request))
    job.update(status="failed", artifact_ids=[artifact["artifact_id"]])
    jobs.save(job, "fixture.only.failed")
    from forestry_agent.lidar_terrain import RESUME_FILES

    result = client.post(url, json={**payload, "request_id": "bad-reuse", "resume_job_id": job["job_id"],
        "resume_refs": {name: payload["cloud_ref"] for name in RESUME_FILES}}).json()
    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert len(jobs.list(state["task_id"])["jobs"]) == 1
    client.close()


def test_operator_and_native_equivalent_science_requests_reuse_original_job(tmp_path):
    _, app, client, state, payload, _ = setup(tmp_path)
    url = f"/tasks/{state['task_id']}/lidar/terrain"
    first = client.post(url, json=payload).json()
    second = client.post(url, json={**payload, "request_id": "another-science-call"}).json()
    assert second["job"]["job_id"] == first["job"]["job_id"]
    assert second["original_producer_action_id"] == first["action_id"]
    assert len(app.state.engine.jobs.list(state["task_id"])["jobs"]) == 1
    assert second["action_id"] != first["action_id"]
    client.close()


def test_equivalent_science_request_requires_revision_when_input_version_changes(tmp_path):
    _, app, client, state, payload, path = setup(tmp_path)
    url = f"/tasks/{state['task_id']}/lidar/terrain"
    first = client.post(url, json=payload).json()
    # Keep valid LAS bytes; the changed file version must still prevent stale reuse.
    import os

    before = path.stat()
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    result = client.post(url, json={**payload, "request_id": "changed-input-version"}).json()
    assert result["error"]["code"] == "VERSION_CONFLICT"
    jobs = app.state.engine.jobs.list(state["task_id"])["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == first["job"]["job_id"]
    client.close()
