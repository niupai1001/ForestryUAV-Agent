"""Tests for the local scorecard viewer.

The viewer is the only place a human reads a score, so two classes of mistake
matter most and are pinned here:

* it must never present an absent or unreadable scorecard as a real score, and
  it must not be able to read outside the directory it was pointed at;
* ``start_run`` must return promptly. An earlier version re-acquired a
  non-reentrant ``Lock`` on the same thread and blocked forever, which made the
  page's run button hang with no error. That case is covered directly.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from evaluation import dashboard
from evaluation.scorecard import CONFIG_FIELDS


ROOT = Path(__file__).resolve().parents[1]


def start_server(root: Path) -> tuple[ThreadingHTTPServer, str]:
    dashboard.Handler.root = root
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def post(url: str, payload) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


class FailureReasonTests(unittest.TestCase):
    """A crash and a gate failure share exit code 1 but mean different things."""

    def test_success_and_real_gate_failure_report_nothing_extra(self):
        self.assertIsNone(dashboard.failure_reason(0, "", None))
        self.assertIsNone(
            dashboard.failure_reason(1, "no traceback here", None),
            "exit 1 without a traceback is a real blocked verdict",
        )

    def test_a_traceback_is_reported_as_a_crash_not_a_gate_failure(self):
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            "RuntimeError: Incomplete trial directory must be reviewed: /tmp/x"
        )
        reason = dashboard.failure_reason(1, stderr, None)
        self.assertIsNotNone(reason)
        self.assertIn("crashed", reason)
        self.assertIn("earlier run", reason, "the reader must know the page is stale")
        self.assertIn("RuntimeError", reason)

    def test_incomplete_evidence_is_explained(self):
        reason = dashboard.failure_reason(2, "", None)
        self.assertIn("incomplete", reason)

    def test_an_external_error_takes_precedence(self):
        self.assertEqual(
            dashboard.failure_reason(None, "", "the run timed out"),
            "the run timed out",
        )

    def test_a_missing_return_code_is_reported(self):
        self.assertIsNotNone(dashboard.failure_reason(None, "", None))


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_absent_scorecard_is_reported_as_not_run_rather_than_zero(self):
        view = dashboard.load_view(self.root)
        self.assertIsNone(view["scorecard"])
        self.assertIsNone(view["scorecard_error"])
        self.assertIsNone(view["summary"])
        self.assertIsNone(view["record_count"])

    def test_scorecard_and_record_count_are_exposed(self):
        (self.root / "scorecard.json").write_text(
            json.dumps({
                "suite_version": "forestry-eval-0.1", "qualification": "incomplete",
                "gates": "pass", "cases": [], "groups": {}, "missing_evidence": [],
            }), encoding="utf-8")
        (self.root / "records.json").write_text(json.dumps([{"case_id": "x"}]), encoding="utf-8")
        view = dashboard.load_view(self.root)
        self.assertEqual(view["summary"]["qualification"], "incomplete")
        self.assertEqual(view["summary"]["gates"], "pass")
        self.assertEqual(view["record_count"], 1)
        self.assertIsNone(view["scorecard_error"])

    def test_unparseable_scorecard_is_an_error_not_a_silent_zero(self):
        (self.root / "scorecard.json").write_text("{not json", encoding="utf-8")
        view = dashboard.load_view(self.root)
        self.assertIsNone(view["scorecard"])
        self.assertIn("not valid JSON", view["scorecard_error"])

    def test_byte_order_mark_is_reported_rather_than_crashing(self):
        (self.root / "scorecard.json").write_bytes(b"\xef\xbb\xbf{}")
        view = dashboard.load_view(self.root)
        self.assertIsNone(view["scorecard"])
        self.assertIn("BOM", view["scorecard_error"])

    def test_records_that_are_not_a_list_do_not_report_a_count(self):
        (self.root / "records.json").write_text(json.dumps({"nope": True}), encoding="utf-8")
        view = dashboard.load_view(self.root)
        self.assertIsNone(view["record_count"])


class StartRunTests(unittest.TestCase):
    """start_run must never block; this is the regression the page depends on."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # A run needs the frozen configuration for the track it collects. Without it
        # start_run now refuses up front instead of failing inside the runner.
        (self.root / "configuration-engineering.json").write_text(
            json.dumps({field: "test" for field in CONFIG_FIELDS}), encoding="utf-8"
        )
        self.addCleanup(self._reset_state)
        self._stub = _StubExecute()
        self._stub.install()

    def _reset_state(self):
        self._stub.restore()
        with dashboard._RUN_LOCK:
            dashboard._RUN_STATE.update({
                "status": "idle", "tracks": [], "started_at": None,
                "finished_at": None, "returncode": None, "stdout": "",
                "stderr": "", "error": None,
            })

    def test_unknown_track_is_refused_without_starting_anything(self):
        accepted, message = dashboard.start_run(["nope"], None, None, self.root)
        self.assertFalse(accepted)
        self.assertIn("unknown track", message)

    def test_start_run_returns_promptly_and_claims_the_slot(self):
        started = time.monotonic()
        accepted, message = dashboard.start_run(["engineering"], None, None, self.root)
        elapsed = time.monotonic() - started
        self.assertTrue(accepted, message)
        self.assertLess(elapsed, 2.0, "start_run blocked; the run button would hang")
        with dashboard._RUN_LOCK:
            self.assertEqual(dashboard._RUN_STATE["status"], "running")

    def test_second_run_is_refused_promptly_while_one_is_claimed(self):
        self.assertTrue(dashboard.start_run(["engineering"], None, None, self.root)[0])
        started = time.monotonic()
        accepted, message = dashboard.start_run(["engineering"], None, None, self.root)
        elapsed = time.monotonic() - started
        self.assertFalse(accepted)
        self.assertIn("already in progress", message)
        self.assertLess(elapsed, 2.0, "the refusal path must not block either")

    def test_slot_is_released_after_the_run_settles(self):
        self.assertTrue(dashboard.start_run(["engineering"], None, None, self.root)[0])
        self._stub.wait()
        accepted, message = dashboard.start_run(["engineering"], None, None, self.root)
        self.assertTrue(accepted, message)

    def test_a_missing_configuration_is_refused_before_the_run_starts(self):
        """The failure a reader actually hit: the page went to "running", then died.

        An uninitialised directory must be reported as one missing file with a way to
        fix it, not discovered minutes later inside a traceback.
        """
        (self.root / "configuration-engineering.json").unlink()
        accepted, message = dashboard.start_run(["engineering"], None, None, self.root)
        self.assertFalse(accepted)
        self.assertIn("configuration-engineering.json", message)
        with dashboard._RUN_LOCK:
            self.assertEqual(
                dashboard._RUN_STATE["status"], "idle",
                "a refused run must not claim the slot",
            )

    def test_the_agent_track_names_where_a_frozen_configuration_can_be_copied_from(self):
        accepted, message = dashboard.start_run(["agent"], None, None, self.root)
        self.assertFalse(accepted)
        self.assertIn("configuration-agent.json", message)
        self.assertTrue(
            "freeze_agent_config" in message or "Copy the frozen one" in message,
            f"the message must name a way forward: {message}",
        )

    def test_preflight_passes_once_the_configuration_exists(self):
        self.assertIsNone(
            dashboard.preflight(["engineering"], self.root),
            "a configured directory must not be refused",
        )
        self.assertIsNone(dashboard.preflight([], self.root))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "configuration-engineering.json").write_text(
            json.dumps({field: "test" for field in CONFIG_FIELDS}), encoding="utf-8"
        )
        (self.root / "scorecard.json").write_text(
            json.dumps({
                "suite_version": "forestry-eval-0.1", "qualification": "incomplete",
                "gates": "pass", "agent_macro_score": None,
                "worst_agent_group_score": None, "agent_missing_evidence_bounds": [0, 100],
                "cases": [], "groups": {}, "missing_evidence": [],
            }), encoding="utf-8")
        self._stub = _StubExecute()
        self._stub.install()
        self.addCleanup(self._stub.restore)
        self.addCleanup(self._reset_state)
        self.server, self.base = start_server(self.root)
        self.addCleanup(self.server.shutdown)

    def _reset_state(self):
        with dashboard._RUN_LOCK:
            dashboard._RUN_STATE.update({
                "status": "idle", "tracks": [], "started_at": None,
                "finished_at": None, "returncode": None, "stdout": "",
                "stderr": "", "error": None,
            })

    def test_index_renders_the_sections_a_human_reads(self):
        status, body = get(self.base + "/")
        page = body.decode("utf-8")
        self.assertEqual(status, 200)
        for marker in ("<html", "api/view", "api/run", "scorecard"):
            with self.subTest(marker=marker):
                self.assertIn(marker, page)

    def test_view_endpoint_returns_the_scorecard(self):
        status, body = get(self.base + "/api/view")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["summary"]["gates"], "pass")
        self.assertEqual(payload["root"], str(self.root))

    def test_run_endpoint_accepts_a_track_and_refuses_a_duplicate(self):
        status, payload = post(self.base + "/api/run", {"tracks": ["engineering"]})
        self.assertEqual(status, 200)
        self.assertTrue(payload["accepted"])
        status, payload = post(self.base + "/api/run", {"tracks": ["engineering"]})
        self.assertEqual(status, 409)
        self.assertFalse(payload["accepted"])

    def test_run_endpoint_validates_its_input(self):
        status, payload = post(self.base + "/api/run", {"tracks": ["bogus"]})
        self.assertEqual(status, 409)
        self.assertIn("unknown track", payload["message"])
        status, payload = post(self.base + "/api/run", {"tracks": "engineering"})
        self.assertEqual(status, 400)
        self.assertIn("list of strings", payload["message"])

    def test_unknown_endpoints_are_not_found(self):
        self.assertEqual(get(self.base + "/api/nope")[0], 404)
        self.assertEqual(post(self.base + "/nope", {})[0], 404)

    def test_server_only_reads_inside_its_configured_root(self):
        """The viewer reports one directory; it must not follow a path out of it."""
        secret = self.root.parent / "outside-scorecard.json"
        secret.write_text(json.dumps({"qualification": "blocked"}), encoding="utf-8")
        self.addCleanup(secret.unlink, True)
        status, body = get(self.base + "/api/view")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(Path(payload["scorecard_path"]).name, "scorecard.json")
        self.assertTrue(
            Path(payload["scorecard_path"]).is_relative_to(self.root),
            "the viewer must only ever name files inside its root",
        )
        self.assertEqual(payload["summary"]["qualification"], "incomplete")


