"""Durable-job observation: incremental cursors, runtime-backed waiting, log artifacts.

Three different facts must not be confused with one another:

* a tool call returned -- that only means the Runtime answered;
* a background job finished -- that is what ``state`` and ``terminal`` report;
* the user's goal is complete -- only the Agent can claim that, and the
  coordinator still reconciles live jobs before a Run may end as completed.

Waiting belongs to the Runtime, not to repeated model calls: ``job_wait``
suspends inside the tool call, and a Run that ends while a job is live is put
into ``waiting`` and resumed by the monitor exactly once per terminal state.
"""
from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
import re
import time

from ...storage import AssetError
from ...store.executions import snapshot_workspace
from shared.outcome import missing_shared_library


class JobWaitInterrupted(Exception):
    """Raised internally when the Run is paused, cancelled, or shutting down."""


class JobStatusCapability:
    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _terminal_state(state: str | None) -> bool:
        return str(state or "").casefold() in {
            "succeeded", "failed", "canceled", "cancelled", "timed_out",
            "backend_removed", "submission_uncertain",
        }

    def _cursor_path(self) -> Path:
        return self.workspace / ".runtime" / "job-cursors.json"

    def _cursors(self) -> dict:
        path = self._cursor_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _cursor(self, job_id: str) -> int:
        entry = self._cursors().get(job_id) or {}
        try:
            return max(0, int(entry.get("offset", 0)))
        except (TypeError, ValueError):
            return 0

    def _set_cursor(self, job_id: str, offset: int, *, state: str | None = None) -> None:
        path = self._cursor_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        cursors = self._cursors()
        entry = dict(cursors.get(job_id) or {})
        entry["offset"] = max(0, int(offset))
        entry["updated_at"] = time.time()
        if state:
            entry["state"] = state
        cursors[job_id] = entry
        # Keep the file bounded: the newest 200 job cursors are enough for any Run.
        if len(cursors) > 200:
            ordered = sorted(
                cursors.items(),
                key=lambda item: float((item[1] or {}).get("updated_at") or 0),
                reverse=True,
            )[:200]
            cursors = dict(ordered)
        path.write_text(json.dumps(cursors, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record_kind(self, job_id: str) -> str | None:
        try:
            return self.records.get(job_id)["kind"]
        except AssetError:
            return None

    def _domain_job_data(self, name: str, arguments: dict) -> dict:
        job_id = str(arguments["job_id"])
        if re.fullmatch(r"job_[0-9a-f]{32}", job_id):
            raise AssetError("Code job is not registered in this workspace")
        return {
            "job_id": job_id, "job_type": "legacy-external",
            "state": "backend_removed", "terminal": True,
            "needs_finalization": False,
            "failure": {
                "stage": "backend", "code": "local_photogrammetry_backend_removed",
                "operation_started": False, "side_effects": "none",
                "message": (
                    "The local photogrammetry backend is not part of this Runtime. "
                    "Existing outputs may be imported; future execution can be provided by MCP."
                ),
            },
        }

    def _register_job_artifacts(self, job_id: str) -> list[dict]:
        record = self.records.get(job_id)
        existing = json.loads(record["artifacts_json"] or "[]")
        if existing:
            return existing
        before = {key: tuple(value) for key, value in json.loads(record["before_json"]).items()}
        after = snapshot_workspace(self.workspace)
        changed = [path for path, value in after.items() if before.get(path) != value]
        artifacts = []
        for relative in changed[:500]:
            target = self.workspace / relative
            media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            asset = self.store.register_path(target, target.name, self.owner, media_type, artifact_kind="job-output", metadata={"job_id": job_id, "workspace_path": relative})
            artifacts.append(asset)
            if asset["id"] not in {item["id"] for item in self.created}:
                self.created.append(asset)
                self.allowed.add(asset["id"])
        self.records.update(job_id, record["state"], artifacts, after)
        return artifacts

    def _decorate(self, job_id: str, result: dict) -> dict:
        kind = self._record_kind(job_id)
        result["job_type"] = kind if kind in {"install", "code"} else result.get("job_type") or "code"
        if result.get("terminal"):
            result.setdefault("exit_code", None)
        if result.get("state") == "failed":
            library = missing_shared_library(str(result.get("output") or ""))
            result["job_failure"] = {
                "reason": "missing_env" if library else "unknown",
                "code": "missing_shared_library" if library else "job_failed",
                "exit_code": result.get("exit_code"),
                **({"missing": [{"kind": "shared_library", "name": library}]}
                   if library else {}),
            }
        return result

    def _publish_status(self, result: dict) -> None:
        publish = getattr(self, "publish_job_status", None)
        if publish is None:
            return
        publish({
            "job_id": result.get("job_id"),
            "job_type": result.get("job_type"),
            "state": result.get("state"),
            "terminal": bool(result.get("terminal")),
            "exit_code": result.get("exit_code"),
            "offset": result.get("offset"),
            "progress_percent": result.get("progress_percent"),
            "needs_finalization": result.get("needs_finalization"),
        })

    # ------------------------------------------------------------------- tools

    def job_status(self, job_id, offset=None, wait_seconds=0):
        """Poll once. Without an explicit offset, continue from the saved cursor."""
        explicit = offset is not None
        try:
            self.records.get(job_id)
        except AssetError:
            return self._domain_job_data("get_job_status", {"job_id": job_id})
        cursor = int(offset) if explicit else self._cursor(job_id)
        deadline = time.monotonic() + max(0, int(wait_seconds))
        while True:
            result = self.workspaces.bridge.job("status", {"job_id": job_id, "offset": cursor})
            if result["terminal"] or result.get("output") or time.monotonic() >= deadline:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        self.records.update(job_id, result["state"])
        result = self._decorate(job_id, result)
        result["cursor"] = {
            "requested_offset": cursor,
            "next_offset": int(result.get("offset", cursor)),
            "explicit": explicit,
            "replay_available": True,
        }
        if not explicit:
            self._set_cursor(job_id, int(result.get("offset", cursor)), state=result.get("state"))
        if result["terminal"]:
            result["artifacts"] = self._register_job_artifacts(job_id)
            if result.get("job_type") == "install":
                manifest = self.workspace / ".runtime" / "deps" / "installed-packages.json"
                if manifest.is_file():
                    try:
                        result["installed_packages"] = json.loads(
                            manifest.read_text(encoding="utf-8")
                        )[:500]
                    except ValueError:
                        result["installed_packages"] = None
        self._publish_status(result)
        return result

    def job_wait(self, job_id, timeout_seconds=300, offset=None):
        """Wait inside this tool call until the job settles or the budget expires.

        The Runtime does the waiting.  No additional model request is issued while
        nothing changes; the Run's pause, cancel, and shutdown flags are honoured
        at every check, and a timeout returns a non-terminal observation instead
        of pretending the job finished.
        """
        try:
            self.records.get(job_id)
        except AssetError:
            return self._domain_job_data("wait_job", {"job_id": job_id})
        if offset is None:
            cursor = self._cursor(job_id)
        else:
            cursor = max(0, int(offset))
        timeout = max(0, min(3600, int(timeout_seconds)))
        deadline = time.monotonic() + timeout
        interval = max(0.25, float(os.getenv("JOB_WAIT_POLL_SECONDS", "1.5")))
        chunks: list[str] = []
        result: dict = {}
        interrupted = False
        while True:
            if self._run_interrupted():
                interrupted = True
                break
            result = self.workspaces.bridge.job("status", {"job_id": job_id, "offset": cursor})
            chunk = result.get("output") or ""
            if chunk:
                chunks.append(chunk)
                cursor = int(result.get("offset", cursor))
                self._set_cursor(job_id, cursor, state=result.get("state"))
            self.records.update(job_id, result["state"])
            self._publish_status(self._decorate(job_id, result))
            if result["terminal"]:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result = dict(result, wait_timed_out=True)
                break
            if self._sleep_interruptible(min(interval, remaining)):
                interrupted = True
                break
        combined = "".join(chunks)
        if not combined:
            combined = result.get("output") or ""
        # `job_status` builds a fresh status dict, so the outcome observed during
        # the wait has to be carried across explicitly rather than inferred later.
        timed_out = bool(result.get("wait_timed_out"))
        # Re-read through `job_status` for cursor, artifacts and manifest handling.
        latest = self.job_status(job_id, offset=cursor)
        latest["output"] = combined
        latest = self._decorate(job_id, latest)
        latest["offset"] = max(cursor, int(latest.get("offset", cursor)))
        latest["waited_seconds"] = round(timeout - max(0.0, deadline - time.monotonic()), 3)
        latest["wait_timeout_seconds"] = timeout
        latest["log_chars"] = len(combined)
        latest["wait_timed_out"] = bool(timed_out and not latest.get("terminal"))
        if interrupted:
            latest["wait_outcome"] = "interrupted"
            latest["wait_timed_out"] = not latest.get("terminal")
            latest["note"] = (
                "The Run was paused or cancelled while waiting; the job was not "
                "affected and its durable reference is preserved."
            )
        elif latest.get("terminal"):
            latest["wait_outcome"] = "terminal"
        elif latest["wait_timed_out"]:
            latest["wait_outcome"] = "timeout"
            latest["guidance"] = (
                "The job is still running. Call job_wait again to keep waiting, or end "
                "your turn — the Runtime keeps monitoring and will resume you once the "
                "job reaches a terminal state. Do not report this job's result as finished."
            )
        else:
            latest["wait_outcome"] = "observed"
        return latest

    def job_log(self, job_id, offset=0, max_chars=200000):
        """Read the complete job log, saving the full text as a workspace artifact."""
        try:
            self.records.get(job_id)
        except AssetError:
            return self._domain_job_data("read_job_log", {"job_id": job_id})
        collected: list[str] = []
        cursor = max(0, int(offset))
        pages = 0
        while pages < 400:
            result = self.workspaces.bridge.job("status", {"job_id": job_id, "offset": cursor})
            chunk = result.get("output") or ""
            if not chunk:
                break
            collected.append(chunk)
            cursor = int(result.get("offset", cursor))
            if not result.get("has_more_output"):
                break
            pages += 1
        text = "".join(collected)
        directory = self.workspace / ".runtime" / "job-logs"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{job_id}.log"
        target.write_text(text, encoding="utf-8")
        relative = target.relative_to(self.workspace).as_posix()
        self._set_cursor(job_id, cursor)
        limit = max(1000, min(1_000_000, int(max_chars)))
        returned = text if len(text) <= limit else text[:limit // 2] + (
            f"\n\n… [{len(text) - limit} characters omitted; read the artifact ] …\n\n"
        ) + text[-(limit // 2):]
        return {
            "job_id": job_id,
            "state": result.get("state"),
            "terminal": bool(result.get("terminal")),
            "log_artifact": relative,
            "total_chars": len(text),
            "returned_chars": len(returned),
            "truncated": len(text) > limit,
            "content": returned,
        }

    # ------------------------------------------------------------------ waiting

    def _run_interrupted(self) -> bool:
        for name in ("cancelled", "pause_requested"):
            check = getattr(self, name, None)
            if callable(check):
                try:
                    if check():
                        return True
                except Exception:
                    continue
        return False

    @staticmethod
    def _sleep_interruptible(seconds: float) -> bool:
        """Sleep in slices so a long wait never blocks for the whole interval."""
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            step = min(0.5, remaining)
            time.sleep(step)
            remaining -= step
        return False
