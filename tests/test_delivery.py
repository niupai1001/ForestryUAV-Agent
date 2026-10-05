import json
from pathlib import Path

import pytest
from test_core import task

from forestry_agent.contracts import Failure
from forestry_agent.store import now


def ready(client, app):
    state, _ = task(client)
    artifact = app.state.resources.write(state["task_id"], "answer-action", "report.md", "# 实测\n均值为 12.5。")
    def respond(s, db):
        s.update(status="waiting_user")
        s["messages"].append({"id": "answer-1", "role": "assistant", "text": "已读取输入并生成报告。",
            "artifact_ids": [artifact["artifact_id"]], "limitations": ["没有现场精度验证"],
            "turn_id": "turn-1", "spec_revision": 1, "at": now()})
    app.state.store.change(state["task_id"], "fixture.answer", respond)
    candidate = client.get(f"/tasks/{state['task_id']}/acceptance-candidate").json()
    payload = {"request_id": "accept-1", "expected_state_version": candidate["state_version"],
        "expected_spec_revision": 1, "candidate_sha256": candidate["candidate_sha256"],
        "verdict": "accept", "comment": "测试夹具验收"}
    return state["task_id"], artifact, candidate, payload


def test_only_explicit_acceptance_can_publish_and_replay_is_identical(client, app):
    task_id, artifact, candidate, payload = ready(client, app)
    assert app.state.store.task(task_id)["status"] == "waiting_user"
    result = client.post(f"/tasks/{task_id}/acceptance", json=payload)
    assert result.status_code == 200, result.text
    state = result.json()
    assert state["status"] == "completed"
    manifest_artifact = app.state.resources.artifact(state["delivery"]["manifest_artifact_id"])
    manifest = json.loads(Path(manifest_artifact["locator"]).read_text(encoding="utf-8"))
    assert manifest["candidate_sha256"] == candidate["candidate_sha256"]
    assert manifest["candidate"]["outputs"][0]["sha256"] == artifact["sha256"]
    assert manifest["acceptance"]["semantic_check"] == "user_attested"
    assert manifest["acceptance"]["scientific_accuracy"] == "not_assessed"
    assert manifest_artifact["artifact_id"] not in [a["artifact_id"] for a in manifest["candidate"]["outputs"]]
    assert client.post(f"/tasks/{task_id}/acceptance", json=payload).json() == state
    assert client.post(f"/tasks/{task_id}/acceptance", json={**payload, "comment": "changed"}).status_code == 409
    kinds = [e["type"] for e in app.state.store.events(task_id)]
    assert kinds.index("delivery.seal_intent") < kinds.index("delivery.published") < kinds.index("task.terminal")


def test_request_changes_retains_outputs_and_does_not_publish(client, app):
    task_id, artifact, _, payload = ready(client, app)
    result = client.post(f"/tasks/{task_id}/acceptance", json={**payload, "verdict": "request_changes", "comment": "解释计算来源"})
    assert result.json()["status"] == "paused"
    assert result.json()["acceptance"]["semantic_check"] == "rejected"
    assert artifact["artifact_id"] in result.json()["artifact_ids"]
    assert not any(e["type"] == "delivery.published" for e in app.state.store.events(task_id))


def test_revision_conflict_and_candidate_hash_are_authoritative(client, app):
    task_id, _, _, payload = ready(client, app)
    assert client.post(f"/tasks/{task_id}/acceptance", json={**payload, "candidate_sha256": "0" * 64}).status_code == 409
    assert client.post(f"/tasks/{task_id}/acceptance", json={**payload, "expected_spec_revision": 2}).status_code == 409
    assert app.state.store.task(task_id)["status"] == "waiting_user"


def test_tampered_output_prevents_acceptance(client, app):
    task_id, artifact, _, payload = ready(client, app)
    Path(artifact["locator"]).write_text("tampered")
    assert client.post(f"/tasks/{task_id}/acceptance", json=payload).status_code == 422
    assert app.state.store.task(task_id)["status"] == "waiting_user"


def test_recovery_after_manifest_sealed_before_terminal_is_idempotent(client, app, monkeypatch):
    task_id, _, _, payload = ready(client, app)
    original = app.state.resources.import_file
    def fail_after_seal(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("injected crash after durable artifact")
    monkeypatch.setattr(app.state.resources, "import_file", fail_after_seal)
    with pytest.raises(OSError):
        app.state.delivery.accept(task_id, payload)
    state = app.state.store.task(task_id)
    assert state["status"] == "publishing" and len(state["artifact_ids"]) == 2
    assert client.post(f"/tasks/{task_id}/cancel", json={"request_id": "cancel", "expected_state_version": state["state_version"]}).status_code == 409
    monkeypatch.setattr(app.state.resources, "import_file", original)
    app.state.delivery.recover()
    recovered = app.state.store.task(task_id)
    assert recovered["status"] == "completed" and len(recovered["artifact_ids"]) == 2
    assert client.post(f"/tasks/{task_id}/acceptance", json=payload).json() == recovered


def test_active_tasks_cannot_forge_acceptance(client, app):
    state, _ = task(client)
    assert client.get(f"/tasks/{state['task_id']}/acceptance-candidate").status_code == 422
    with pytest.raises(Failure):
        app.state.delivery.candidate(state["task_id"])
