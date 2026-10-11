"""New staged canopy permission and replay boundary; no scientific/mock quality claims."""
from pathlib import Path

from test_lidar_terrain import setup


def test_canopy_stage_roles_grants_and_fixed_image(tmp_path):
    _, app, client, state, payload, path = setup(tmp_path)
    # New method must be explicitly requested in this Task revision.
    amendment = client.post(f"/tasks/{state['task_id']}/amendments", json={
        "request_id": "canopy-method", "expected_spec_revision": 1, "objective": "复用工作云的冠层阶段",
        "input_refs": [payload["cloud_ref"], payload["declaration_ref"]],
        "requested_method_id": "lidar.p2r_lmf_dalponte.v1",
        "constraints": {"allowed_capabilities": ["code.execute", "job.observe"]}}).json()
    client.post(f"/tasks/{state['task_id']}/resume", json={"request_id": "canopy-resume",
                "expected_state_version": amendment["state_version"]})
    url = f"/tasks/{state['task_id']}/lidar/canopy"
    request = {"request_id": "first-chm", "expected_spec_revision": 2, "stage": "chm",
               "input_refs": {"working": payload["cloud_ref"], "terrain_quality": payload["declaration_ref"],
                              "terrain_support": payload["cloud_ref"]}}
    invalid = client.post(url, json={**request, "stage": "instances"})
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "SCHEMA_INVALID"
    extra = client.post("/resources/register", json={"request_id": "not-task-granted", "path": str(path),
                        "read_only": True}).json()["resource_id"]
    denied = client.post(url, json={**request, "request_id": "denied-chm",
                                  "input_refs": {**request["input_refs"], "working": extra}}).json()
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    assert not app.state.engine.jobs.list(state["task_id"])["jobs"]
    first = client.post(url, json=request).json()
    assert first["kind"] == "completed"
    job = app.state.engine.jobs.read(first["job"]["job_id"])
    compile(job["code"], "rendered-canopy", "exec")
    assert len(job["code"].encode()) < 65536
    replay = client.post(url, json={**request, "request_id": "equivalent-second",
                                  "input_refs": dict(reversed(list(request["input_refs"].items()))) }).json()
    assert replay["job"]["job_id"] == first["job"]["job_id"]
    assert len(app.state.engine.jobs.list(state["task_id"])["jobs"]) == 1
    # Registration alone never means these fixture files are scientifically valid.
    assert Path(path).is_file()
    client.close()
