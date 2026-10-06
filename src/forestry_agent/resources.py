from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
from pathlib import Path

from .contracts import Failure
from .store import Store, encode, now, uid


def inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise Failure("PERMISSION_DENIED", "路径超出已授权范围", 403)
    return target


def version(path: Path) -> str:
    info = path.stat()
    return f"stat:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}"


class Resources:
    def __init__(self, store: Store, workspace: Path):
        self.store, self.workspace = store, workspace.resolve()

    def register(self, payload: dict):
        raw = Path(payload["path"])
        if not raw.is_absolute():
            raise Failure("SCHEMA_INVALID", "登记资源须使用用户选择的绝对路径")
        path = raw.resolve(strict=True)
        internal = self.workspace / ".forestry"
        if path == Path(path.anchor) or path.is_relative_to(internal) or internal.is_relative_to(path):
            raise Failure("PERMISSION_DENIED", "不接受整个磁盘或应用内部状态目录作为数据源", 403)
        resource = {"schema_version": 1, "resource_id": uid(), "name": path.name,
            "locator": str(path), "kind": "directory" if path.is_dir() else "file",
            "classification": payload["classification"], "read_only": True,
            "version": version(path), "created_at": now()}
        with self.store.transaction() as db:
            request = {"kind": "resource.register", **payload}
            replay = self.store.replay(db, payload["request_id"], request)
            if replay is not None:
                return replay
            event_id = self.store.user_event(db, "input_root_grant", payload)
            db.execute("INSERT INTO resources VALUES(?,?,?)", (resource["resource_id"], encode(resource), event_id))
            return self.store.receipt(db, payload["request_id"], request, resource)

    def all(self):
        import json
        with self.store.transaction() as db:
            return [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM resources")]

    def resolve(self, resource_id: str, relative: str = "", task_id: str | None = None) -> Path:
        import json
        with self.store.transaction() as db:
            row = db.execute("SELECT payload FROM resources WHERE id=?", (resource_id,)).fetchone()
            if not row:
                raise Failure("PERMISSION_DENIED", "资源未授权或已撤销", 403)
            if task_id and resource_id not in self.store.read_task(db, task_id)["input_refs"]:
                raise Failure("PERMISSION_DENIED", "资源不属于当前任务的输入范围", 403)
            resource = json.loads(row["payload"])
        root = Path(resource["locator"])
        # A replaced link must not redirect a previously granted root.
        if root.resolve() != root:
            raise Failure("PERMISSION_DENIED", "授权路径已变为重定向链接，请重新登记", 403)
        if root.is_file():
            if relative not in {"", ".", root.name}:
                raise Failure("PERMISSION_DENIED", "仅授权了该文件", 403)
            return root
        return inside(root, relative)

    def task_root(self, task_id: str) -> Path:
        self.store.task(task_id)
        target = self.workspace / "tasks" / task_id / "files"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def list(self, resource_id: str, relative: str = "", offset: int = 0, limit: int = 100, task_id=None):
        path = self.resolve(resource_id, relative, task_id)
        if not path.is_dir():
            return {"entries": [{"name": path.name, "kind": "file", "size_bytes": path.stat().st_size}]}
        # Bound the scan itself; do not sort/materialize entire directories.
        entries = []
        more = False
        with os.scandir(path) as scan:
            for index, entry in enumerate(scan):
                if index < offset:
                    continue
                if len(entries) >= limit:
                    more = True
                    break
                if entry.is_symlink():
                    entries.append({"name": entry.name, "kind": "symlink", "readable": False})
                    continue
                entries.append({"name": entry.name, "kind": "directory" if entry.is_dir() else "file",
                                "size_bytes": None if entry.is_dir() else entry.stat().st_size})
        return {"resource_id": resource_id, "relative_path": relative, "entries": entries,
                "next": offset + len(entries), "has_more": more}

    def read(self, resource_id: str, relative: str = "", offset: int = 0, limit: int = 12000, task_id=None):
        path = self.resolve(resource_id, relative, task_id)
        if not path.is_file():
            raise Failure("PRECONDITION_MISSING", "目标不是可读文件", 422)
        before = version(path)
        with path.open("rb") as stream:
            stream.seek(offset)
            content = stream.read(limit + 1)
        if version(path) != before:
            raise Failure("VERSION_CONFLICT", "读取过程中源文件发生变化", 409, True)
        try:
            text = content[:limit].decode("utf-8-sig")
        except UnicodeDecodeError:
            # The last UTF-8 character can straddle a byte window; preserve the cursor.
            text = content[:limit].decode("utf-8-sig", errors="replace")
        if "\x00" in text:
            raise Failure("PRECONDITION_MISSING", "二进制文件需要对应文档/格式读取器", 422)
        return {"resource_id": resource_id, "relative_path": relative, "version": before,
                "offset": offset, "next": offset + min(len(content), limit),
                "has_more": len(content) > limit, "text": text}

    def write(self, task_id: str, action_id: str, relative: str, text: str):
        if len(text.encode()) > 2 * 1024 * 1024:
            raise Failure("RESOURCE_EXHAUSTED", "单次文本写入上限为 2 MiB", 422)
        path = inside(self.task_root(task_id), relative)
        if not relative or path == self.task_root(task_id):
            raise Failure("SCHEMA_INVALID", "需要任务工作区中的文件名")
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{action_id}.staging")
        staging.write_text(text, encoding="utf-8")
        os.replace(staging, path)
        # Each artifact is an immutable copy. Working-file edits cannot rewrite evidence.
        artifact_id = uid()
        frozen = self.workspace / "tasks" / task_id / "artifacts" / artifact_id / path.name
        frozen.parent.mkdir(parents=True, exist_ok=True)
        content = path.read_bytes()
        frozen.write_bytes(content)
        artifact = {"artifact_id": artifact_id, "schema_version": 1, "task_id": task_id,
            "relative_path": relative, "name": path.name, "locator": str(frozen),
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
            "media_type": mimetypes.guess_type(path.name)[0] or "text/plain",
            "producer_action_id": action_id, "created_at": now(), "lifecycle": "available",
            "verification": {"file_readable": True, "answers_task": "not_assessed"}}
        with self.store.transaction() as db:
            state = self.store.read_task(db, task_id)
            state["artifact_ids"].append(artifact_id)
            db.execute("INSERT INTO artifacts VALUES(?,?,?)", (artifact_id, task_id, encode(artifact)))
            self.store.save_task(db, state, state["state_version"])
            self.store.event(db, task_id, "artifact.created", {"artifact_id": artifact_id, "name": path.name})
        return {**artifact, "preview_url": f"/artifacts/{artifact_id}/content",
                "download_url": f"/artifacts/{artifact_id}/content?download=true"}

    def artifact(self, artifact_id: str, task_id: str | None = None):
        import json
        with self.store.transaction() as db:
            row = db.execute("SELECT payload FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not row:
                raise Failure("NOT_FOUND", "产物不存在", 404)
            artifact = json.loads(row["payload"])
        if task_id is not None and artifact["task_id"] != task_id:
            raise Failure("PERMISSION_DENIED", "产物不属于当前任务", 403)
        path = Path(artifact["locator"])
        if not path.resolve().is_relative_to(self.workspace / "tasks" / artifact["task_id"] / "artifacts"):
            raise Failure("PERMISSION_DENIED", "产物路径超出封存目录", 403)
        if file_hash(path) != artifact["sha256"]:
            raise Failure("VERIFICATION_FAILED", "封存产物校验和不一致", 422)
        return artifact

    def read_artifact(self, task_id: str, artifact_id: str, offset: int = 0):
        """Read a bounded window from verified immutable task output."""
        artifact = self.artifact(artifact_id, task_id)
        if offset < 0:
            raise Failure("SCHEMA_INVALID", "分页游标不能为负")
        limit = 12000
        with Path(artifact["locator"]).open("rb") as stream:
            stream.seek(offset)
            raw = stream.read(limit + 1)
        chunk = raw[:limit]
        # Do not consume half of a UTF-8 code point at a window boundary.
        try:
            text = chunk.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            if error.reason == "unexpected end of data" and len(raw) > limit:
                chunk = chunk[:error.start]
                text = chunk.decode("utf-8-sig")
            else:
                raise Failure("PRECONDITION_MISSING", "产物不是 UTF-8 文本，请使用对应格式读取器", 422) from error
        if "\x00" in text:
            raise Failure("PRECONDITION_MISSING", "二进制产物需要对应格式读取器", 422)
        return {"artifact_id": artifact_id, "sha256": artifact["sha256"], "offset": offset,
                "next": offset + len(chunk), "has_more": len(raw) > len(chunk), "text": text}

    def import_file(self, task_id: str, action_id: str, source: Path, relative: str):
        """Stream large completed outputs into immutable, idempotent evidence versions."""
        import json
        existing_id = None
        with self.store.transaction() as db:
            for row in db.execute("SELECT payload FROM artifacts WHERE task_id=?", (task_id,)):
                old = json.loads(row["payload"])
                if old["producer_action_id"] == action_id and old["relative_path"] == relative:
                    existing_id = old["artifact_id"]
                    break
        if existing_id:
            return self.artifact(existing_id)
        before = version(source)
        artifact_id = uid()
        frozen = self.workspace / "tasks" / task_id / "artifacts" / artifact_id / source.name
        frozen.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as reader, frozen.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
        if version(source) != before:
            raise Failure("VERSION_CONFLICT", "封存期间产物发生变化", 409)
        artifact = {"artifact_id": artifact_id, "schema_version": 1, "task_id": task_id,
            "relative_path": relative, "name": source.name, "locator": str(frozen),
            "sha256": file_hash(frozen), "size_bytes": frozen.stat().st_size,
            "media_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            "producer_action_id": action_id, "created_at": now(), "lifecycle": "available",
            "verification": {"file_readable": True, "answers_task": "not_assessed"}}
        with self.store.transaction() as db:
            state = self.store.read_task(db, task_id)
            state["artifact_ids"].append(artifact_id)
            db.execute("INSERT INTO artifacts VALUES(?,?,?)", (artifact_id, task_id, encode(artifact)))
            self.store.save_task(db, state, state["state_version"])
            self.store.event(db, task_id, "artifact.created", {"artifact_id": artifact_id, "name": source.name})
        return artifact


def file_hash(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
