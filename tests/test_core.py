from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from forestry_agent.agent import Deps
from forestry_agent.config import Config, ModelProfile
from forestry_agent.contracts import Failure


def task(client, **extra):
    payload = {"request_id": "create-1", "objective": "读取输入并生成报告", "input_refs": [], **extra}
    reply = client.post("/tasks", json=payload)
    assert reply.status_code == 202, reply.text
    return reply.json(), payload


def test_authenticated_origin_and_dns_rebinding(app):
    with TestClient(app, base_url="http://127.0.0.1") as browser:
        assert browser.get("/tasks").status_code == 403
        browser.get("/session")
        assert browser.post("/tasks", json={"request_id":"x","objective":"hi"}).status_code == 403
        assert browser.get("/tasks", headers={"origin":"https://attacker.example"}).status_code == 403
        assert browser.get("/session", headers={"host":"attacker.example"}).status_code == 403
        assert browser.get("/health").status_code == 200


def test_request_replay_and_payload_conflict(client):
    state, payload = task(client)
    assert client.post("/tasks", json=payload).json()["task_id"] == state["task_id"]
    assert len(client.get("/tasks").json()) == 1
    assert client.post("/tasks", json={**payload,"objective":"different"}).status_code == 409


def test_forged_grant_and_unknown_inputs_rejected(client):
    assert client.post("/tasks", json={"request_id":"bad","objective":"hi","user_event_id":"forged"}).status_code == 400
    assert client.post("/tasks", json={"request_id":"bad2","objective":"hi","input_refs":["fake"]}).status_code == 403
    assert client.post("/tasks", json={"request_id":"bad3","objective":"hi","requested_method_id":"lidar.smrf_idw.v1"}).status_code == 422


