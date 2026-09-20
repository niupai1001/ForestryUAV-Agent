"""SQLite run/action state plus append-only JSONL event logs."""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
from pathlib import Path
import sqlite3
import shutil
import threading
import time
import uuid

from .kernel.trace import enrich_event
from .session.state import ACTIVE, PAUSABLE, TERMINAL, transition
from .storage import AssetError


LOGGER = logging.getLogger(__name__)


def _encode_arguments(arguments: dict) -> str:
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


def _outcome_ok(result: dict, event: dict | None = None) -> bool:
    if "outcome_ok" in result:
        return bool(result["outcome_ok"])
    if event is not None and "outcome_ok" in event:
        return bool(event["outcome_ok"])
    if "ok" in result:
        return bool(result["ok"])
    return bool((event or {}).get("ok", False))


class RunStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs = self.root / "runs"
        self.logs.mkdir(exist_ok=True)
        self.database = self.root / "runs.sqlite3"
        self.lock = threading.RLock()
        with self.db() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT NOT NULL,
                state TEXT NOT NULL, messages_json TEXT NOT NULL, asset_ids_json TEXT NOT NULL,
                use_tools INTEGER NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                pause_requested INTEGER NOT NULL DEFAULT 0,
                model_calls INTEGER NOT NULL DEFAULT 0, final_json TEXT,
                agent_input_count INTEGER NOT NULL DEFAULT 0,
                state_version INTEGER NOT NULL DEFAULT 0, recovery_reason TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)"""
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            if "agent_input_count" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN agent_input_count INTEGER NOT NULL DEFAULT 0")
            if "pause_requested" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0")
            if "state_version" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0")
            if "recovery_reason" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN recovery_reason TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS run_chat ON runs(owner, chat_id, created_at)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT NOT NULL,
                created_at REAL NOT NULL, turn_id TEXT, PRIMARY KEY(run_id, seq))"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL, result_json TEXT, state TEXT NOT NULL,
                started_at REAL NOT NULL, finished_at REAL, turn_id TEXT)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS job_refs (
                run_id TEXT NOT NULL, job_id TEXT NOT NULL, job_type TEXT,
                state TEXT, turn_id TEXT, action_id TEXT,
                PRIMARY KEY(run_id, job_id))"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                agent_run_id TEXT NOT NULL UNIQUE, input_start INTEGER NOT NULL,
                input_end INTEGER NOT NULL, state TEXT NOT NULL,
                checkpoint_state TEXT NOT NULL DEFAULT 'unknown',
                resume_agent_run_id TEXT, input_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(run_id, ordinal))"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS inputs (
                id TEXT NOT NULL, owner TEXT NOT NULL, chat_id TEXT NOT NULL,
                run_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                asset_ids_json TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL,
                turn_id TEXT, ordinal INTEGER NOT NULL, created_at REAL NOT NULL,
                reserved_at REAL, consumed_at REAL,
                PRIMARY KEY(owner,chat_id,id))"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS steps (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                number INTEGER NOT NULL, context_json TEXT NOT NULL,
                estimated_tokens INTEGER, provider_usage_json TEXT,
                created_at REAL NOT NULL, finished_at REAL,
                UNIQUE(turn_id,number))"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, action_id TEXT NOT NULL, run_id TEXT NOT NULL,
                turn_id TEXT, ordinal INTEGER NOT NULL, state TEXT NOT NULL,
                operation_started INTEGER NOT NULL DEFAULT 0,
                result_json TEXT, error_json TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(action_id,ordinal))"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS artifact_refs (
                run_id TEXT NOT NULL, turn_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                job_id TEXT, action_id TEXT, created_at REAL NOT NULL,
                PRIMARY KEY(run_id, turn_id, asset_id))"""
            )
            for table, column, declaration in (
                ("events", "turn_id", "TEXT"),
                ("actions", "turn_id", "TEXT"),
                ("job_refs", "turn_id", "TEXT"),
                ("job_refs", "action_id", "TEXT"),
                ("turns", "resume_agent_run_id", "TEXT"),
                ("turns", "input_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                existing = {
                    row["name"] for row in conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            conn.execute("CREATE INDEX IF NOT EXISTS event_turn ON events(run_id, turn_id, seq)")
            conn.execute("CREATE INDEX IF NOT EXISTS action_turn ON actions(run_id, turn_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS turn_run ON turns(run_id, ordinal)")
            conn.execute("CREATE INDEX IF NOT EXISTS input_run ON inputs(run_id, ordinal)")
            conn.execute("CREATE INDEX IF NOT EXISTS attempt_action ON attempts(action_id, ordinal)")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _input_id(message: dict) -> str:
        value = str(message.get("input_id") or "").strip()
        return value or "input_" + uuid.uuid4().hex

    def create(self, owner: str, chat_id: str, messages: list[dict], asset_ids: list[str], use_tools: bool) -> dict:
        run_id = "run_" + uuid.uuid4().hex
        turn_id = "turn_" + uuid.uuid4().hex
        agent_run_id = "agent_" + uuid.uuid4().hex
        now = time.time()
        normalized = [dict(item, input_id=self._input_id(item)) for item in messages]
        input_ids = [item["input_id"] for item in normalized]
        with self.db() as conn:
            for input_id in input_ids:
                existing = conn.execute(
                    "SELECT run_id FROM inputs WHERE owner=? AND chat_id=? AND id=?",
                    (owner, chat_id, input_id),
                ).fetchone()
                if existing:
                    return self.get(existing["run_id"], owner)
            active = conn.execute(
                """SELECT id FROM runs WHERE owner=? AND chat_id=?
                AND state IN ('queued','running','waiting','canceling') LIMIT 1""",
                (owner, chat_id),
            ).fetchone()
            if active:
                raise AssetError("This Session already has an active Run")
            conn.execute(
                "INSERT INTO runs(id,owner,chat_id,state,messages_json,asset_ids_json,use_tools,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, owner, chat_id, "queued", json.dumps(normalized, ensure_ascii=False), json.dumps(asset_ids), int(use_tools), now, now),
            )
            conn.execute(
                """INSERT INTO turns(
                id,run_id,ordinal,agent_run_id,input_start,input_end,state,input_ids_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (turn_id, run_id, 1, agent_run_id, 0, len(normalized), "queued", json.dumps(input_ids), now, now),
            )
            for ordinal, item in enumerate(normalized, 1):
                conn.execute(
                    """INSERT INTO inputs(id,owner,chat_id,run_id,role,content,asset_ids_json,state,turn_id,ordinal,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (item["input_id"], owner, chat_id, run_id, item.get("role", "user"), str(item.get("content") or ""), json.dumps(asset_ids), "pending", turn_id, ordinal, now),
                )
        return self.get(run_id, owner)

    def get(self, run_id: str, owner: str | None = None) -> dict:
        with self.db() as conn:
            if owner is None:
                row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if row is None:
            raise AssetError("Run not found or not accessible")
        return self._decode(dict(row))

    def run_for_input(self, owner: str, chat_id: str, input_id: str) -> dict | None:
        with self.db() as conn:
            row = conn.execute(
                "SELECT run_id FROM inputs WHERE owner=? AND chat_id=? AND id=?",
                (owner, chat_id, input_id),
            ).fetchone()
        return self.get(row["run_id"], owner) if row else None

    def list_active(self) -> list[dict]:
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM runs WHERE state IN ('queued','running','waiting','canceling') ORDER BY created_at").fetchall()
        return [self._decode(dict(row)) for row in rows]

    def set_state(self, run_id: str, state: str, final: dict | None = None,
                  recovery_reason: str | None = None) -> None:
        event_record = None
        with self.lock, self.db() as conn:
            current = conn.execute(
                "SELECT state,state_version FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if current is None:
                raise AssetError("Run not found")
            transition(current["state"], state)
            if current["state"] == state and final is None and recovery_reason is None:
                return
            conn.execute(
                """UPDATE runs SET state=?, final_json=COALESCE(?, final_json),
                recovery_reason=?, state_version=state_version+1, updated_at=? WHERE id=?""",
                (state, json.dumps(final, ensure_ascii=False) if final is not None else None, recovery_reason, time.time(), run_id),
            )
            if current["state"] != state or recovery_reason:
                version = int(current["state_version"] or 0) + 1
                now = time.time()
                event = {
                    "type": "run_state", "state": state,
                    "from_state": current["state"],
                    "state_version": version,
                    "recovery_reason": recovery_reason,
                }
                seq = int(conn.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0])
                conn.execute(
                    "INSERT INTO events(run_id,seq,event_json,created_at) VALUES(?,?,?,?)",
                    (run_id, seq, json.dumps(event, ensure_ascii=False), now),
                )
                event_record = (seq, now, event)
        if event_record:
            self._export_log(run_id, *event_record)

    def request_cancel(self, run_id: str, owner: str) -> dict:
        self.get(run_id, owner)
        with self.db() as conn:
            conn.execute("UPDATE runs SET cancel_requested=1,pause_requested=0,updated_at=? WHERE id=?", (time.time(), run_id))
        self.set_state(run_id, "canceling")
        return self.get(run_id, owner)

    def finish_cancel(self, run_id: str) -> None:
        now = time.time()
        with self.db() as conn:
            conn.execute("UPDATE turns SET state='canceled',updated_at=? WHERE run_id=? AND state IN ('queued','running','waiting','canceling')", (now, run_id))
        self.set_state(run_id, "canceled")

    def finish_cancel_incomplete(self, run_id: str, reason: str) -> None:
        self.append(run_id, {
            "type": "cancel_timeout", "state": "cancel_incomplete",
            "content": reason,
        })
        self.set_state(run_id, "cancel_incomplete", recovery_reason=reason)

    def is_cancelled(self, run_id: str) -> bool:
        with self.db() as conn:
            row = conn.execute("SELECT cancel_requested FROM runs WHERE id=?", (run_id,)).fetchone()
        return row is None or bool(row["cancel_requested"])

    def request_pause(self, run_id: str, owner: str) -> dict:
        run = self.get(run_id, owner)
        if run["state"] not in PAUSABLE:
            raise AssetError("Only a queued or running Run can be paused")
        with self.db() as conn:
            conn.execute(
                "UPDATE runs SET pause_requested=1,updated_at=? WHERE id=?",
                (time.time(), run_id),
            )
        return self.get(run_id, owner)

    def is_pause_requested(self, run_id: str) -> bool:
        with self.db() as conn:
            row = conn.execute(
                "SELECT pause_requested FROM runs WHERE id=?", (run_id,)
            ).fetchone()
        return bool(row and row["pause_requested"])

    def add_message(self, run_id: str, owner: str, content: str, asset_ids: list[str] | None = None) -> dict:
        return self.add_input(run_id, owner, "user", content, asset_ids)

    def add_input(self, run_id: str, owner: str, role: str, content: str,
                  asset_ids: list[str] | None = None, input_id: str | None = None) -> dict:
        run = self.get(run_id, owner)
        if run["state"] not in TERMINAL | {"paused", "waiting"}:
            raise AssetError("Run is already active")
        if role not in {"user", "system"}:
            raise AssetError("Unsupported Run input role")
        input_id = input_id or "input_" + uuid.uuid4().hex
        with self.db() as conn:
            existing = conn.execute("SELECT run_id FROM inputs WHERE owner=? AND chat_id=? AND id=?", (owner, run["chat_id"], input_id)).fetchone()
        if existing:
            return self.get(existing["run_id"], owner)
        messages = run["messages"] + [{"role": role, "content": content, "input_id": input_id}]
        merged_assets = list(dict.fromkeys(run["asset_ids"] + list(asset_ids or [])))
        turn_id = "turn_" + uuid.uuid4().hex
        agent_run_id = "agent_" + uuid.uuid4().hex
        now = time.time()
        with self.db() as conn:
            ordinal = int(conn.execute(
                "SELECT COALESCE(MAX(ordinal),0)+1 FROM turns WHERE run_id=?",
                (run_id,),
            ).fetchone()[0])
            conn.execute(
                "UPDATE runs SET messages_json=?,asset_ids_json=?,cancel_requested=0,pause_requested=0,state='queued',final_json=NULL,updated_at=? WHERE id=?",
                (json.dumps(messages, ensure_ascii=False), json.dumps(merged_assets), now, run_id),
            )
            conn.execute(
                """INSERT INTO turns(
                id,run_id,ordinal,agent_run_id,input_start,input_end,state,input_ids_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    turn_id, run_id, ordinal, agent_run_id, len(run["messages"]),
                    len(messages), "queued", json.dumps([input_id]), now, now,
                ),
            )
            conn.execute("""INSERT INTO inputs(id,owner,chat_id,run_id,role,content,asset_ids_json,state,turn_id,ordinal,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (input_id, owner, run["chat_id"], run_id, role, content, json.dumps(asset_ids or []), "pending", turn_id, ordinal, now))
        return self.get(run_id, owner)

    def queue_input(
        self, run_id: str, owner: str, role: str, content: str,
        asset_ids: list[str] | None = None, input_id: str | None = None,
    ) -> dict:
        run = self.get(run_id, owner)
        if run["state"] not in {"queued", "running"}:
            raise AssetError("Run is not active; continue it as a new Turn instead")
        if role != "user":
            raise AssetError("Only user input can be queued during an active Run")
        input_id = input_id or "input_" + uuid.uuid4().hex
        with self.db() as conn:
            existing = conn.execute("SELECT run_id FROM inputs WHERE owner=? AND chat_id=? AND id=?", (owner, run["chat_id"], input_id)).fetchone()
        if existing:
            return self.get(existing["run_id"], owner)
        messages = run["messages"] + [{"role": role, "content": content, "input_id": input_id}]
        merged_assets = list(dict.fromkeys(run["asset_ids"] + list(asset_ids or [])))
        turn_id = "turn_" + uuid.uuid4().hex
        agent_run_id = "agent_" + uuid.uuid4().hex
        now = time.time()
        with self.db() as conn:
            ordinal = int(conn.execute(
                "SELECT COALESCE(MAX(ordinal),0)+1 FROM turns WHERE run_id=?",
                (run_id,),
            ).fetchone()[0])
            conn.execute(
                """UPDATE runs SET messages_json=?,asset_ids_json=?,updated_at=?
                WHERE id=?""",
                (
                    json.dumps(messages, ensure_ascii=False),
                    json.dumps(merged_assets), now, run_id,
                ),
            )
            conn.execute(
                """INSERT INTO turns(
                id,run_id,ordinal,agent_run_id,input_start,input_end,state,input_ids_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    turn_id, run_id, ordinal, agent_run_id, len(run["messages"]),
                    len(messages), "queued", json.dumps([input_id]), now, now,
                ),
            )
            conn.execute("""INSERT INTO inputs(id,owner,chat_id,run_id,role,content,asset_ids_json,state,turn_id,ordinal,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (input_id, owner, run["chat_id"], run_id, role, content, json.dumps(asset_ids or []), "pending", turn_id, ordinal, now))
        return self.get(run_id, owner)

    def turns(self, run_id: str, owner: str | None = None) -> list[dict]:
        self.get(run_id, owner)
        with self.db() as conn:
            rows = conn.execute(
                "SELECT * FROM turns WHERE run_id=? ORDER BY ordinal", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_turn(self, run_id: str) -> dict | None:
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM turns WHERE run_id=? ORDER BY ordinal DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def has_queued_turn(self, run_id: str) -> bool:
        with self.db() as conn:
            row = conn.execute(
                "SELECT 1 FROM turns WHERE run_id=? AND state='queued' LIMIT 1",
                (run_id,),
            ).fetchone()
        return row is not None

    def claim_turn(self, run_id: str) -> dict:
        now = time.time()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM turns WHERE run_id=? AND state='queued' ORDER BY ordinal LIMIT 1",
                (run_id,),
            ).fetchone()
            if row is None:
                raise AssetError("Run has no queued Turn")
            conn.execute(
                "UPDATE turns SET state='running',updated_at=? WHERE id=? AND state='queued'",
                (now, row["id"]),
            )
            conn.execute("UPDATE inputs SET state='reserved',reserved_at=? WHERE turn_id=? AND state='pending'", (now, row["id"]))
        result = dict(row)
        result["state"] = "running"
        result["updated_at"] = now
        return result

    def turn_inputs(self, turn_id: str) -> list[dict]:
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM inputs WHERE turn_id=? ORDER BY ordinal", (turn_id,)).fetchall()
        return [dict(row) | {"asset_ids": json.loads(row["asset_ids_json"])} for row in rows]

    def mark_turn_consumed(self, run_id: str, turn_id: str, input_end: int) -> None:
        now = time.time()
        with self.db() as conn:
            conn.execute("UPDATE inputs SET state='consumed',consumed_at=? WHERE turn_id=? AND state IN ('pending','reserved')", (now, turn_id))
            conn.execute("UPDATE runs SET agent_input_count=MAX(agent_input_count,?),updated_at=? WHERE id=?", (input_end, now, run_id))

    def set_turn_state(
        self, turn_id: str, state: str, checkpoint_state: str | None = None
    ) -> None:
        with self.db() as conn:
            conn.execute(
                """UPDATE turns SET state=?, checkpoint_state=COALESCE(?,checkpoint_state),
                updated_at=? WHERE id=?""",
                (state, checkpoint_state, time.time(), turn_id),
            )

    def mark_inflight_actions_uncertain(self, run_id: str, turn_id: str | None) -> int:
        with self.db() as conn:
            cursor = conn.execute(
                """UPDATE actions SET state='unknown_after_crash',finished_at=?
                WHERE run_id=? AND state='running' AND (? IS NULL OR turn_id=?)""",
                (time.time(), run_id, turn_id, turn_id),
            )
        return int(cursor.rowcount)

    def latest_job_observations(self, run_id: str) -> list[dict]:
        latest = {}
        for event in self.events_all(run_id, self.get(run_id)["owner"]):
            if event.get("type") in {"job_status", "job_reconciled"} and event.get("job_id"):
                latest[event["job_id"]] = {
                    key: event.get(key) for key in (
                        "job_id", "job_type", "state", "terminal", "exit_code",
                        "output", "error", "progress_percent", "needs_finalization",
                    ) if event.get(key) is not None
                }
        return list(latest.values())

    def context_facts(self, run_id: str) -> dict:
        """Bounded execution facts for the next model request."""
        with self.db() as conn:
            actions = conn.execute(
                """SELECT id,tool_name,state,result_json FROM actions
                WHERE run_id=? ORDER BY started_at DESC LIMIT 20""",
                (run_id,),
            ).fetchall()
            jobs = conn.execute(
                "SELECT job_id,job_type,state,action_id FROM job_refs WHERE run_id=?",
                (run_id,),
            ).fetchall()
        completed = []
        unresolved = []
        for row in actions:
            item = {"action_id": row["id"], "tool": row["tool_name"], "state": row["state"]}
            if row["result_json"]:
                result = json.loads(row["result_json"])
                failure = result.get("failure") if isinstance(result, dict) else None
                if failure:
                    item["failure"] = failure
                data = result.get("data") if isinstance(result, dict) else None
                if isinstance(data, dict) and data.get("job_id"):
                    item["job_id"] = data["job_id"]
            (completed if row["state"] == "succeeded" else unresolved).append(item)
        return {
            "completed_actions": list(reversed(completed[:12])),
            "unresolved_actions": list(reversed(unresolved[:12])),
            "jobs": [dict(row) for row in jobs[:30]],
        }

    def latest_for_chat(self, owner: str, chat_id: str) -> dict | None:
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE owner=? AND chat_id=? ORDER BY created_at DESC LIMIT 1",
                (owner, chat_id),
            ).fetchone()
        return self._decode(dict(row)) if row else None

    def chat_title(self, owner: str, chat_id: str) -> str:
        run = self.latest_for_chat(owner, chat_id)
        if not run:
            return "新会话"
        first = next((item.get("content", "") for item in run["messages"] if item.get("role") == "user"), "")
        return str(first).strip().replace("\n", " ")[:36] or "新会话"

    def begin_step(self, run_id: str, turn_id: str, number: int,
                   context: dict, estimated_tokens: int | None = None) -> str:
        step_id = f"{turn_id}:step:{number}"
        now = time.time()
        event = {
            "type": "model_call", "number": number, "step_id": step_id,
            "context": context, "estimated_tokens": estimated_tokens,
            "turn_id": turn_id,
        }
        with self.lock, self.db() as conn:
            conn.execute(
                """INSERT INTO steps(id,run_id,turn_id,number,context_json,estimated_tokens,created_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(turn_id,number) DO NOTHING""",
                (step_id, run_id, turn_id, number, json.dumps(context, ensure_ascii=False), estimated_tokens, now),
            )
            seq = int(conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?",
                (run_id,),
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO events(run_id,seq,event_json,created_at,turn_id) VALUES(?,?,?,?,?)",
                (run_id, seq, json.dumps(event, ensure_ascii=False), now, turn_id),
            )
            conn.execute(
                "UPDATE runs SET model_calls=model_calls+1,updated_at=? WHERE id=?",
                (now, run_id),
            )
        self._export_log(run_id, seq, now, event)
        return step_id

    def finish_step(self, step_id: str, provider_usage: dict | None = None) -> None:
        with self.db() as conn:
            conn.execute(
                "UPDATE steps SET provider_usage_json=?,finished_at=? WHERE id=?",
                (json.dumps(provider_usage or {}, ensure_ascii=False), time.time(), step_id),
            )

    def begin_action(self, run_id: str, turn_id: str | None, action_id: str,
                     tool_name: str, arguments: dict) -> dict:
        """Register a tool action before execution and return its replay decision."""
        now = time.time()
        encoded_args = _encode_arguments(arguments)
        event = None
        seq = None
        with self.lock, self.db() as conn:
            existing = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if existing:
                row = dict(existing)
                if row["run_id"] != run_id or row["tool_name"] != tool_name or row["arguments_json"] != encoded_args:
                    raise AssetError("Action id was reused with different tool arguments")
                result = json.loads(row["result_json"]) if row.get("result_json") else None
                return {"execute": False, "state": row["state"], "result": result}
            conn.execute(
                """INSERT INTO actions(id,run_id,tool_name,arguments_json,state,started_at,turn_id)
                VALUES(?,?,?,?,?,?,?)""",
                (action_id, run_id, tool_name, encoded_args, "running", now, turn_id),
            )
            attempt_id = "attempt_" + uuid.uuid4().hex
            conn.execute(
                """INSERT INTO attempts(id,action_id,run_id,turn_id,ordinal,state,created_at,updated_at)
                VALUES(?,?,?,?,1,'prepared',?,?)""",
                (attempt_id, action_id, run_id, turn_id, now, now),
            )
            event = enrich_event({
                "type": "tool_start", "action_id": action_id,
                "attempt_id": attempt_id, "name": tool_name,
                "arguments": arguments,
            })
            if turn_id is not None:
                event["turn_id"] = turn_id
            seq = int(conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?",
                (run_id,),
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO events(run_id,seq,event_json,created_at,turn_id) VALUES(?,?,?,?,?)",
                (run_id, seq, json.dumps(event, ensure_ascii=False), now, turn_id),
            )
        self._export_log(run_id, seq, now, event)
        return {"execute": True, "state": "prepared", "attempt_id": attempt_id}

    def mark_attempt_started(self, attempt_id: str) -> None:
        with self.db() as conn:
            conn.execute(
                "UPDATE attempts SET state='running',operation_started=1,updated_at=? WHERE id=? AND state='prepared'",
                (time.time(), attempt_id),
            )

    def finish_action(self, run_id: str, turn_id: str | None, action_id: str,
                      tool_name: str, result: dict, duration_seconds: float,
                      attempt_id: str | None = None) -> None:
        outcome_ok = _outcome_ok(result)
        state = "succeeded" if outcome_ok else "failed"
        now = time.time()
        event = {
            "type": "tool_end", "action_id": action_id,
            "attempt_id": attempt_id, "name": tool_name,
            "ok": bool(result.get("ok", False)), "outcome_ok": outcome_ok,
            "duration_seconds": duration_seconds, "result": result,
        }
        if turn_id is not None:
            event["turn_id"] = turn_id
        with self.lock, self.db() as conn:
            action = conn.execute(
                "SELECT tool_name FROM actions WHERE id=? AND run_id=?",
                (action_id, run_id),
            ).fetchone()
            if action is None or action["tool_name"] != tool_name:
                raise AssetError("Tool result does not match a registered action")
            if attempt_id is None:
                attempt = conn.execute(
                    """SELECT id FROM attempts
                    WHERE action_id=? AND run_id=?
                    ORDER BY ordinal DESC LIMIT 1""",
                    (action_id, run_id),
                ).fetchone()
                attempt_id = attempt["id"] if attempt is not None else None
            attempt = conn.execute(
                """SELECT id FROM attempts
                WHERE id=? AND action_id=? AND run_id=?""",
                (attempt_id, action_id, run_id),
            ).fetchone()
            if attempt is None:
                raise AssetError("Attempt does not belong to this Run and Action")
            event["attempt_id"] = attempt_id
            action_update = conn.execute(
                "UPDATE actions SET result_json=?,state=?,finished_at=? WHERE id=? AND run_id=?",
                (json.dumps(result, ensure_ascii=False), state, now, action_id, run_id),
            )
            if action_update.rowcount != 1:
                raise AssetError("Action result was not persisted")
            attempt_update = conn.execute(
                """UPDATE attempts SET state=?,result_json=?,updated_at=?
                WHERE id=? AND action_id=? AND run_id=?""",
                (
                    state, json.dumps(result, ensure_ascii=False), now,
                    attempt_id, action_id, run_id,
                ),
            )
            if attempt_update.rowcount != 1:
                raise AssetError("Attempt result was not persisted")
            data = result.get("data") or {}
            job_id = data.get("job_id") if isinstance(data, dict) else None
            if job_id:
                conn.execute(
                    """INSERT INTO job_refs(run_id,job_id,job_type,state,turn_id,action_id)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,job_id) DO UPDATE SET
                    job_type=COALESCE(excluded.job_type,job_refs.job_type),
                    state=excluded.state,
                    turn_id=COALESCE(job_refs.turn_id,excluded.turn_id),
                    action_id=COALESCE(job_refs.action_id,excluded.action_id)""",
                    (run_id, job_id, data.get("job_type", "code"),
                     data.get("state"), turn_id, action_id),
                )
            seq = int(conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?",
                (run_id,),
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO events(run_id,seq,event_json,created_at,turn_id) VALUES(?,?,?,?,?)",
                (run_id, seq, json.dumps(event, ensure_ascii=False), now, turn_id),
            )
        self._export_log(run_id, seq, now, event)

    def append(self, run_id: str, event: dict, turn_id: str | None = None,
               project: bool = True) -> int:
        now = time.time()
        event = enrich_event(event, occurred_at=now)
        if turn_id is not None:
            event.setdefault("turn_id", turn_id)
        with self.lock, self.db() as conn:
            if project and event.get("type") == "tool_start":
                action = conn.execute(
                    "SELECT * FROM actions WHERE id=?", (event["action_id"],)
                ).fetchone()
                if action is None:
                    attempt_id = str(
                        event.get("attempt_id") or "attempt_" + uuid.uuid4().hex
                    )
                    event["attempt_id"] = attempt_id
                    conn.execute(
                        """INSERT INTO actions(
                        id,run_id,tool_name,arguments_json,state,started_at,turn_id
                        ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            event["action_id"], run_id, event.get("name", ""),
                            _encode_arguments(event.get("arguments")),
                            "running", now, turn_id,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO attempts(
                        id,action_id,run_id,turn_id,ordinal,state,
                        operation_started,created_at,updated_at
                        ) VALUES(?,?,?,?,1,'running',1,?,?)""",
                        (
                            attempt_id, event["action_id"], run_id, turn_id,
                            now, now,
                        ),
                    )
                else:
                    encoded_args = _encode_arguments(event.get("arguments"))
                    if (
                        action["run_id"] != run_id
                        or action["tool_name"] != event.get("name", "")
                        or action["arguments_json"] != encoded_args
                    ):
                        raise AssetError(
                            "Action id was reused with different tool arguments"
                        )
                if action is not None:
                    if not event.get("attempt_id"):
                        attempt = conn.execute(
                            """SELECT id FROM attempts
                            WHERE action_id=? AND run_id=?
                            ORDER BY ordinal DESC LIMIT 1""",
                            (event["action_id"], run_id),
                        ).fetchone()
                        if attempt is not None:
                            event["attempt_id"] = attempt["id"]
                    attempt = conn.execute(
                        """SELECT id FROM attempts
                        WHERE id=? AND action_id=? AND run_id=?""",
                        (
                            event.get("attempt_id"), event["action_id"], run_id,
                        ),
                    ).fetchone()
                    if attempt is None:
                        raise AssetError(
                            "Attempt does not belong to this Run and Action"
                        )
            if project and event.get("type") == "tool_end":
                action = conn.execute(
                    "SELECT tool_name FROM actions WHERE id=? AND run_id=?",
                    (event.get("action_id"), run_id),
                ).fetchone()
                if action is None or action["tool_name"] != event.get("name", ""):
                    raise AssetError("Tool result does not match a registered action")
                if not event.get("attempt_id"):
                    attempt = conn.execute(
                        """SELECT id FROM attempts
                        WHERE action_id=? AND run_id=?
                        ORDER BY ordinal DESC LIMIT 1""",
                        (event.get("action_id"), run_id),
                    ).fetchone()
                    if attempt is not None:
                        event["attempt_id"] = attempt["id"]
                attempt = conn.execute(
                    """SELECT id FROM attempts
                    WHERE id=? AND action_id=? AND run_id=?""",
                    (
                        event.get("attempt_id"), event.get("action_id"), run_id,
                    ),
                ).fetchone()
                if attempt is None:
                    raise AssetError(
                        "Attempt does not belong to this Run and Action"
                    )
            encoded = json.dumps(event, ensure_ascii=False, allow_nan=False)
            row = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS seq FROM events WHERE run_id=?", (run_id,)).fetchone()
            seq = int(row["seq"])
            conn.execute(
                "INSERT INTO events(run_id,seq,event_json,created_at,turn_id) VALUES(?,?,?,?,?)",
                (run_id, seq, encoded, now, turn_id),
            )
            if event.get("type") == "model_call":
                conn.execute("UPDATE runs SET model_calls=model_calls+1, updated_at=? WHERE id=?", (now, run_id))
            if project and event.get("type") == "tool_end":
                result = event.get("result") or {}
                outcome_ok = _outcome_ok(result, event)
                state = "succeeded" if outcome_ok else "failed"
                action_update = conn.execute(
                    "UPDATE actions SET result_json=?, state=?, finished_at=? WHERE id=? AND run_id=?",
                    (
                        json.dumps(event.get("result"), ensure_ascii=False), state,
                        now, event.get("action_id"), run_id,
                    ),
                )
                if action_update.rowcount != 1:
                    raise AssetError("Action result was not persisted")
                attempt_update = conn.execute(
                    """UPDATE attempts SET state=?,result_json=?,updated_at=?
                    WHERE id=? AND action_id=? AND run_id=?""",
                    (
                        state, json.dumps(event.get("result"), ensure_ascii=False),
                        now, event["attempt_id"], event.get("action_id"), run_id,
                    ),
                )
                if attempt_update.rowcount != 1:
                    raise AssetError("Attempt result was not persisted")
                data = result.get("data") or {}
                job_id = data.get("job_id") if isinstance(data, dict) else None
                if job_id:
                    conn.execute(
                        """INSERT INTO job_refs(
                        run_id,job_id,job_type,state,turn_id,action_id
                        ) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(run_id,job_id) DO UPDATE SET
                        job_type=COALESCE(excluded.job_type,job_refs.job_type),
                        state=excluded.state,
                        turn_id=COALESCE(job_refs.turn_id,excluded.turn_id),
                        action_id=COALESCE(job_refs.action_id,excluded.action_id)""",
                        (
                            run_id, job_id, data.get("job_type", "code"),
                            data.get("state"), turn_id, event.get("action_id"),
                        ),
                    )
            if event.get("type") in {"job_status", "job_reconciled"} and event.get("job_id"):
                conn.execute(
                    """INSERT INTO job_refs(
                    run_id,job_id,job_type,state,turn_id,action_id
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(run_id,job_id) DO UPDATE SET
                    job_type=COALESCE(excluded.job_type,job_refs.job_type),
                    state=excluded.state,
                        turn_id=COALESCE(job_refs.turn_id,excluded.turn_id),
                        action_id=COALESCE(job_refs.action_id,excluded.action_id)""",
                    (
                        run_id, event["job_id"], event.get("job_type"),
                        event.get("state"), turn_id, event.get("action_id"),
                    ),
                )
            if event.get("type") == "done" and turn_id:
                for artifact in event.get("artifacts") or []:
                    if not isinstance(artifact, dict) or not artifact.get("id"):
                        continue
                    metadata = artifact.get("metadata") or {}
                    conn.execute(
                        """INSERT OR REPLACE INTO artifact_refs(
                        run_id,turn_id,asset_id,job_id,action_id,created_at
                        ) VALUES(?,?,?,?,?,?)""",
                        (
                            run_id, turn_id, artifact["id"], metadata.get("job_id"),
                            metadata.get("action_id"), now,
                        ),
                    )
        self._export_log(run_id, seq, now, event)
        return seq

    def _export_log(
        self, run_id: str, seq: int, created_at: float, event: dict
    ) -> None:
        try:
            self._write_log(run_id, seq, created_at, event)
        except OSError:
            LOGGER.exception(
                "JSONL event export failed after SQLite commit "
                "(run_id=%s, seq=%s)",
                run_id, seq,
            )

    def _write_log(self, run_id: str, seq: int, created_at: float, event: dict) -> None:
        directory = self.logs / run_id
        directory.mkdir(exist_ok=True)
        with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"seq": seq, "created_at": created_at, **event},
                ensure_ascii=False,
            ) + "\n")

    def events_after(self, run_id: str, owner: str, after: int = 0, limit: int = 500) -> list[dict]:
        self.get(run_id, owner)
        with self.db() as conn:
            rows = conn.execute(
                "SELECT seq,event_json,created_at FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                (run_id, after, min(max(limit, 1), 1001)),
            ).fetchall()
        return [{"seq": row["seq"], "created_at": row["created_at"], **json.loads(row["event_json"])} for row in rows]

    def event_page(
        self, run_id: str, owner: str, after: int = 0, limit: int = 500
    ) -> dict:
        size = min(max(limit, 1), 1000)
        rows = self.events_after(run_id, owner, after, size + 1)
        has_more = len(rows) > size
        events = rows[:size]
        return {
            "events": events,
            "next": events[-1]["seq"] if events else after,
            "has_more": has_more,
        }

    def events_all(self, run_id: str, owner: str) -> list[dict]:
        self.get(run_id, owner)
        with self.db() as conn:
            rows = conn.execute(
                "SELECT seq,event_json,created_at FROM events WHERE run_id=? ORDER BY seq",
                (run_id,),
            ).fetchall()
        return [
            {"seq": row["seq"], "created_at": row["created_at"], **json.loads(row["event_json"])}
            for row in rows
        ]

    def events_tail(self, run_id: str, owner: str, limit: int = 300) -> list[dict]:
        self.get(run_id, owner)
        size = min(max(limit, 1), 2000)
        with self.db() as conn:
            rows = conn.execute(
                """SELECT seq,event_json,created_at FROM events WHERE run_id=?
                ORDER BY seq DESC LIMIT ?""",
                (run_id, size),
            ).fetchall()
        return [
            {"seq": row["seq"], "created_at": row["created_at"], **json.loads(row["event_json"])}
            for row in reversed(rows)
        ]

    def jobs(self, run_id: str) -> list[dict]:
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM job_refs WHERE run_id=?", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def input_count(self, run_id: str) -> int:
        with self.db() as conn:
            row = conn.execute(
                "SELECT agent_input_count FROM runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise AssetError("Run not found")
        return int(row["agent_input_count"] or 0)

    def bind_turn_resume(self, turn_id: str, agent_run_id: str | None) -> None:
        with self.db() as conn:
            conn.execute(
                "UPDATE turns SET resume_agent_run_id=?,updated_at=? WHERE id=?",
                (agent_run_id, time.time(), turn_id),
            )

    def cleanup_chat(self, chat_id: str) -> None:
        with self.lock, self.db() as conn:
            run_ids = [row["id"] for row in conn.execute("SELECT id FROM runs WHERE chat_id=?", (chat_id,)).fetchall()]
            for run_id in run_ids:
                conn.execute("DELETE FROM attempts WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM steps WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM inputs WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM artifact_refs WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM job_refs WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM actions WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM events WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM turns WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
        logs_root = self.logs.resolve()
        for run_id in run_ids:
            directory = (self.logs / run_id).resolve()
            if directory.parent == logs_root and directory.is_dir() and not directory.is_symlink():
                shutil.rmtree(directory)

    @staticmethod
    def _decode(row: dict) -> dict:
        return {
            "id": row["id"], "owner": row["owner"], "chat_id": row["chat_id"],
            "state": row["state"], "messages": json.loads(row["messages_json"]),
            "asset_ids": json.loads(row["asset_ids_json"]), "use_tools": bool(row["use_tools"]),
            "cancel_requested": bool(row["cancel_requested"]), "model_calls": row["model_calls"],
            "pause_requested": bool(row.get("pause_requested") or 0),
            "agent_input_count": int(row.get("agent_input_count") or 0),
            "state_version": int(row.get("state_version") or 0),
            "recovery_reason": row.get("recovery_reason"),
            "final": json.loads(row["final_json"]) if row["final_json"] else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
