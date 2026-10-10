from __future__ import annotations

import hashlib
import json
from datetime import datetime

from .contracts import Failure
from .documents import Documents, tokens
from .resources import file_hash, version
from .store import Store, encode, now, uid


class Knowledge:
    def __init__(self, store: Store, documents: Documents):
        self.store, self.documents = store, documents

    def index(self, task_id, resource_id, relative_path="", page=0):
        document = self.documents.read(task_id, resource_id, relative_path, page)
        citation = document["citation"]
        text = document["text"]
        if not text.strip():
            raise Failure("PRECONDITION_MISSING", "文档没有可索引内容", 422)
        chunks = []
        for start in range(0, len(text), 1360):
            content = text[start:start + 1600]
            pointer = {**citation, "text_start": start, "text_end": start + len(content)}
            chunk_id = hashlib.sha256(encode(pointer).encode()).hexdigest()
            chunks.append((chunk_id, resource_id, relative_path, citation["source_version"], encode(pointer), content))
        with self.store.transaction() as db:
            for chunk in chunks:
                if db.execute("INSERT OR IGNORE INTO knowledge_chunks VALUES(?,?,?,?,?,?)", chunk).rowcount:
                    db.execute("INSERT INTO knowledge_fts VALUES(?,?)", (chunk[0], tokens(chunk[-1])))
            self.store.event(db, task_id, "knowledge.indexed", {"resource_id": resource_id,
                "source_version": citation["source_version"], "unit": citation["unit"], "chunks": len(chunks)})
        return {"chunk_ids": [c[0] for c in chunks], "citation": citation,
                "next_page": document["next_page"], "has_more": document["has_more"]}

    def search(self, task_id, query: str, limit: int = 8, allowed_refs=None):
        terms = list(dict.fromkeys(tokens(query).split()))[:40]
        if not terms or len(query) > 2000 or limit < 1 or limit > 20:
            raise Failure("SCHEMA_INVALID", "检索查询或结果数不符合限制")
        state = self.store.task(task_id)
        refs = [ref for ref in state["input_refs"] if allowed_refs is None or ref in allowed_refs]
        if not refs:
            return {"matches": [], "scope": "task_inputs_only"}
        expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        with self.store.transaction() as db:
            matches = db.execute("SELECT k.*,bm25(knowledge_fts) AS score FROM knowledge_fts JOIN knowledge_chunks k ON k.id=knowledge_fts.chunk_id WHERE knowledge_fts MATCH ? AND k.resource_id IN (" + ",".join("?" for _ in refs) + ") ORDER BY score LIMIT ?",
                (expression, *refs, limit * 3)).fetchall()
        results = []
        hashes = {}
        for row in matches:
            try:
                source = self.documents.resources.resolve(row["resource_id"], row["relative_path"], task_id)
                if version(source) != row["source_version"]:
                    continue
                pointer = json.loads(row["citation"])
                if source not in hashes:
                    hashes[source] = file_hash(source)
                if hashes[source] != pointer["source_sha256"]:
                    continue
            except (Failure, OSError):
                continue
            results.append({"chunk_id": row["id"], "text": row["content"],
                            "citation": json.loads(row["citation"]), "score": row["score"]})
            if len(results) == limit:
                break
        return {"matches": results, "scope": "task_inputs_only", "stale_sources_filtered": True}


