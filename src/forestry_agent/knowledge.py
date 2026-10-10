from __future__ import annotations

import hashlib
import json
from datetime import datetime

from .contracts import Failure
from .documents import TEXT_READER_VERSION, Documents, tokens
from .resources import file_hash, native_path, version
from .store import Store, encode, now, uid


class Knowledge:
    def __init__(self, store: Store, documents: Documents):
        self.store, self.documents = store, documents

    def index(self, task_id, resource_id, relative_path="", page=0):
        document = self.documents.read(task_id, resource_id, relative_path, page)
        chunks = self._chunks(document)
        with self.store.transaction() as db:
            self._index_chunks(db, chunks)
            self.store.event(db, task_id, "knowledge.indexed", {"resource_id": resource_id,
                "source_version": document["citation"]["source_version"], "unit": document["citation"]["unit"], "chunks": len(chunks)})
        return {"chunk_ids": [c[0] for c in chunks], "citation": document["citation"],
                "next_page": document["next_page"], "has_more": document["has_more"]}

    @staticmethod
    def _chunks(document):
        citation = document["citation"]
        resource_id, relative_path = citation["resource_id"], citation["relative_path"]
        text = document["text"]
        if not text.strip():
            raise Failure("PRECONDITION_MISSING", "文档没有可索引内容", 422)
        chunks = []
        for start in range(0, len(text), 1360):
            content = text[start:start + 1600]
            pointer = {**citation, "text_start": start, "text_end": start + len(content)}
            chunk_id = hashlib.sha256(encode(pointer).encode()).hexdigest()
            chunks.append((chunk_id, resource_id, relative_path, citation["source_version"], encode(pointer), content))
        return chunks

    @staticmethod
    def _index_chunks(db, chunks):
        for chunk in chunks:
            if db.execute("INSERT OR IGNORE INTO knowledge_chunks VALUES(?,?,?,?,?,?)", chunk).rowcount:
                db.execute("INSERT INTO knowledge_fts VALUES(?,?)", (chunk[0], tokens(chunk[-1])))

    def _snapshot_path(self, sha256):
        root = self.documents.resources.workspace / ".forestry" / "knowledge-snapshots"
        root.mkdir(parents=True, exist_ok=True)
        return native_path(root / (sha256 + ".bin"))

    def _snapshot(self, source, citation):
        target = self._snapshot_path(citation["source_sha256"])
        if target.exists():
            if file_hash(target) != citation["source_sha256"]:
                raise Failure("VERSION_CONFLICT", "知识来源快照校验失败", 409)
            return
        temporary = target.with_name(uid() + ".tmp")
        try:
            copied, digest = 0, hashlib.sha256()
            with native_path(source).open("rb") as stream, temporary.open("xb") as output:
                while data := stream.read(1024 * 1024):
                    copied += len(data)
                    if copied > 128 * 1024 * 1024:
                        raise Failure("RESOURCE_EXHAUSTED", "知识快照超过128 MiB导入上限", 422)
                    digest.update(data)
                    output.write(data)
            if digest.hexdigest() != citation["source_sha256"] or version(source) != citation["source_version"]:
                raise Failure("VERSION_CONFLICT", "导入期间来源已改变，请重新导入", 409)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def import_source(self, payload):
        request = {"kind": "knowledge.import", **payload}
        with self.store.transaction() as db:
            if not db.execute("SELECT 1 FROM resources WHERE id=?", (payload["resource_id"],)).fetchone():
                raise Failure("PERMISSION_DENIED", "导入来源未授权或已撤销", 403)
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
        source = self.documents.resources.resolve(payload["resource_id"], payload["relative_path"])
        if source.stat().st_size > 128 * 1024 * 1024:
            raise Failure("RESOURCE_EXHAUSTED", "知识快照超过128 MiB导入上限", 422)
        document = self.documents.read(None, payload["resource_id"], payload["relative_path"], payload["page"])
        citation = document["citation"]
        self._snapshot(source, citation)
        chunks = self._chunks(document)
        metadata = {key: value for key, value in payload.items() if key not in {"request_id", "page", "resource_id", "relative_path"}}
        fingerprint = hashlib.sha256(encode({"citation": citation, **metadata}).encode()).hexdigest()
        with self.store.transaction() as db:
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
            row = db.execute("SELECT payload FROM resources WHERE id=?", (payload["resource_id"],)).fetchone()
            if not row:
                raise Failure("PERMISSION_DENIED", "导入来源授权已撤销", 403)
            existing = db.execute("SELECT payload FROM knowledge_imports WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                return self.store.receipt(db, payload["request_id"], request, json.loads(existing["payload"]))
            event_id = self.store.user_event(db, "explicit_knowledge_import", request)
            record = {"schema_version": 1, "import_id": uid(), "citation": citation, **metadata,
                      "classification": json.loads(row["payload"])["classification"], "created_at": now(),
                      "user_event_id": event_id, "chunk_ids": [chunk[0] for chunk in chunks],
                      "next_page": document["next_page"], "has_more": document["has_more"], "document": document}
            for old in db.execute("SELECT id,payload FROM knowledge_imports WHERE status='active'").fetchall():
                pointer = json.loads(old["payload"])["citation"]
                if (pointer["resource_id"], pointer["relative_path"]) == (citation["resource_id"], citation["relative_path"]) and (pointer["source_version"] != citation["source_version"] or pointer.get("reader_version") != citation.get("reader_version")):
                    db.execute("UPDATE knowledge_imports SET status='superseded' WHERE id=?", (old["id"],))
            db.execute("INSERT INTO knowledge_imports VALUES(?,?,?,?,?)", (record["import_id"], fingerprint, "active", encode(record), event_id))
            self._index_chunks(db, chunks)
            for chunk in chunks:
                db.execute("INSERT INTO knowledge_import_chunks VALUES(?,?)", (record["import_id"], chunk[0]))
            return self.store.receipt(db, payload["request_id"], request, record)

    def _source_status(self, record, cache=None):
        pointer = record["citation"]
        if pointer["unit"].startswith("bytes:") and pointer.get("reader_version") != TEXT_READER_VERSION:
            return "reader_outdated"
        key = (pointer["resource_id"], pointer["relative_path"], pointer["source_version"], pointer["source_sha256"])
        if cache is not None and key in cache:
            return cache[key]
        try:
            source = self.documents.resources.resolve(pointer["resource_id"], pointer["relative_path"])
            if version(source) != pointer["source_version"] or file_hash(source) != pointer["source_sha256"]:
                return "stale"
            if file_hash(self._snapshot_path(pointer["source_sha256"])) != pointer["source_sha256"]:
                return "snapshot_invalid"
        except (Failure, OSError):
            return "unavailable"
        if cache is not None:
            cache[key] = "current"
        return "current"

    def imports(self, offset=0):
        with self.store.transaction() as db:
            rows = db.execute("SELECT json_remove(payload,'$.document') AS payload,status FROM knowledge_imports ORDER BY rowid DESC LIMIT 21 OFFSET ?", (offset,)).fetchall()
        cache = {}
        records = [{key: value for key, value in json.loads(row["payload"]).items() if key != "document"} for row in rows[:20]]
        for record, row in zip(records, rows[:20], strict=True):
            record["status"] = row["status"]
            record["source_status"] = self._source_status(record, cache)
        return {"imports": records, "next": offset + len(records), "has_more": len(rows) > 20}

    def import_content(self, import_id):
        with self.store.transaction() as db:
            row = db.execute("SELECT payload,status FROM knowledge_imports WHERE id=?", (import_id,)).fetchone()
            if not row:
                raise Failure("NOT_FOUND", "导入记录不存在", 404)
            record = json.loads(row["payload"])
            if not db.execute("SELECT 1 FROM resources WHERE id=?", (record["citation"]["resource_id"],)).fetchone():
                raise Failure("PERMISSION_DENIED", "知识来源授权已撤销", 403)
        pointer = record["citation"]
        try:
            snapshot_valid = file_hash(self._snapshot_path(pointer["source_sha256"])) == pointer["source_sha256"]
        except OSError:
            snapshot_valid = False
        if not snapshot_valid:
            raise Failure("VERSION_CONFLICT", "知识来源快照校验失败", 409)
        return {**record, "status": row["status"], "source_status": self._source_status(record)}

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
                (expression, *refs, 501)).fetchall()
            imports = db.execute("SELECT json_remove(i.payload,'$.document') AS payload,i.status,c.chunk_id FROM knowledge_imports i JOIN knowledge_import_chunks c ON c.import_id=i.id WHERE c.chunk_id IN (" + ",".join("?" for _ in matches) + ")", [row["id"] for row in matches]).fetchall() if matches else []
            resource_rows = db.execute("SELECT id,payload FROM resources WHERE id IN (" + ",".join("?" for _ in refs) + ")", refs).fetchall()
        classifications = {row["id"]: json.loads(row["payload"])["classification"] for row in resource_rows}
        public = state["config_snapshot"].get("context_classification") == "public"
        import_records, source_notices, by_chunk, status_cache = {}, [], {}, {}
        for row in imports:
            record = json.loads(row["payload"])
            if public and record["classification"] != "public":
                continue
            if record["import_id"] not in import_records:
                status = self._source_status(record, status_cache)
                import_records[record["import_id"]] = (record, row["status"], status)
                if row["status"] != "active" or status != "current":
                    source_notices.append({"import_id": record["import_id"], "citation": record["citation"],
                                           "status": row["status"], "source_status": status})
            by_chunk.setdefault(row["chunk_id"], set()).add(record["import_id"])
        results = []
        hashes = {}
        seen_sources = set()
        extras = []
        legacy_text_chunks_filtered = 0
        for row in matches[:500]:
            if row["resource_id"] not in classifications or public and classifications[row["resource_id"]] != "public":
                continue
            try:
                source = self.documents.resources.resolve(row["resource_id"], row["relative_path"], task_id)
                if version(source) != row["source_version"]:
                    continue
                pointer = json.loads(row["citation"])
                if pointer["unit"].startswith("bytes:") and pointer.get("reader_version") != TEXT_READER_VERSION:
                    legacy_text_chunks_filtered += 1
                    continue
                if source not in hashes:
                    hashes[source] = file_hash(source)
                if hashes[source] != pointer["source_sha256"]:
                    continue
            except (Failure, OSError):
                continue
            records = [import_records[identifier][0] for identifier in by_chunk.get(row["id"], ())
                       if import_records[identifier][1:] == ("active", "current")]
            # Imported chunks with invalid snapshots cannot fall back to unverified task cache.
            if by_chunk.get(row["id"]) and not records:
                continue
            match = {"chunk_id": row["id"], "text": row["content"], "citation": pointer, "score": row["score"],
                     "classification": classifications[row["resource_id"]],
                     "imports": [{key: record[key] for key in ("import_id", "claim_kind", "source_label", "conditions", "limitations", "user_event_id")} for record in sorted(records, key=lambda record: record["import_id"])],
                     "origin": "explicit_user_import" if records else "task_input_cache"}
            source_key = (row["resource_id"], row["relative_path"], pointer["source_sha256"])
            if source_key not in seen_sources:
                results.append(match)
                seen_sources.add(source_key)
            else:
                extras.append(match)
        return {"matches": (results + extras)[:limit], "scope": "task_inputs_only", "stale_sources_filtered": True,
                "source_notices": source_notices, "candidate_limit_reached": len(matches) > 500,
                "legacy_text_chunks_filtered": legacy_text_chunks_filtered,
                "conflict_resolution": "not_performed",
                "notice": "保留来源与条件；检索不裁定意见冲突，也不把用户分类的专家断言当成测量验证。"}


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
