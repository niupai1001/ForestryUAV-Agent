"""应用侧的资源保护：无进展计数、上下文恢复与结构化判定。

判定只读 ``runtime.evidence`` 给出的事实维度（输入身份/版本、读取范围、实际结果、
失败类别、作业状态），不解析模型可见正文，也不看界面文本。``observation`` 与
``observation_fingerprint`` 在这里保留导出名，实现已移到 ``runtime.evidence``。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import Failure
from .resources import native_path
from .runtime.evidence import (DIMENSIONS, Evidence, facts, fingerprint as observation_fingerprint,
                               observation)
from .store import encode, now

__all__ = ["Progress", "Verdict", "observation", "observation_fingerprint", "ordinary_path"]


@dataclass(frozen=True)
class Verdict:
    """一次进展判定的结论；调用方据此投影，不重复计算。"""

    tool: str
    new: bool
    stalled: int
    limit: int
    changed: tuple[str, ...] = ()
    evidence: Evidence | None = None
    replay: bool = False
    repeated_request: bool = False
    obstacle_repeats: int = 0
    recovery: dict = field(default_factory=dict)

    def payload(self) -> dict:
        return {"new_observation": self.new, "stalled": self.stalled, "limit": self.limit,
                "changed_dimensions": list(self.changed), "replayed_call": self.replay,
                "repeated_request": self.repeated_request,
                **({"obstacle_repeats": self.obstacle_repeats} if self.obstacle_repeats > 1 else {}),
                **(self.evidence.payload() if self.evidence else {}),
                **({'context_recovered': True, **self.recovery} if self.recovery else {})}


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
    def stopped(stalled, limit, verdict: Verdict | None = None):
        if stalled >= limit:
            detail = ""
            if verdict is not None and verdict.obstacle_repeats > 1:
                detail = ("；同一阻碍已重复出现 %d 次（失败类别 %s），换工具或换说法都不算新事实，"
                          "需要先改变相关条件" % (verdict.obstacle_repeats, verdict.evidence.failure if verdict.evidence else "未知"))
            elif verdict is not None and verdict.repeated_request:
                detail = "；最后几次是相同请求的重复，结果与已保存的事实相同"
            raise Failure("NO_PROGRESS", f"连续{stalled}次工具观察没有新增证据（上限{limit}），已停止本回合。"
                f"请检查执行记录并调整任务；直接恢复不会重置计数{detail}。", 422)

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

    def _obstacles(self, db, deps, obstacle: str, limit: int) -> int:
        """同一未改变障碍在本活动里已被观察多少次（含本次之外的既有记录）。"""
        row = db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM events WHERE task_id=? AND kind='progress.observed' "
            "AND json_extract(payload,'$.turn_id')=? AND json_extract(payload,'$.assignment_id') IS ? "
            "AND json_extract(payload,'$.obstacle')=? ORDER BY seq DESC LIMIT ?)",
            (deps.task_id, deps.turn_id, deps.assignment_id, obstacle, max(1, limit * 4))).fetchone()
        return row[0]

    def record(self, deps, call_id, tool, args, result) -> Verdict:
        value = observation(tool, result)
        evidence = facts(tool, result)
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
            previous = None if prior else db.execute(
                "SELECT payload FROM events WHERE task_id=? AND kind='progress.observed' "
                "AND json_extract(payload,'$.turn_id')=? AND json_extract(payload,'$.assignment_id') IS ? "
                "ORDER BY seq DESC LIMIT 1",
                (deps.task_id, deps.turn_id, deps.assignment_id)).fetchone()
            if prior:
                if prior["decision_sha256"] != decision:
                    raise Failure("VERSION_CONFLICT", "重放的工具观察载荷变化", 409)
                stalled, limit = prior["stalled"], prior["limit_value"]
                verdict = Verdict(tool=tool, new=False, stalled=stalled, limit=limit,
                                  changed=evidence.changed_from(_restore(previous)), evidence=evidence, replay=True)
                self.stopped(stalled, limit, verdict)
                return verdict
            latest = self.latest(db, deps)
            limit = latest["limit_value"] if latest else state["config_snapshot"].get("generation", {}).get("max_unchanged_decisions", self.config.max_unchanged_decisions)
            seen = fingerprint is not None and db.execute("SELECT 1 FROM progress_observations WHERE task_id=? AND revision=? AND scope=? AND turn_id=? AND fingerprint=? LIMIT 1",
                (deps.task_id, deps.revision, scope, deps.turn_id, fingerprint)).fetchone()
            progressed = fingerprint is not None and not seen
            recovery = self.recovery(db, deps, tool, fingerprint) if seen else None
            stalled = 0 if progressed else (latest["stalled"] if latest else 0) + (0 if recovery else 1)
            repeated = bool(not progressed and db.execute(
                "SELECT 1 FROM progress_observations WHERE task_id=? AND revision=? AND scope=? AND turn_id=? AND decision_sha256=? LIMIT 1",
                (deps.task_id, deps.revision, scope, deps.turn_id, decision)).fetchone())
            repeats = self._obstacles(db, deps, evidence.obstacle, limit) + 1 if evidence.obstacle else 0
            verdict = Verdict(tool=tool, new=progressed, stalled=stalled, limit=limit,
                              changed=evidence.changed_from(_restore(previous)) if progressed else (),
                              evidence=evidence, repeated_request=repeated,
                              obstacle_repeats=0 if progressed else repeats, recovery=recovery or {})
            db.execute("INSERT INTO progress_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (deps.task_id, deps.revision, scope, deps.turn_id, call_id, request_id, tool, decision, fingerprint, stalled, limit, now()))
            self.store.event(db, deps.task_id, "progress.observed", {"turn_id": deps.turn_id,
                "assignment_id": deps.assignment_id, "context_manifest_id": request_id or None,
                "tool": tool, "observation_sha256": fingerprint, **verdict.payload()})
        # Commit the observation before stopping, including successful effects.
        self.stopped(stalled, limit, verdict)
        return verdict


def _restore(row) -> Evidence | None:
    """Rebuild the previous evidence view from its event; unknown shapes stay unknown."""
    if not row:
        return None
    try:
        payload = json.loads(row["payload"])
    except (ValueError, TypeError):
        return None
    facets = tuple((name, payload.get("facets", {}).get(name, "")) for name in payload.get("dimensions", ())
                   if name in DIMENSIONS)
    return Evidence(tool=payload.get("tool", ""), facets=facets, failure=payload.get("failure_code"),
                    obstacle=payload.get("obstacle"))


def ordinary_path(path):
    """Compare already resolved Windows paths independently of long-path spelling."""
    value = str(path)
    return Path('\\\\' + value[8:] if value.startswith('\\\\?\\UNC\\') else value.removeprefix('\\\\?\\'))