class Memory:
    """Writes exist only at the authenticated user boundary, never as model tools."""

    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _note(value):
        return {"schema_version": 1, "version": 1, "active": True, "scope_task_ids": [], "source_note": "",
                "conditions": "", "limitations": "", "expires_at": None,
                "source_authority": "user_statement", **value}

    @staticmethod
    def _expired(note):
        return bool(note["expires_at"] and datetime.fromisoformat(note["expires_at"]) <= datetime.fromisoformat(now()))

    def _validate_scope(self, db, payload):
        for task_id in payload.get("scope_task_ids", []):
            self.store.read_task(db, task_id)

    def _write(self, db, note, event_id):
        db.execute("INSERT INTO memories VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,event_id=excluded.event_id",
                   (note["memory_id"], encode(note), event_id))
        db.execute("INSERT INTO memory_versions VALUES(?,?,?,?)",
                   (note["memory_id"], note["version"], encode(note), event_id))

    def _current(self, db, memory_id):
        row = db.execute("SELECT payload FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            raise Failure("NOT_FOUND", "记忆不存在", 404)
        return self._note(json.loads(row["payload"]))

    @staticmethod
    def _check_version(note, payload):
        if note["version"] != payload["expected_version"]:
            raise Failure("VERSION_CONFLICT", "记忆已更新，请重新读取后更正或停用", 409)

    def save(self, payload):
        with self.store.transaction() as db:
            request = {"kind": "memory.save", **payload}
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
            self._validate_scope(db, payload)
            event_id = self.store.user_event(db, "explicit_memory_save", payload)
            note = self._note({key: value for key, value in payload.items() if key != "request_id"})
            note.update(memory_id=uid(), classification="private", created_at=now(), origin="explicit_user_note")
            self._write(db, note, event_id)
            return self.store.receipt(db, payload["request_id"], request, note)

    def list(self, offset=0, *, include_inactive=False, task_id=None):
        with self.store.transaction() as db:
            if task_id:
                state = self.store.read_task(db, task_id)
                if state["config_snapshot"].get("context_classification") == "public":
                    raise Failure("PERMISSION_DENIED", "公开任务不能读取私密长期记忆", 403)
            notes, skipped = [], 0
            for row in db.execute("SELECT payload FROM memories ORDER BY rowid DESC"):
                note = self._note(json.loads(row["payload"]))
                if (task_id or not include_inactive) and (not note["active"] or self._expired(note)):
                    continue
                if task_id and note["scope_task_ids"] and task_id not in note["scope_task_ids"]:
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                notes.append(note)
                if len(notes) == 21:
                    break
        return {"notes": notes[:20], "next": offset + min(len(notes), 20), "has_more": len(notes) > 20}

    def update(self, memory_id, payload):
        with self.store.transaction() as db:
            request = {"kind": "memory.update", "memory_id": memory_id, **payload}
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
            note = self._current(db, memory_id)
            self._check_version(note, payload)
            if not note["active"]:
                raise Failure("PRECONDITION_MISSING", "记忆已停用；如需重新使用，请明确保存新记忆", 409)
            self._validate_scope(db, payload)
            event_id = self.store.user_event(db, "explicit_memory_update", request)
            note.update({key: value for key, value in payload.items() if key not in {"request_id", "expected_version"}})
            note.update(version=note["version"] + 1, updated_at=now())
            self._write(db, note, event_id)
            return self.store.receipt(db, payload["request_id"], request, note)

    def history(self, memory_id, offset=0):
        with self.store.transaction() as db:
            self._current(db, memory_id)
            rows = db.execute("SELECT * FROM memory_versions WHERE memory_id=? ORDER BY version DESC LIMIT 21 OFFSET ?",
                              (memory_id, offset)).fetchall()
            versions = [{"note": self._note(json.loads(row["payload"])), "user_event_id": row["event_id"]}
                        for row in rows[:20]]
        return {"versions": versions, "next": offset + len(versions), "has_more": len(rows) > 20}

    def forget(self, memory_id, payload):
        with self.store.transaction() as db:
            request = {"kind": "memory.forget", "memory_id": memory_id, **payload}
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
            note = self._current(db, memory_id)
            self._check_version(note, payload)
            if note["active"]:
                event_id = self.store.user_event(db, "explicit_memory_forget", request)
                note.update(active=False, version=note["version"] + 1, updated_at=now())
                self._write(db, note, event_id)
            return self.store.receipt(db, payload["request_id"], request,
                                      {"memory_id": memory_id, "status": "forgotten", "version": note["version"]})
