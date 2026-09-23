"""Tests for the health and readiness endpoints.

The regression this prevents: ``/health`` used to report ``status: ok``
unconditionally, so a deployment whose model server was completely unreachable
still looked ready. Both the honest report and the separation between liveness
(always 200, polled by the container healthcheck) and readiness (503 when a
dependency is down) are pinned here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"models": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HealthEndpointTests(unittest.TestCase):
    """Each test builds the app with the environment it wants to describe."""

    def setUp(self):
        self._saved = {
            name: os.environ.get(name)
            for name in ("OLLAMA_URL", "HOST_BRIDGE_URL", "HOST_BRIDGE_KEY",
                         "RUNTIME_API_KEY")
        }
        self.addCleanup(self._restore)
        os.environ["RUNTIME_API_KEY"] = "k" * 40
        self._servers: list[ThreadingHTTPServer] = []

    def _restore(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _serve_ok(self) -> int:
        port = free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), _Ok)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        self.addCleanup(server.shutdown)
        return port

    def _client(self) -> TestClient:
        # Imported here so each test reads the environment set in setUp.
        from runtime.api.app import app

        return TestClient(app)

    def test_unreachable_dependencies_report_degraded_but_stay_http_200(self):
        os.environ["OLLAMA_URL"] = f"http://127.0.0.1:{free_port()}"
        os.environ["HOST_BRIDGE_URL"] = f"http://127.0.0.1:{free_port()}"
        os.environ["HOST_BRIDGE_KEY"] = "b" * 40
        with self._client() as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200, "the container healthcheck polls this")
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertFalse(body["model_reachable"])
        self.assertFalse(body["host_bridge_reachable"])
        self.assertTrue(body["model_error"], "an unreachable model must explain itself")
        self.assertTrue(body["host_bridge_error"])

    def test_reachable_dependencies_report_ok(self):
        port = self._serve_ok()
        os.environ["OLLAMA_URL"] = f"http://127.0.0.1:{port}"
        os.environ["HOST_BRIDGE_URL"] = f"http://127.0.0.1:{port}"
        os.environ["HOST_BRIDGE_KEY"] = "b" * 40
        with self._client() as client:
            body = client.get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["model_reachable"])
        self.assertTrue(body["host_bridge_reachable"])
        self.assertIsNone(body["model_error"])

    def test_ready_is_503_when_the_model_is_unreachable(self):
        os.environ["OLLAMA_URL"] = f"http://127.0.0.1:{free_port()}"
        os.environ["HOST_BRIDGE_URL"] = f"http://127.0.0.1:{free_port()}"
        os.environ["HOST_BRIDGE_KEY"] = "b" * 40
        with self._client() as client:
            response = client.get("/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")

    def test_ready_is_200_when_the_model_answers(self):
        port = self._serve_ok()
        os.environ["OLLAMA_URL"] = f"http://127.0.0.1:{port}"
        os.environ["HOST_BRIDGE_URL"] = f"http://127.0.0.1:{port}"
        os.environ["HOST_BRIDGE_KEY"] = "b" * 40
        with self._client() as client:
            response = client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_unconfigured_bridge_is_reported_rather_than_probed(self):
        port = self._serve_ok()
        os.environ["OLLAMA_URL"] = f"http://127.0.0.1:{port}"
        os.environ.pop("HOST_BRIDGE_URL", None)
        os.environ["HOST_BRIDGE_KEY"] = ""
        with self._client() as client:
            body = client.get("/health").json()
        self.assertFalse(body["host_bridge_reachable"])
        self.assertIn("not configured", body["host_bridge_error"])

    def test_health_still_reports_the_deployment_facts(self):
        os.environ["OLLAMA_URL"] = f"http://127.0.0.1:{free_port()}"
        os.environ["HOST_BRIDGE_URL"] = f"http://127.0.0.1:{free_port()}"
        os.environ["HOST_BRIDGE_KEY"] = "b" * 40
        with self._client() as client:
            body = client.get("/health").json()
        self.assertEqual(body["version"], "0.10.0")
        self.assertEqual(body["kernel"], "pydantic-ai")
        self.assertIn("model", body)
        self.assertIn("remote_sensing_plugins", body)


class ProbeHelperTests(unittest.TestCase):
    def test_probe_treats_an_http_error_as_reachable(self):
        """A 401 from the bridge means it answered; that is not unavailability."""
        class _Unauthorized(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        port = free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), _Unauthorized)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)

        from runtime.api.routes.health import _probe

        reachable, error = _probe(f"http://127.0.0.1:{port}/health", timeout=3)
        self.assertTrue(reachable)
        self.assertIn("401", error or "")

    def test_probe_is_bounded_when_nothing_is_listening(self):
        from runtime.api.routes.health import _probe

        reachable, error = _probe(f"http://127.0.0.1:{free_port()}/health", timeout=1)
        self.assertFalse(reachable)
        self.assertTrue(error)


if __name__ == "__main__":
    unittest.main()
