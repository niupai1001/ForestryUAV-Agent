"""Phase 1 acceptance: Runtime-owned job waiting and incremental observation.

The behaviours that matter here:

* waiting happens inside the Runtime, so no model request is spent on polling;
* a wait that times out reports the job as still running, never as finished;
* repeated `job_status` calls return only new output, because the Runtime keeps
  the cursor, while an explicit offset can still re-read history;
* a cancellation or pause stops the wait without touching the job.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from tests.test_generic_runtime import FakeBridge
from runtime.lifecycle import Sessions
from runtime.workspace import WorkspaceRegistry
from runtime.capabilities.runtime import RuntimeTools


class ScriptedBridge(FakeBridge):
    """A bridge whose jobs emit scripted log chunks and settle on demand."""

    def __init__(self):
        super().__init__()
        self.logs: dict[str, str] = {}
        self.settle_after: dict[str, int] = {}
        self.polls: dict[str, int] = {}

    def job(self, operation, payload):
        job_id = payload["job_id"]
        if operation == "start":
            self.started.append(payload)
            self.states[job_id] = "running"
            self.logs.setdefault(job_id, "")
            self.polls.setdefault(job_id, 0)
            return {"job_id": job_id, "state": "running", "terminal": False, "offset": 0}
        if operation == "status":
            self.polls[job_id] = self.polls.get(job_id, 0) + 1
            limit = self.settle_after.get(job_id)
            if limit is not None and self.polls[job_id] >= limit:
                self.states[job_id] = "succeeded"
            logs = self.logs.get(job_id, "")
            offset = max(0, int(payload.get("offset", 0)))
            chunk = logs[offset:offset + 65536]
            state = self.states[job_id]
            return {
                "job_id": job_id, "state": state, "terminal": state != "running",
                "offset": offset + len(chunk), "output": chunk,
                "exit_code": 0 if state == "succeeded" else None,
                "has_more_output": offset + len(chunk) < len(logs),
            }
        if operation == "cancel":
            self.states[job_id] = "canceled"
            return {"job_id": job_id, "state": "canceled", "terminal": True,
                    "offset": 0, "output": "", "canceled": True}
        return {"job_id": job_id, "removed": True}


class JobWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = Sessions(self.root)
        self.chat_id = str(uuid.uuid4())
        self.sessions.create("alice", self.chat_id)
        self.store = self.sessions.acquire("alice", self.chat_id)
        self.sessions.release(self.chat_id)
        self.registry = WorkspaceRegistry(self.root)
        self.bridge = ScriptedBridge()
        self.registry.bridge = self.bridge
        self.box = RuntimeTools(self.store, "alice", [], self.registry, "")

    def tearDown(self):
        self.temp.cleanup()

    def _start(self, code: str = "print('work')") -> str:
        with patch.dict(os.environ, {
            "RUNTIME_DATA_HOST_ROOT": str(self.root),
            "CODE_RUN_PREFLIGHT": "false",
        }):
            started = self.box.execute(
                "code_run", {"language": "python", "code": code}
            )
        self.assertTrue(started["ok"], started)
        return started["data"]["job_id"]

    def test_wait_returns_as_soon_as_the_job_settles(self):
        job_id = self._start()
        self.bridge.settle_after[job_id] = 2
        self.bridge.logs[job_id] = "step 1\nstep 2\ndone\n"
        started = time.monotonic()
        result = self.box.execute(
            "job_wait", {"job_id": job_id, "timeout_seconds": 10}
        )
        elapsed = time.monotonic() - started
        data = result["data"]
        self.assertTrue(data["terminal"])
        self.assertEqual(data["state"], "succeeded")
        self.assertEqual(data["wait_outcome"], "terminal")
        self.assertIn("done", data["output"])
        # The wait ended because the job settled, not because the timeout expired.
        self.assertLess(elapsed, 10)

    def test_timeout_reports_the_job_as_still_running(self):
        """A timeout must never be reported as a finished result."""
        job_id = self._start()
        with patch.dict(os.environ, {"JOB_WAIT_POLL_SECONDS": "0.25"}):
            result = self.box.execute(
                "job_wait", {"job_id": job_id, "timeout_seconds": 1}
            )
        data = result["data"]
        self.assertFalse(data["terminal"])
        self.assertTrue(data["wait_timed_out"])
        self.assertEqual(data["wait_outcome"], "timeout")
        self.assertEqual(data["state"], "running")
        self.assertIn("still running", data["guidance"])

    def test_wait_stops_when_the_run_is_paused_or_cancelled(self):
        job_id = self._start()
        self.box.cancelled = lambda: True
        result = self.box.execute("job_wait", {"job_id": job_id, "timeout_seconds": 30})
        data = result["data"]
        self.assertEqual(data["wait_outcome"], "interrupted")
        self.assertFalse(data["terminal"])
        # The job itself was left alone.
        self.assertEqual(self.bridge.states[job_id], "running")

    def test_status_uses_a_runtime_cursor_and_explicit_offset_replays(self):
        """Repeated polls must not re-return the same old log."""
        job_id = self._start()
        self.bridge.logs[job_id] = "first\n"
        first = self.box.execute("job_status", {"job_id": job_id})["data"]
        self.assertEqual(first["output"], "first\n")
        self.assertEqual(first["cursor"]["next_offset"], len("first\n"))

        self.bridge.logs[job_id] = "first\nsecond\n"
        second = self.box.execute("job_status", {"job_id": job_id})["data"]
        self.assertEqual(second["output"], "second\n")
        self.assertFalse(second["cursor"]["explicit"])

        # An explicit offset re-reads from the start without moving the cursor.
        replay = self.box.execute(
            "job_status", {"job_id": job_id, "offset": 0}
        )["data"]
        self.assertEqual(replay["output"], "first\nsecond\n")
        self.assertTrue(replay["cursor"]["explicit"])
        self.bridge.logs[job_id] = "first\nsecond\nthird\n"
        third = self.box.execute("job_status", {"job_id": job_id})["data"]
        self.assertEqual(third["output"], "third\n")

    def test_cursor_survives_a_runtime_restart(self):
        """A new Runtime object must not replay output it already delivered."""
        job_id = self._start()
        self.bridge.logs[job_id] = "alpha\n"
        self.box.execute("job_status", {"job_id": job_id})
        reopened = RuntimeTools(self.store, "alice", [], self.registry, "")
        reopened.workspaces.bridge = self.bridge
        self.bridge.logs[job_id] = "alpha\nbeta\n"
        result = reopened.execute("job_status", {"job_id": job_id})["data"]
        self.assertEqual(result["output"], "beta\n")

    def test_job_log_saves_the_complete_log_as_a_workspace_artifact(self):
        job_id = self._start()
        self.bridge.logs[job_id] = "head\n" + ("middle\n" * 5000) + "FATAL: last line\n"
        result = self.box.execute(
            "job_log", {"job_id": job_id, "max_chars": 4000}
        )["data"]
        artifact = self.registry.workspace(self.store) / result["log_artifact"]
        self.assertTrue(artifact.is_file())
        self.assertEqual(artifact.read_text(encoding="utf-8"), self.bridge.logs[job_id])
        self.assertEqual(result["total_chars"], len(self.bridge.logs[job_id]))
        self.assertTrue(result["truncated"])
        # Both ends survive: the head for context and the tail for the terminal error.
        self.assertIn("head", result["content"])
        self.assertIn("FATAL: last line", result["content"])

    def test_wait_delivers_progress_to_the_ui_without_model_requests(self):
        job_id = self._start()
        published: list[dict] = []
        self.box.publish_job_status = published.append
        self.bridge.settle_after[job_id] = 2
        self.box.execute("job_wait", {"job_id": job_id, "timeout_seconds": 5})
        states = [item["state"] for item in published]
        self.assertIn("running", states)
        self.assertIn("succeeded", states)
        self.assertTrue(all(item["job_id"] == job_id for item in published))


if __name__ == "__main__":
    unittest.main()
