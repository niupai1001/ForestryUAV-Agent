"""会话事实的读取与提交边界。

`Store` 负责事务、表名和事件写入；这里只把「会话里已经发生过什么」整理成运行
核心能直接使用的形状。所有读取都是只读快照，所有提交都走 `Store.change`，
因此状态与事件仍在同一次事务里落地。

不使用「最后一个 completed 回合」作为历史起点：失败、暂停、取消回合里的用户
指令同样是已提交事实，必须能被后续回合看到。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..store import Store, encode, now

# 回合状态里表示「不会再自动继续」的集合；其余状态仍属于未结清活动。
SETTLED_TURNS = {"completed", "cancelled"}
JOB_TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


@dataclass(frozen=True)
class UserRequest:
    """一条已提交的用户消息及其回合状态。"""

    turn_id: str
    revision: int
    text: str
    status: str
    created_at: str

    def public(self, *, limit: int = 1200) -> dict:
        text = self.text.strip()
        truncated = len(text) > limit
        return {"turn_id": self.turn_id, "spec_revision": self.revision, "status": self.status,
                "created_at": self.created_at, "text": text[:limit] if truncated else text,
                "characters": len(self.text), "truncated": truncated}


@dataclass(frozen=True)
class Activity:
    """一个作业记录，连同发起它的消息和执行输入。

    旧记录缺少的字段标 ``unknown``，不按推测补齐。
    """

    job_id: str
    operation: str
    name: str | None
    status: str
    request_turn_id: str | None
    call_id: str | None
    inputs: dict
    result: dict
    seconds: int | None
    created_at: str | None
    updated_at: str | None

    @property
    def terminal(self) -> bool:
        return self.status in JOB_TERMINAL

    def public(self) -> dict:
        return {"job_id": self.job_id, "operation": self.operation,
                "name": self.name, "name_status": "recorded" if self.name else "unknown",
                "status": self.status, "request_turn_id": self.request_turn_id,
                "call_id": self.call_id, "inputs": self.inputs, "result": self.result,
                "seconds": self.seconds, "created_at": self.created_at,
                "updated_at": self.updated_at}


class StoreSessionRepository:
    """`SessionRepository` 的存储实现，并额外提供会话事实查询。"""

    def __init__(self, store: Store):
        self.store = store

    # --- 提交边界 -----------------------------------------------------------
    def read(self, task_id: str) -> dict:
        return self.store.task(task_id)

    def commit(self, task_id: str, kind: str, mutate, payload: dict | None = None) -> dict:
        return self.store.change(task_id, kind, mutate, payload)

    # --- 已提交用户消息 -----------------------------------------------------
    def turns(self, task_id: str, revision: int | None = None) -> list[dict]:
        sql = "SELECT id,task_id,revision,prompt,status,created_at FROM turns WHERE task_id=?"
        args: list = [task_id]
        if revision is not None:
            sql += " AND revision=?"
            args.append(revision)
        with self.store.snapshot() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY created_at,rowid", args)]

    def committed_requests(self, task_id: str, *, after_turn_id: str | None = None,
                           exclude: tuple = ()) -> list[UserRequest]:
        """已提交但还没有可靠原生历史的用户消息，按提交顺序返回。

        ``after_turn_id`` 是已继承检查点所属回合，它之前的消息已经在原生历史里；
        它为 ``None`` 时说明没有任何可继承历史，全部已提交消息都要承接。
        """
        rows = self.turns(task_id)
        position = None
        if after_turn_id:
            position = next((index for index, row in enumerate(rows) if row["id"] == after_turn_id), None)
        window = rows[position + 1:] if position is not None else rows
        return [UserRequest(turn_id=row["id"], revision=row["revision"], text=row["prompt"] or "",
                            status=row["status"], created_at=row["created_at"])
                for row in window if row["id"] not in exclude and (row["prompt"] or "").strip()]

    def unrepresented_requests(self, task_id: str, turn_id: str, *, after_turn_id: str | None) -> list[UserRequest]:
        """当前回合之外、没有进入原生历史的已提交请求（含失败/暂停回合）。"""
        return self.committed_requests(task_id, after_turn_id=after_turn_id, exclude=(turn_id,))

    # --- 活动（作业）记录 ---------------------------------------------------
    def activities(self, task_id: str, revision: int, *, limit: int = 8) -> list[Activity]:
        with self.store.snapshot() as db:
            rows = db.execute("SELECT j.id,j.status,j.payload,j.updated_at,a.turn_id,a.call_id "
                "FROM jobs j JOIN actions a ON j.action_id=a.id WHERE j.task_id=? AND j.revision=? "
                "ORDER BY j.rowid DESC LIMIT ?", (task_id, revision, limit)).fetchall()
        values = []
        for row in rows:
            payload = json.loads(row["payload"])
            mounts = payload.get("mounts") or []
            artifact_ids = payload.get("artifact_ids") or []
            environment = payload.get("environment") or {}
            values.append(Activity(
                job_id=payload.get("job_id", row["id"]),
                operation=payload.get("kind", "unknown"),
                name=payload.get("name") or None,
                status=row["status"],
                request_turn_id=row["turn_id"],
                call_id=row["call_id"],
                inputs={"mount_count": len(mounts),
                        "resource_ids": [m.get("resource_id") for m in mounts[:4] if m.get("resource_id")],
                        "environment_artifact_id": environment.get("artifact_id")},
                result={"artifact_count": len(artifact_ids), "artifact_ids": artifact_ids[-4:],
                        "exit_code": payload.get("exit_code"), "blocker": payload.get("blocker")},
                seconds=payload.get("seconds"), created_at=payload.get("created_at"),
                updated_at=row["updated_at"]))
        return values

    def open_activities(self, task_id: str, revision: int) -> list[Activity]:
        return [activity for activity in self.activities(task_id, revision, limit=24) if not activity.terminal]

    # --- 未结清动作 ---------------------------------------------------------
    def settle_interrupted_actions(self, task_id: str, *, keep_turn_id: str) -> list[dict]:
        """把中断回合留下的 pending 动作结清为「结果未确认」。

        这些动作的副作用窗口已经打开，但工具没有返回。它们既不能重放成成功，
        也不能继续以 pending 阻塞交付和清理：按未确认失败结清，同时保留作业
        记录，让作业本身仍可被观察和取消。仍在运行的回合和委派自己的活动不在
        此列——它们的状态由各自执行范围负责。
        """
        with self.store.snapshot() as db:
            pending = db.execute("SELECT a.id FROM actions a JOIN turns t ON t.id=a.turn_id "
                                 "WHERE a.task_id=? AND a.status='pending' AND a.turn_id!=? "
                                 "AND t.status!='running' LIMIT 1", (task_id, keep_turn_id)).fetchone()
        if not pending:
            return []
        settled = []
        with self.store.transaction() as db:
            # 会话正在删除时 read_task 会抛出 SESSION_DELETING，这里不额外写回。
            self.store.read_task(db, task_id)
            # 只结清「回合已经不在运行、且不属于委派」的动作：正在运行的回合和
            # 委派自己的活动由各自的执行范围负责，父回合不能替它们下结论。
            rows = db.execute("SELECT a.id,a.turn_id,a.call_id,a.kind,a.revision FROM actions a "
                "JOIN turns t ON t.id=a.turn_id WHERE a.task_id=? AND a.status='pending' AND a.turn_id!=? "
                "AND t.status!='running' AND a.turn_id NOT IN "
                "(SELECT json_extract(payload,'$.run_id') FROM assignments WHERE task_id=?)",
                (task_id, keep_turn_id, task_id)).fetchall()
            for row in rows:
                job = db.execute("SELECT id,status FROM jobs WHERE action_id=?", (row["id"],)).fetchone()
                result = {"kind": "failed", "action_id": row["id"],
                          "error": {"code": "TURN_INTERRUPTED",
                                    "message": "回合中断时副作用窗口已打开，工具没有返回；结果未确认，不视为成功。"}}
                if job:
                    result["job"] = {"job_id": job["id"], "status": job["status"]}
                    result["error"]["message"] += "已登记的作业记录仍可继续观察或取消。"
                db.execute("UPDATE actions SET status='failed',result=? WHERE id=? AND status='pending'",
                           (encode(result), row["id"]))
                db.execute("UPDATE attempts SET effect_state='unconfirmed',finished_at=? WHERE action_id=? "
                           "AND effect_state='in_progress'", (now(), row["id"]))
                self.store.event(db, task_id, "action.interrupted", {
                    "action_id": row["id"], "turn_id": row["turn_id"], "call_id": row["call_id"],
                    "tool": row["kind"], "spec_revision": row["revision"],
                    "job_id": job["id"] if job else None, "effect_state": "unconfirmed"})
                settled.append({"action_id": row["id"], "turn_id": row["turn_id"], "tool": row["kind"],
                                "job_id": job["id"] if job else None})
        return settled

    # --- 其它事实 -----------------------------------------------------------
    def turn_outcomes(self, task_id: str, turn_ids) -> dict:
        """这些回合留下了什么：可见正文前缀、中断标记、停止原因。

        停止原因来自 ``run.stopped`` 事件，不依赖会被下个回合清空的状态字段。
        """
        wanted = set(turn_ids)
        if not wanted:
            return {}
        outcomes = {turn_id: {"assistant_prefix_characters": 0, "interrupted": False, "error": None}
                    for turn_id in wanted}
        with self.store.snapshot() as db:
            state = self.store.read_task(db, task_id)
            for message in state["messages"]:
                turn = message.get("turn_id")
                if message.get("role") != "assistant" or turn not in wanted:
                    continue
                outcomes[turn]["assistant_prefix_characters"] += len(message.get("text") or "")
                outcomes[turn]["interrupted"] = outcomes[turn]["interrupted"] or bool(message.get("interrupted"))
            for row in db.execute("SELECT payload FROM events WHERE task_id=? AND kind IN ('run.stopped','run.stale_stopped') "
                    "ORDER BY seq DESC LIMIT 64", (task_id,)):
                payload = json.loads(row["payload"])
                turn = payload.get("turn_id")
                if turn in wanted and outcomes[turn]["error"] is None:
                    blocker = payload.get("blocker")
                    if not blocker and payload.get("message"):
                        blocker = {"code": payload.get("code", "RUN_STOPPED"), "message": payload["message"]}
                    if blocker:
                        outcomes[turn]["error"] = {"code": blocker.get("code"), "message": str(blocker.get("message"))[:300]}
        return outcomes

    def latest_message(self, task_id: str, *, role: str | None = None) -> dict | None:
        with self.store.snapshot() as db:
            state = self.store.read_task(db, task_id)
        for message in reversed(state["messages"]):
            if role is None or message.get("role") == role:
                return message
        return None

    def record_activity_note(self, task_id: str, note: dict) -> None:
        """把一次承接说明写成事件，便于追问时复核而不是靠模型记性。"""
        with self.store.transaction() as db:
            self.store.event(db, task_id, "session.carried_requests", note)
