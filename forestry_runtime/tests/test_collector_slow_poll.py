"""A slow event poll is not a failed experiment.

The collector polls `/runs/{id}/events`, which long-polls server-side for up to 20
seconds. Under load a single read can exceed the client's per-read timeout while the
Run is perfectly healthy. Two `capability.supervised` slots were recorded as
`infra_error` with 32 and 24 steps of real work already on disk -- the evidence was
there, the collector had simply given up on reading it.

The poll resumes from the last event offset, so retrying costs only the wait.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.collect.api import RuntimeApiClient  # noqa: E402


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


class SlowPollTests(unittest.TestCase):
    def _client(self):
        client = RuntimeApiClient("http://127.0.0.1:1", "k" * 40, "evaluator", "chat-x")
        client.wait_until_healthy = lambda: False  # type: ignore[method-assign]
        return client

    def test_a_timeout_is_retried_and_the_answer_is_kept(self):
        client = self._client()
        calls = {"n": 0}

        def flaky(request, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("timed out")
            return _Response(b'{"events": [], "next": 7}')

        with patch("evaluation.collect.api.urlopen", side_effect=flaky), \
             patch("evaluation.collect.api.time.sleep"):
            result = client.request("GET", "/runs/x/events")
        self.assertEqual(result, {"events": [], "next": 7})
        self.assertEqual(calls["n"], 2)
        self.assertEqual(client.transient_retries, 1)

    def test_a_persistent_timeout_still_fails(self):
        client = self._client()
        with patch("evaluation.collect.api.urlopen", side_effect=TimeoutError("timed out")), \
             patch("evaluation.collect.api.time.sleep"):
            with self.assertRaises(RuntimeError) as raised:
                client.request("GET", "/runs/x/events")
        self.assertIn("Runtime unavailable", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
