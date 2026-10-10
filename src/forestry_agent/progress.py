from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic_ai.messages import ToolReturn
from pydantic_ai_harness.tool_output_limits import indented_json

from .contracts import Failure
from .resources import native_path
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
    if tool in {"acquire_dependencies", "resolve_dependencies", "environment.acquire"} and "wheels" in value:
        # Re-downloading the same public bytes into fresh artifact IDs is not
        # new task evidence. Keep release/hash/source facts, not receipt handles.
        value.pop("wheel_artifact_ids", None)
        value.pop("source_artifact_ids", None)
        value.pop('built_wheel_artifact_ids', None)
        value.pop("lock_artifact_id", None)
        value.pop("resolution_lock_artifact_id", None)
        value.pop("target_artifact_id", None)
        value.pop("metadata_requests", None)
        value.pop("reused_from_action_id", None)
        value.pop("recorded_metadata_requests", None)
        for group in ['wheels', 'source_packages', 'built_wheels']:
            if group in value:
                value[group] = [{k: v for k, v in package.items() if k not in
                    {"artifact_id", "metadata_artifact_id", "hops", "observed_at"}} for package in value[group]]
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

    def recovery(self, db, deps, tool, fingerprint):
        """Restore one lost read per native context loss, without claiming new facts.

        Prove loss from hash-bound prepared tool returns and their existing
        compaction lineage. Bookkeeping, side effects and unverified snapshots
        cannot earn this exception; repeated restoration of the same loss cannot.
        """
        if tool not in {'read_file', 'read_artifact', 'read_tool_result_window', 'fs.read', 'artifact.read'} or not deps.context_manifest_id:
            return None
        root = ordinary_path(native_path(self.config.workspace_root / '.forestry' / 'model-inputs').resolve())

        def prepared(context_id):
            row = db.execute("SELECT payload FROM contexts WHERE id=? AND task_id=? AND turn_id=? "
                "AND json_extract(payload,'$.spec_revision')=? AND json_extract(payload,'$.assignment_id') IS ?",
                (context_id, deps.task_id, deps.turn_id, deps.revision, deps.assignment_id)).fetchone()
            if not row:
                return None
            manifest = json.loads(row['payload'])
            if (manifest.get('context_manifest_id') != context_id or manifest.get('task_id') != deps.task_id or
                manifest.get('turn_id') != deps.turn_id):
                return None
            path = native_path(Path(manifest['prepared_input_ref'])).resolve(strict=True)
            if not ordinary_path(path).is_relative_to(root) or path.stat().st_size > 2 * 1024 * 1024:
                return None
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest['prepared_input_sha256']:
                return None
            messages = json.loads(data)['messages']
            refs = [hashlib.sha256(encode(message).encode()).hexdigest() for message in messages]
            if refs != manifest['ordered_message_refs']:
                return None
            matching = {ref for message, ref in zip(messages, refs, strict=True) if any(
                part.get('part_kind') == 'tool-return' and part.get('tool_name') == tool and
                observation_fingerprint(tool, observation(tool, part.get('content'))) == fingerprint
                for part in message.get('parts', []))}
            return manifest, matching

        try:
            current = prepared(deps.context_manifest_id)
            if not current or current[1]:
                return None
            # Follow the existing same-turn, same-role lineage, bounded locally.
            for _ in range(64):
                child = current[0]
                current = prepared(child.get('previous_context_ref'))
                if not current:
                    return None
                if current[1]:
                    if not current[1].intersection(child['prior_message_refs_absent']):
                        return None
                    lost_at = child['context_manifest_id']
                    prior = db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='progress.observed' "
                        "AND json_extract(payload,'$.turn_id')=? AND json_extract(payload,'$.assignment_id') IS ? "
                        "AND json_extract(payload,'$.context_loss_id')=? AND json_extract(payload,'$.observation_sha256')=? LIMIT 1",
                        (deps.task_id, deps.turn_id, deps.assignment_id, lost_at, fingerprint)).fetchone()
                    return None if prior else {'context_loss_id': lost_at, 'restored_from_context': current[0]['context_manifest_id']}
        except (OSError, ValueError, KeyError, TypeError):
            return None  # Missing/corrupt lineage never weakens the normal guard.
        return None

    def record(self, deps, call_id, tool, args, result):
        value = observation(tool, result)
        fingerprint = observation_fingerprint(tool, value)
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
                recovery = self.recovery(db, deps, tool, fingerprint) if seen else None
                stalled = 0 if progressed else (latest["stalled"] if latest else 0) + (0 if recovery else 1)
                db.execute("INSERT INTO progress_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (deps.task_id, deps.revision, scope, deps.turn_id, call_id, request_id, tool, decision, fingerprint, stalled, limit, now()))
                self.store.event(db, deps.task_id, "progress.observed", {"turn_id": deps.turn_id,
                    "assignment_id": deps.assignment_id, "context_manifest_id": request_id or None,
                    "tool": tool, "new_observation": progressed,
                    "stalled": stalled, "limit": limit, "observation_sha256": fingerprint,
                    **({'context_recovered': True, **recovery} if recovery else {})})
        # Commit the observation before stopping, including successful effects.
        self.stopped(stalled, limit)


def observation_fingerprint(tool, value):
    return hashlib.sha256(encode(json.loads(indented_json({'tool': tool, 'observation': value}))).encode()).hexdigest() if value is not None else None


def ordinary_path(path):
    """Compare already resolved Windows paths independently of long-path spelling."""
    value = str(path)
    return Path('\\\\' + value[8:] if value.startswith('\\\\?\\UNC\\') else value.removeprefix('\\\\?\\'))
