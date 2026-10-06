import hashlib
import http.client
import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.network import MAX_BYTES, PublicHTTP, TextPage, decode_text, search_results
from forestry_agent.resources import native_path
from forestry_agent.store import encode


def dns(addresses):
    return lambda host, port, **_: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.2"], ["169.254.169.254"], ["::1"], ["fd00::1"], ["::ffff:127.0.0.1"], ["224.0.0.1"], ["ff0e::1"], ["8.8.8.8", "192.168.0.1"]])
def test_ssrf_rejects_every_private_or_mixed_dns_result(addresses):
    http = PublicHTTP(resolver=dns(addresses), exchange=lambda *args: pytest.fail("private connection attempted"))
    with pytest.raises(Failure, match="DNS地址"):
        http.get("https://public-looking.example/page")


@pytest.mark.parametrize("url", ["file:///secret.txt", "https://user:secret@example.org/", "https://example.org:22/", "https://example.org/\r\nInjected", "https://example.org\\@localhost/"])
def test_invalid_schemes_credentials_ports_and_header_injection_do_not_resolve(url):
    http = PublicHTTP(resolver=lambda *a, **kw: pytest.fail("invalid URL resolved"))
    with pytest.raises(Failure):
        http.get(url)


def test_dns_is_pinned_once_and_redirect_is_rechecked_before_connection():
    resolutions, connections = [], []
    def resolve(host, port, **kwargs):
        resolutions.append(host)
        return dns(["8.8.8.8"] if host == "safe.example" else ["127.0.0.1"])(host, port)
    def exchange(url, host, port, address):
        connections.append((host, address))
        return 302, {"location": "https://localhost/private"}, b""
    with pytest.raises(Failure, match="DNS地址"):
        PublicHTTP(resolve, exchange).get("https://safe.example/")
    assert resolutions == ["safe.example", "localhost"] and connections == [("safe.example", "8.8.8.8")]


def test_redirect_cannot_expand_private_grant_or_downgrade_tls():
    for location, expected in [("https://other.example/secret", "目的地"), ("http://safe.example/secret", "降级")]:
        http = PublicHTTP(dns(["8.8.8.8"]), lambda *args, location=location: (302, {"location": location}, b""))
        with pytest.raises(Failure, match=expected):
            http.get("https://safe.example/", allowed_origins=["https://safe.example"])


def test_response_size_and_redirect_count_are_bounded():
    with pytest.raises(Failure, match="2 MiB"):
        PublicHTTP(dns(["8.8.8.8"]), lambda *a: (200, {}, b"x" * (MAX_BYTES + 1))).get("https://safe.example/")
    with pytest.raises(Failure, match="次数"):
        PublicHTTP(dns(["8.8.8.8"]), lambda *a: (302, {"location": "/again"}, b"")).get("https://safe.example/")


class HTTPFixture:
    def __init__(self):
        self.requests = []
        self.body = b'<h1>fixture value 42</h1><script>steal private memory</script><p>source observation</p>'
    def get(self, url, **kwargs):
        kwargs["guard"](url)
        self.requests.append(url)
        return {"url": url, "headers": {"content-type": "text/html"}, "body": self.body,
            "hops": [{"url": url, "address": "8.8.8.8", "status": 200}], "observed_at": datetime.now(UTC).isoformat()}


def read(engine, state, call="read", url="https://safe.example/page", approval=None):
    deps = Deps(engine, state["task_id"], "network-turn", state["spec_revision"])
    return engine.perform(deps, call, "web.read", {"url": url, "approval_id": approval},
        lambda action: engine.network.execute(deps, action, "web.read", url, approval))


def resolve(client, state, record, verdict="approve", request_id="approve"):
    return client.post(f"/tasks/{state['task_id']}/approvals/{record['approval_id']}", json={
        "request_id": request_id, "expected_spec_revision": state["spec_revision"],
        "request_sha256": record["request_sha256"], "verdict": verdict})


