from __future__ import annotations

import hashlib
import json

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

    def search(self, task_id, query: str, limit: int = 8):
        terms = list(dict.fromkeys(tokens(query).split()))[:40]
        if not terms or len(query) > 2000 or limit < 1 or limit > 20:
            raise Failure("SCHEMA_INVALID", "检索查询或结果数不符合限制")
        state = self.store.task(task_id)
        refs = state["input_refs"]
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

    def save(self, payload):
        with self.store.transaction() as db:
            request = {"kind": "memory.save", **payload}
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
            event_id = self.store.user_event(db, "explicit_memory_save", payload)
            note = {"memory_id": uid(), "text": payload["text"], "tags": payload["tags"],
                    "classification": "private", "created_at": now(), "origin": "explicit_user_note"}
            db.execute("INSERT INTO memories VALUES(?,?,?)", (note["memory_id"], encode(note), event_id))
            return self.store.receipt(db, payload["request_id"], request, note)

    def list(self, offset=0):
        with self.store.transaction() as db:
            notes = [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM memories ORDER BY rowid DESC LIMIT 20 OFFSET ?", (offset,))]
        return {"notes": notes, "next": offset + len(notes), "has_more": len(notes) == 20}

    def forget(self, memory_id, payload):
        with self.store.transaction() as db:
            request = {"kind": "memory.forget", "memory_id": memory_id, **payload}
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
            self.store.user_event(db, "explicit_memory_forget", request)
            db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            return self.store.receipt(db, payload["request_id"], request, {"memory_id": memory_id, "status": "forgotten"})