def test_source_readonly_scope_and_versions(client,app,tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    file = source / "data.csv"
    file.write_text("tree,height\n1,8\n2,12\n", encoding="utf-8")
    grant = client.post("/resources/register", json={"request_id":"grant","path":str(source)}).json()
    state,_ = task(client, input_refs=[grant["resource_id"]])
    result = app.state.resources.read(grant["resource_id"], "data.csv", task_id=state["task_id"])
    assert "2,12" in result["text"]
    before = result["version"]
    file.write_text("tree,height\n1,9\n2,12\n", encoding="utf-8")
    assert app.state.resources.read(grant["resource_id"],"data.csv")["version"] != before
    with pytest.raises(Failure):
        app.state.resources.resolve(grant["resource_id"],"../outside.txt",state["task_id"])
    other,_ = task(client, request_id="other")
    with pytest.raises(Failure):
        app.state.resources.resolve(grant["resource_id"],"data.csv",other["task_id"])


def test_actions_are_durable_and_replay_does_not_reexecute(client,app):
    state,_ = task(client)
    deps = Deps(app.state.engine,state["task_id"],"turn-a",1)
    observed = []
    def execute(action_id):
        with app.state.store.transaction() as db:
            assert db.execute("SELECT status FROM actions WHERE id=?",(action_id,)).fetchone()[0] == "pending"
        observed.append(action_id)
        return {"value":42}
    one = app.state.engine.perform(deps,"call-1","fs.write_workspace",{"x":1},execute)
    two = app.state.engine.perform(deps,"call-1","fs.write_workspace",{"x":1},execute)
    assert one == two and len(observed) == 1
    app.state.engine.perform(deps,"call-2","fs.write_workspace",{"x":1},execute)
    assert len(observed) == 2
    with pytest.raises(Failure):
        app.state.engine.perform(deps,"call-1","fs.write_workspace",{"x":2},execute)
    assert app.state.store.task(state["task_id"])["action_count"] == 2


def test_immutable_artifacts_and_tamper_detection(client,app):
    state,_ = task(client)
    root = app.state.resources.task_root(state["task_id"])
    a = app.state.resources.write(state["task_id"],"action-a","report.md","original")
    b = app.state.resources.write(state["task_id"],"action-b","report.md","new")
    assert (root/"report.md").read_text() == "new"
    assert client.get(a["preview_url"]).text == "original"
    assert a["artifact_id"] != b["artifact_id"]
    Path(a["locator"]).write_text("tampered")
    assert client.get(a["preview_url"]).status_code == 422
    with pytest.raises(Failure):
        app.state.resources.write(state["task_id"],"action-c","../../other.txt","no")


def test_text_write_preserves_supplied_utf8_and_mixed_newlines(client, app):
    state, _ = task(client)
    content = "中文🙂\nLF\r\nCRLF\rCR\n"
    saved = app.state.resources.write(state["task_id"], "exact-byte-write", "mixed.txt", content)
    assert Path(saved["locator"]).read_bytes() == content.encode("utf-8")
    assert saved["size_bytes"] == len(content.encode("utf-8"))
    assert (app.state.resources.task_root(state["task_id"]) / "mixed.txt").read_bytes() == content.encode("utf-8")


def test_messages_do_not_revise_but_amendments_do(client,app):
    state,_ = task(client)
    msg = {"request_id":"msg","expected_spec_revision":1,"text":"请解释进度"}
    assert client.post(f"/tasks/{state['task_id']}/messages",json=msg).json()["spec_revision"] == 1
    patch = {"request_id":"amend","expected_spec_revision":1,"objective":"修改目标","input_refs":[]}
    result = client.post(f"/tasks/{state['task_id']}/amendments",json=patch)
    assert result.status_code == 200 and result.json()["spec_revision"] == 2
    assert client.post(f"/tasks/{state['task_id']}/amendments",json=patch).json()["spec_revision"] == 2
    with pytest.raises(Failure):
        app.state.engine.perform(Deps(app.state.engine,state["task_id"],"old-turn",1),"old","x",{},lambda _: {})
    with app.state.store.transaction() as db:
        assert len(db.execute("SELECT * FROM specs WHERE task_id=?",(state["task_id"],)).fetchall()) == 2


def test_cancel_confirmed_and_terminal_task_immutable(client,app):
    state,_ = task(client)
    control = {"request_id":"cancel","expected_state_version":state["state_version"]}
    reply = client.post(f"/tasks/{state['task_id']}/cancel",json=control)
    assert reply.json()["status"] == "cancelled"
    assert client.post(f"/tasks/{state['task_id']}/cancel",json=control).json() == reply.json()
    assert client.post(f"/tasks/{state['task_id']}/resume",json={"request_id":"resume","expected_state_version":2}).status_code == 409


def test_restart_keeps_facts_and_pauses_unreconciled_actions(client,app):
    state,_ = task(client)
    app.state.store.change(state["task_id"],"fixture.started",lambda s,_: s.update(status="active"))
    app.state.engine.reconcile_startup()
    recovered = app.state.store.task(state["task_id"])
    assert recovered["status"] == "paused"
    assert recovered["blockers"][0]["code"] == "SERVICE_RESTARTED"
    events = app.state.store.events(state["task_id"])
    assert len({e["event_id"] for e in events}) == len(events)
    assert [e["sequence"] for e in events] == sorted(e["sequence"] for e in events)


def test_html_preview_is_sandboxed(client,app):
    state,_ = task(client)
    artifact = app.state.resources.write(state["task_id"],"html","report.html","<script>fetch('https://evil.example')</script>")
    reply = client.get(artifact["preview_url"])
    assert "sandbox" in reply.headers["content-security-policy"]
    assert "default-src 'none'" in reply.headers["content-security-policy"]


def test_cloud_model_endpoint_rejected(tmp_path):
    with pytest.raises(ValidationError):
        Config(workspace_root=tmp_path,models={"small":ModelProfile(repository="x",served_name="x")},
               model_base_url="https://api.example/v1")


def test_settings_cas_and_task_snapshot(client):
    settings = client.get("/settings").json()
    result = client.put("/settings",json={"request_id":"settings","expected_version":settings["version"],"default_model":"small"})
    assert result.status_code == 200
    assert client.put("/settings",json={"request_id":"settings2","expected_version":1,"default_model":"small"}).status_code == 409
    state,_ = task(client)
    assert state["config_snapshot"]["settings_version"] == 2