def test_private_payload_never_leaves_before_exact_user_approval_and_replay_is_one_effect(client, app):
    state, _ = task(client, objective="SYNTHETIC_PRIVATE_SECRET")
    engine = app.state.engine
    fake = HTTPFixture()
    engine.network.http = fake
    url = "https://safe.example/?q=SYNTHETIC_PRIVATE_SECRET"
    proposed = read(engine, state, url=url)
    assert proposed["kind"] == "approval_required" and not fake.requests
    record = client.get(f"/tasks/{state['task_id']}/approvals").json()[0]
    assert "SYNTHETIC_PRIVATE_SECRET" in record["request"]["url"]
    reply = resolve(client, state, record)
    assert reply.status_code == 200 and reply.json()["authorizing_user_event_id"]
    assert resolve(client, state, record).json() == reply.json()
    mismatch = read(engine, state, call="different", url=url + "changed", approval=record["approval_id"])
    assert mismatch["error"]["code"] == "VERSION_CONFLICT" and not fake.requests
    result = read(engine, state, call="approved", url=url, approval=record["approval_id"])
    assert result["kind"] == "completed" and "42" in result["text"] and "steal" not in result["text"]
    assert read(engine, state, call="approved", url=url, approval=record["approval_id"]) == result and len(fake.requests) == 1
    denied = read(engine, state, call="reuse", url=url, approval=record["approval_id"])
    assert denied["error"]["code"] == "PERMISSION_DENIED" and len(fake.requests) == 1
    raw = engine.resources.artifact(result["citation"]["snapshot_artifact_id"])
    assert native_path(Path(raw["locator"])).read_bytes() == fake.body
    assert result["citation"]["source_sha256"] == hashlib.sha256(fake.body).hexdigest() == raw["sha256"]


@pytest.mark.parametrize("condition", ["expired", "revoked", "rejected", "other_task", "changed_source", "changed_revision"])
def test_stale_or_foreign_approvals_do_not_send(client, app, condition):
    source = app.state.engine.config.workspace_root.parent / "private.txt"
    source.write_text("SYNTHETIC_SECRET", encoding="utf-8")
    ref = client.post("/resources/register", json={"request_id": "private", "path": str(source)}).json()["resource_id"]
    state, _ = task(client, input_refs=[ref])
    engine = app.state.engine
    fake = HTTPFixture()
    engine.network.http = fake
    proposed = read(engine, state)
    record = engine.network.list(state["task_id"])[0]
    resolve(client, state, record, verdict="reject" if condition == "rejected" else "approve")
    if condition == "expired":
        with engine.store.transaction() as db:
            value = json.loads(db.execute("SELECT payload FROM approvals WHERE id=?", (record["approval_id"],)).fetchone()[0])
            value["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            db.execute("UPDATE approvals SET payload=? WHERE id=?", (encode(value), record["approval_id"]))
    elif condition == "revoked":
        assert resolve(client, state, record, "revoke", "revoke").status_code == 200
    elif condition == "other_task":
        state, _ = task(client, request_id="other")
    elif condition == "changed_source":
        source.write_text("changed", encoding="utf-8")
    elif condition == "changed_revision":
        client.post(f"/tasks/{state['task_id']}/amendments", json={"request_id": "amend", "expected_spec_revision": 1, "objective": "changed", "input_refs": [ref]})
        current = engine.store.task(state["task_id"])
        engine.store.change(state["task_id"], "fixture.unpause", lambda s, _: s.update(pause_requested=False))
        state = current
    result = read(engine, state, call="after-control", approval=proposed["approval_id"])
    assert result["kind"] == "failed" and not fake.requests


def test_public_context_cannot_add_private_sources_or_read_private_memory(client, app):
    state, _ = task(client, context_classification="public")
    engine = app.state.engine
    engine.network.http = HTTPFixture()
    assert read(engine, state)["kind"] == "completed"
    assert not engine.network.list(state["task_id"])
    root = Deps(engine, state["task_id"], "public", 1)
    for kind in ["memory.read", "environment.inspect"]:
        with pytest.raises(Failure, match="私密"):
            engine.perform(root, kind, kind, {}, lambda _: pytest.fail("private context escaped"))
    source = engine.config.workspace_root.parent / "private-source.txt"
    source.write_text("private", encoding="utf-8")
    ref = client.post("/resources/register", json={"request_id": "private", "path": str(source)}).json()["resource_id"]
    assert client.post("/tasks", json={"request_id": "bad-public", "objective": "public", "context_classification": "public", "input_refs": [ref]}).status_code == 403
    assert client.post(f"/tasks/{state['task_id']}/amendments", json={"request_id": "bad-amend", "expected_spec_revision": 1, "objective": "public", "input_refs": [ref]}).status_code == 403


def test_pending_network_scope_prevents_acceptance_and_untrusted_html_cannot_execute(client, app):
    state, _ = task(client)
    app.state.engine.network.http = HTTPFixture()
    read(app.state.engine, state)
    app.state.store.change(state["task_id"], "fixture.answer", lambda s, _: s.update(status="waiting_user"))
    with pytest.raises(Failure, match="网络请求"):
        app.state.delivery.candidate(state["task_id"])
    assert TextPage.extract('<p>42</p><style>secret</style><script src="https://attacker.example"/>') == "42"


@pytest.mark.parametrize("response", [b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n42", b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n42"])
def test_real_http_parser_handles_connection_close_without_reresolving(monkeypatch, response):
    client_socket, server_socket = socket.socketpair()
    requests = []
    def server():
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                request += server_socket.recv(4096)
            requests.append(request)
            server_socket.sendall(response)
        finally:
            server_socket.close()
    worker = threading.Thread(target=server, daemon=True)
    worker.start()
    connections, guards = [], []
    def connect(destination, timeout):
        connections.append(destination)
        return client_socket
    monkeypatch.setattr(socket, "create_connection", connect)
    status, _, body = PublicHTTP._exchange("http://safe.example/?q=public", "safe.example", 80, "8.8.8.8", guards.append, time.monotonic()+2)
    worker.join(timeout=2)
    assert status == 200 and body == b"42"
    assert connections == [("8.8.8.8", 80)] and len(guards) >= 2
    assert b"Host: safe.example" in requests[0] and b"GET /?q=public HTTP/1.1" in requests[0]
    assert b"Authorization:" not in requests[0] and b"Cookie:" not in requests[0]


@pytest.mark.parametrize("prefix", [b"", b"HTTP/1.0 200 OK\r\nContent-Length: 10000\r\n\r\n"])
def test_total_deadline_stops_dripping_headers_and_body(monkeypatch, prefix):
    client_socket, server_socket = socket.socketpair()
    stop = threading.Event()
    def server():
        try:
            request = b""
            while b"\r\n\r\n" not in request:
                request += server_socket.recv(4096)
            server_socket.sendall(prefix)
            while not stop.wait(0.025):
                server_socket.sendall(b"x")
        except OSError:
            pass
        finally:
            server_socket.close()
    worker = threading.Thread(target=server, daemon=True)
    worker.start()
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: client_socket)
    started = time.monotonic()
    try:
        with pytest.raises((Failure, OSError, ValueError, http.client.HTTPException)):
            PublicHTTP._exchange("http://safe.example/", "safe.example", 80, "8.8.8.8", lambda _: None, started+0.2)
        assert time.monotonic()-started < 1.5
    finally:
        stop.set()
        worker.join(timeout=2)
        client_socket.close()


