from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .contracts import CAPABILITY_NAMES, Failure, TaskConstraints


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(UTC).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    """Short SQLite transactions own application facts; external work stays outside."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, digest TEXT NOT NULL, response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS user_events(id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, version INTEGER NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS specs(task_id TEXT NOT NULL REFERENCES tasks(id), revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,revision));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id,seq);
            CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, payload TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES user_events(id));
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), turn_id TEXT NOT NULL, call_id TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL, args TEXT NOT NULL, status TEXT NOT NULL, result TEXT, UNIQUE(task_id,turn_id,call_id));
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),revision INTEGER NOT NULL,prompt TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turn_requests(request_id TEXT PRIMARY KEY,turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id),user_event_id TEXT NOT NULL REFERENCES user_events(id));
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,action_id TEXT NOT NULL REFERENCES actions(id),effect_state TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),action_id TEXT NOT NULL UNIQUE REFERENCES actions(id),revision INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),revision INTEGER NOT NULL,request_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,status TEXT NOT NULL,UNIQUE(task_id,revision));
            CREATE TABLE IF NOT EXISTS knowledge_chunks(id TEXT PRIMARY KEY,resource_id TEXT NOT NULL,relative_path TEXT NOT NULL,source_version TEXT NOT NULL,citation TEXT NOT NULL,content TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(chunk_id UNINDEXED,terms);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,payload TEXT NOT NULL,event_id TEXT NOT NULL REFERENCES user_events(id));
            CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),turn_id TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assignments(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),parent_run_id TEXT NOT NULL,role_id TEXT NOT NULL,action_id TEXT NOT NULL UNIQUE REFERENCES actions(id),status TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY,turn_id TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),revision INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,user_event_id TEXT REFERENCES user_events(id));
            CREATE TABLE IF NOT EXISTS progress_observations(task_id TEXT NOT NULL REFERENCES tasks(id),revision INTEGER NOT NULL,scope TEXT NOT NULL,turn_id TEXT NOT NULL,call_id TEXT NOT NULL,request_id TEXT NOT NULL,tool TEXT NOT NULL,decision_sha256 TEXT NOT NULL,fingerprint TEXT,stalled INTEGER NOT NULL,limit_value INTEGER NOT NULL,at TEXT NOT NULL,PRIMARY KEY(task_id,revision,scope,turn_id,call_id,request_id));
            CREATE INDEX IF NOT EXISTS progress_seen ON progress_observations(task_id,revision,scope,turn_id,fingerprint);
            """)
            db.execute("INSERT OR IGNORE INTO schema_versions VALUES(1,?)", (now(),))
            db.execute("INSERT OR IGNORE INTO schema_versions VALUES(2,?)", (now(),))

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def replay(self, db, request_id: str, payload: dict):
        digest = hashlib.sha256(encode(payload).encode()).hexdigest()
        row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row:
            if row["digest"] != digest:
                raise Failure("VERSION_CONFLICT", "相同 request_id 不能用于不同请求", 409)
            return json.loads(row["response"])
        return None

    def receipt(self, db, request_id: str, payload: dict, response):
        db.execute("INSERT INTO requests VALUES(?,?,?)", (
            request_id, hashlib.sha256(encode(payload).encode()).hexdigest(), encode(response)))
        return response

    def user_event(self, db, kind: str, payload: dict) -> str:
        event_id = uid()
        db.execute("INSERT INTO user_events VALUES(?,?,?,?)", (event_id, kind, encode(payload), now()))
        return event_id

    def event(self, db, task_id: str, kind: str, payload: dict):
        event_id = uid()
        db.execute("INSERT INTO events(id,task_id,kind,payload,at) VALUES(?,?,?,?,?)",
                   (event_id, task_id, kind, encode(payload), now()))

    def read_task(self, db, task_id: str) -> dict:
        row = db.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise Failure("NOT_FOUND", "任务不存在", 404)
        return json.loads(row["state"])

    def task(self, task_id: str) -> dict:
        with self.transaction() as db:
            return self.read_task(db, task_id)

    def constraints(self, db, state):
        row = db.execute("SELECT payload FROM specs WHERE task_id=? AND revision=?",
                         (state["task_id"], state["spec_revision"])).fetchone()
        if row is None:
            raise Failure("PRECONDITION_MISSING", "当前任务规范缺失，不能推断工具范围", 422)
        return TaskConstraints.model_validate(json.loads(row["payload"]).get("constraints", {}))

    def capabilities(self, db, state):
        allowed = self.constraints(db, state).allowed_capabilities
        return set(CAPABILITY_NAMES.values() if allowed is None else allowed)

    def capability_allowed(self, db, state, kind):
        return kind in self.capabilities(db, state)

    def authorize_capability(self, db, state, kind):
        if not self.capability_allowed(db, state, kind):
            raise Failure("PERMISSION_DENIED", "能力超出当前任务规范的允许范围上限；须由用户修订规范", 403)

    def describe_task(self, task_id):
        with self.transaction() as db:
            state = self.read_task(db, task_id)
            return {**state, "constraints": self.constraints(db, state).model_dump()}

    def tasks(self):
        with self.transaction() as db:
            states = [json.loads(row["state"]) for row in db.execute(
                "SELECT state FROM tasks ORDER BY updated_at DESC LIMIT 200")]
            return [{**state, "constraints": self.constraints(db, state).model_dump()} for state in states]

    def save_task(self, db, state: dict, expected: int):
        state["state_version"] = expected + 1
        state["updated_at"] = now()
        result = db.execute("UPDATE tasks SET version=?,revision=?,status=?,state=?,updated_at=? WHERE id=? AND version=?",
            (state["state_version"], state["spec_revision"], state["status"], encode(state), now(), state["task_id"], expected))
        if result.rowcount != 1:
            raise Failure("VERSION_CONFLICT", "状态版本已变化，请刷新", 409)

    def create_task(self, payload: dict, snapshot: dict):
        with self.transaction() as db:
            replay = self.replay(db, payload["request_id"], {"kind": "task.create", **payload})
            if replay is not None:
                return replay
            for resource_id in payload["input_refs"]:
                resource = db.execute("SELECT payload FROM resources WHERE id=?", (resource_id,)).fetchone()
                if not resource:
                    raise Failure("PERMISSION_DENIED", "输入尚未由用户登记", 403)
                if payload.get("context_classification") == "public" and json.loads(resource["payload"])["classification"] != "public":
                    raise Failure("PERMISSION_DENIED", "公开任务不能包含私密资源；请选择私密任务", 403)
            event_id = self.user_event(db, "task_create", payload)
            state = {"schema_version": 1, "task_id": uid(), "state_version": 1,
                "spec_revision": 1, "status": "created", "objective": payload["objective"],
                "input_refs": payload["input_refs"], "requested_method_id": payload.get("requested_method_id"),
                "model_profile": snapshot["model_profile"], "config_snapshot": snapshot,
                "created_at": now(), "updated_at": now(), "user_event_id": event_id,
                "messages": [], "artifact_ids": [], "blockers": [], "cancellation_requested": False,
                "pause_requested": False, "action_count": 0, "model_requests": 0, "acceptance": None}
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)",
                (state["task_id"], 1, 1, "created", encode(state), now()))
            db.execute("INSERT INTO specs VALUES(?,?,?)", (state["task_id"], 1, encode(payload)))
            self.event(db, state["task_id"], "task.created", {"objective": state["objective"]})
            return self.receipt(db, payload["request_id"], {"kind": "task.create", **payload}, state)

    def change(self, task_id: str, kind: str, mutate, payload: dict | None = None, expected: int | None = None):
        with self.transaction() as db:
            request_payload = {"task_id": task_id, "kind": kind, **(payload or {})}
            if payload and "request_id" in payload:
                replay = self.replay(db, payload["request_id"], request_payload)
                if replay is not None:
                    return replay
            state = self.read_task(db, task_id)
            version = state["state_version"]
            if expected is not None and expected != version:
                raise Failure("VERSION_CONFLICT", "状态版本已变化，请刷新", 409)
            if state["status"] in {"completed", "failed", "cancelled", "publishing"}:
                raise Failure("VERSION_CONFLICT", "终态或正在封存的任务不能修改", 409)
            mutate(state, db)
            self.save_task(db, state, version)
            self.event(db, task_id, kind, payload or {"status": state["status"]})
            if payload and "request_id" in payload:
                return self.receipt(db, payload["request_id"], request_payload, state)
            return state

    @staticmethod
    def enqueue_turn(db, state, prompt, request_id, user_event_id):
        """Queue human input atomically with its event and idempotency receipt."""
        turn_id = uid()
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)",
            (turn_id, state["task_id"], state["spec_revision"], prompt, "queued", now()))
        db.execute("INSERT INTO turn_requests VALUES(?,?,?)", (request_id, turn_id, user_event_id))
        return turn_id

    def events(self, task_id: str, after: int = 0, limit: int = 100):
        with self.transaction() as db:
            self.read_task(db, task_id)
            return [{"sequence": r["seq"], "event_id": r["id"], "type": r["kind"],
                     "payload": json.loads(r["payload"]), "at": r["at"]} for r in db.execute(
                "SELECT * FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?", (task_id, after, limit))]

    def settings(self, initial: dict):
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO settings VALUES(1,1,?)", (encode(initial),))
            row = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
            return {"version": row["version"], **json.loads(row["payload"])}
