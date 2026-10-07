from __future__ import annotations

import hashlib
import json

from pydantic_ai.messages import ToolReturn
from pydantic_ai_harness.tool_output_limits import indented_json

from .contracts import Failure
from .store import encode, now


def observation(tool, result):
    """Normalize known tool envelopes, leaving source text and user fields intact.

    Novel observations are a bounded engineering signal, not semantic acceptance.
    New attempt IDs, timestamps and recursive inspections are not new evidence.
    """
    if tool in {"write_plan", "read_plan", "task.plan"}:
        return None  # Model-authored bookkeeping is not newly observed evidence.
    if isinstance(result, ToolReturn):
        value = observation(tool, result.return_value)
        return {"return_value": value, "content": result.content} if result.content is not None else value
    if tool in {"delegate_task", "role.delegate"} and isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            pass
    if not isinstance(result, dict):
        return result if result else None
    value = dict(result)
    if value.get("kind") in {"failed", "window_required"} or value.get("status") == "failed":
        return None
    if tool in {"read_tool_result_window", "read_artifact", "read_file", "artifact.read", "fs.read"} and value.get("text") == "":
        return None
    # Ignore only application-owned envelope keys, never arbitrary keys in text.
    for key in ("action_id", "attempt_id", "producer_action_id", "created_at", "observed_at",
                "updated_at", "preview_url", "download_url", "locator", "continuation"):
        value.pop(key, None)
    if tool in {"write_file", "inspect_artifact", "fs.write_workspace", "artifact.inspect"}:
        value.pop("artifact_id", None)
    if tool in {"read_tool_result_window", "read_tool_result"}:
        value.pop("handle", None)
    citation = value.get("citation")
    if isinstance(citation, dict):
        value["citation"] = {k: v for k, v in citation.items()
                             if k not in {"observed_at", "snapshot_artifact_id"}}
        value.pop("text_artifact_id", None)
    if tool in {"inspect_action", "action.inspect"} and "result" in value:
        # Flatten actual inspection envelopes, bounded against adversarial nesting.
        target = value
        for _ in range(32):
            if target.get("tool") != "action.inspect" or not isinstance(target.get("result"), dict):
                break
            target = target["result"]
        else:
            return None
        source_tool = target.get("tool", "unknown")
        source = observation(source_tool, target.get("result"))
        return {"tool": source_tool, "observation": source} if source is not None else None
    if isinstance(value.get("job"), dict):
        value["job"] = {k: v for k, v in value["job"].items()
                        if k not in {"updated_at", "created_at"}}
    return value


class Progress:
    """Application resource protection; Harness still owns execution and history."""

    def __init__(self, store, config):
        self.store, self.config = store, config

    @staticmethod
    def scope(deps):
        return deps.assignment_id or "root"

    def latest(self, db, deps):
        return db.execute("SELECT * FROM progress_observations WHERE task_id=? AND revision=? AND scope=? AND turn_id=? ORDER BY rowid DESC LIMIT 1",
            (deps.task_id, deps.revision, self.scope(deps), deps.turn_id)).fetchone()

    @staticmethod
    def stopped(stalled, limit):
        if stalled >= limit:
            raise Failure("NO_PROGRESS", f"连续{stalled}次工具观察没有新增证据（上限{limit}），已停止本回合。请检查执行记录并调整任务；直接恢复不会重置计数。", 422)

    def check(self, db, deps):
        row = self.latest(db, deps)
        if row:
            self.stopped(row["stalled"], row["limit_value"])

    def record(self, deps, call_id, tool, args, result):
        value = observation(tool, result)
        fingerprint = hashlib.sha256(encode(json.loads(indented_json({"tool": tool, "observation": value}))).encode()).hexdigest() if value is not None else None
        decision = hashlib.sha256(encode({"tool": tool, "args": args}).encode()).hexdigest()
        scope = self.scope(deps)
        request_id = deps.context_manifest_id or ""
        with self.store.transaction() as db:
            state = self.store.read_task(db, deps.task_id)
            if state["spec_revision"] != deps.revision or state["pause_requested"] or state["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "控制或修订状态阻止继续观察", 409)
            # A repeated call ID in a new model response is another decision, while
            # restoring a pending native call without a new request is a replay.
            prior = db.execute("SELECT * FROM progress_observations WHERE task_id=? AND revision=? AND scope=? AND turn_id=? AND call_id=? AND (?='' OR request_id=?) ORDER BY rowid DESC LIMIT 1",
                (deps.task_id, deps.revision, scope, deps.turn_id, call_id, request_id, request_id)).fetchone()
            if prior:
                if prior["decision_sha256"] != decision:
                    raise Failure("VERSION_CONFLICT", "重放的工具观察载荷变化", 409)
                stalled, limit = prior["stalled"], prior["limit_value"]
            else:
                latest = self.latest(db, deps)
                limit = latest["limit_value"] if latest else state["config_snapshot"].get("generation", {}).get("max_unchanged_decisions", self.config.max_unchanged_decisions)
                seen = fingerprint is not None and db.execute("SELECT 1 FROM progress_observations WHERE task_id=? AND revision=? AND scope=? AND turn_id=? AND fingerprint=? LIMIT 1",
                    (deps.task_id, deps.revision, scope, deps.turn_id, fingerprint)).fetchone()
                progressed = fingerprint is not None and not seen
                stalled = 0 if progressed else (latest["stalled"] if latest else 0) + 1
                db.execute("INSERT INTO progress_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (deps.task_id, deps.revision, scope, deps.turn_id, call_id, request_id, tool, decision, fingerprint, stalled, limit, now()))
                self.store.event(db, deps.task_id, "progress.observed", {"turn_id": deps.turn_id,
                    "assignment_id": deps.assignment_id, "context_manifest_id": request_id or None,
                    "tool": tool, "new_observation": progressed,
                    "stalled": stalled, "limit": limit, "observation_sha256": fingerprint})
        # Commit the observation before stopping, including successful effects.
        self.stopped(stalled, limit)