def test_private_proposal_has_no_dns_and_revocation_after_dns_has_no_connection(client, app):
    state, _ = task(client)
    engine = app.state.engine
    resolved, sent = [], []
    def resolver(host, port, **kwargs):
        resolved.append(host)
        record = engine.network.list(state["task_id"])[0]
        assert resolve(client, state, record, "revoke", "late-revoke").status_code == 200
        return dns(["8.8.8.8"])(host, port)
    engine.network.http = PublicHTTP(resolver, lambda *args: sent.append(args))
    read(engine, state)
    assert not resolved and not sent
    record = engine.network.list(state["task_id"])[0]
    assert resolve(client, state, record).status_code == 200
    result = read(engine, state, "after-approval", approval=record["approval_id"])
    assert result["kind"] == "failed" and result["error"]["code"] == "PERMISSION_DENIED"
    assert resolved == ["safe.example"] and not sent


@pytest.mark.parametrize("provider,text", [("bing_rss", "<html/>"), ("bing_rss", '<!DOCTYPE rss [<!ENTITY a "private">]><rss/>'), ("searxng", "{}"), ("searxng", '{"results":{}}'), ("searxng", '{"results":[42]}')])
def test_search_does_not_mislabel_error_pages_as_zero_results(provider, text):
    with pytest.raises(Failure):
        search_results(provider, text)


def test_declared_charset_preserves_text_and_nontext_codec_is_rejected():
    assert decode_text("林业调查".encode("gb18030"), "text/html; charset=gb18030") == "林业调查"
    with pytest.raises(Failure):
        decode_text(b"private", "text/plain; charset=base64_codec")