class _StubExecute:
    """Replace the real subprocess so tests do not spend model calls or minutes."""

    def __init__(self):
        self._original = None
        self._done = threading.Event()

    def install(self):
        self._original = dashboard._execute

        def fake(tracks, cases, repeats, root):
            time.sleep(0.15)
            with dashboard._RUN_LOCK:
                dashboard._RUN_STATE.update({
                    "status": "finished", "finished_at": time.time(),
                    "returncode": 2, "stdout": "stub", "stderr": "", "error": None,
                })
            self._done.set()

        dashboard._execute = fake

    def wait(self, timeout: float = 5.0) -> bool:
        return self._done.wait(timeout)

    def restore(self):
        if self._original is not None:
            dashboard._execute = self._original
            self._original = None


class StartupTests(unittest.TestCase):
    """Starting the viewer twice, or with no browser, must never look like a failure.

    These drive ``run_viewer`` rather than ``main``: the decision under test is
    "reuse or bind", and a test that went through ``main`` had to shut a real server
    down afterwards -- where a second ``shutdown()`` on an already-closed server hangs.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_missing_viewer_is_detected(self):
        self.assertFalse(dashboard.already_serving("127.0.0.1", _free_port()))

    def test_a_running_viewer_is_detected(self):
        server, port = _start_viewer(self.root)
        self.addCleanup(_shutdown, server)
        self.assertTrue(dashboard.already_serving("127.0.0.1", port))

    def test_an_endpoint_that_is_not_the_viewer_is_not_reused(self):
        """A port answering 500 is occupied, not a viewer; binding it must fail."""
        server, port = _serve_health_status(500)
        self.addCleanup(_shutdown, server)
        self.assertFalse(dashboard.already_serving("127.0.0.1", port))
        opened: list[str] = []
        with patch.object(dashboard, "open_viewer", lambda url: opened.append(url)):
            code = dashboard.run_viewer("127.0.0.1", port, self.root, open_browser=True)
        self.assertEqual(code, 1, "the port really is unusable")
        self.assertEqual(opened, [], "a failed start must not open a dead page")

    def test_reusing_a_running_viewer_succeeds_and_opens_it(self):
        """The regression: a second start raised "address already in use".

        What the operator asked for -- a page to look at -- was already true, so it
        must say so, exit 0, and open it. The original never opened anything:
        ``webbrowser`` was not even imported, which is why the command appeared to do
        nothing at all.
        """
        server, port = _start_viewer(self.root)
        self.addCleanup(_shutdown, server)
        opened: list[str] = []
        with patch.object(dashboard, "open_viewer", lambda url: opened.append(url)):
            code = dashboard.run_viewer("127.0.0.1", port, self.root, open_browser=True)
        self.assertEqual(code, 0)
        self.assertEqual(opened, [f"http://127.0.0.1:{port}/"])

    def test_reusing_a_running_viewer_can_skip_the_browser(self):
        server, port = _start_viewer(self.root)
        self.addCleanup(_shutdown, server)
        with patch.object(dashboard, "open_viewer",
                          lambda url: self.fail("open_browser=False must not open one")):
            code = dashboard.run_viewer("127.0.0.1", port, self.root, open_browser=False)
        self.assertEqual(code, 0)

    def test_a_failed_browser_open_is_not_an_error(self):
        """`webbrowser` can raise on a machine with no registered handler."""
        with patch.object(dashboard.webbrowser, "open",
                          side_effect=dashboard.webbrowser.Error("no browser")):
            self.assertFalse(dashboard.open_viewer("http://127.0.0.1:1/"))

    def test_the_browser_opens_by_default(self):
        """`start.cmd` relies on this: it starts the viewer without --open."""
        code = (
            ROOT / "evaluation" / "dashboard.py"
        ).read_text(encoding="utf-8")
        self.assertIn('dest="open_browser", action="store_true", default=True', code)


def _free_port() -> int:
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _shutdown(server) -> None:
    """Stop a helper server without blocking the test process from exiting.

    ``serve_forever`` ran in a plain thread here, and a non-daemon thread keeps the
    interpreter alive after the last test finishes -- so the suite hung with no output
    rather than failing. The thread is a daemon, and this closes the socket too.
    """
    server.shutdown()
    server.server_close()


def _start_viewer(root: Path):
    """A genuine viewer on an ephemeral port, so a real endpoint answers the probe."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    server.daemon_threads = True
    dashboard.Handler.root = root
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def _serve_health_status(status: int):
    """A throwaway server that answers ``/api/health`` with *status*."""
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/api/health":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


class ModuleTests(unittest.TestCase):
    def test_dashboard_does_not_import_runtime(self):
        source = (
            ROOT / "evaluation" / "dashboard.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import runtime", source)
        self.assertNotIn("from runtime", source)

    def test_dashboard_has_no_third_party_imports(self):
        """It must start even where the Runtime image is unavailable."""
        source = (ROOT / "evaluation" / "dashboard.py").read_text(encoding="utf-8")
        for forbidden in ("fastapi", "uvicorn", "rasterio", "numpy", "httpx"):
            with self.subTest(module=forbidden):
                self.assertNotIn(f"import {forbidden}", source)

    def test_track_parser_rejects_unknown_names(self):
        self.assertEqual(dashboard.parse_tracks("engineering,ui"), ["engineering", "ui"])
        self.assertEqual(dashboard.parse_tracks(None), [])
        with self.assertRaises(Exception):
            dashboard.parse_tracks("engineering,bogus")


if __name__ == "__main__":
    unittest.main()
