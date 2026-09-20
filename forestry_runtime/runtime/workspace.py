"""Managed chat workspaces and explicit host-directory grants."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
import unicodedata
import uuid

import httpx

from .storage import AssetError


WINDOWS_PATH = re.compile(r"(?<![\w])([A-Za-z]:[\\/][^\r\n\"'<>|?*]+)")
WINDOWS_SYSTEM_DIRECTORIES = {
    "windows", "program files", "program files (x86)", "programdata",
    "system volume information", "$recycle.bin", "recovery",
}
POSIX_SYSTEM_DIRECTORIES = {
    "bin", "boot", "dev", "etc", "proc", "root", "run", "sbin",
    "sys", "usr", "var",
}


def _directory_name_key(value: str) -> str:
    """Normalize a user-spelled directory name without enabling fuzzy guessing."""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(character for character in normalized if not character.isspace())


def _host_path_object(value: str):
    return PureWindowsPath(value) if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", str(value)) else PurePosixPath(value)


def is_host_path(value: str) -> bool:
    return _host_path_object(value).is_absolute()


def _normalized_host_path(value: str) -> str:
    return str(value).strip().strip('"\'').replace("/", "\\").rstrip("\\").casefold()


def _explicit_path_mention(requested_path: str, latest_user: str) -> bool:
    """Require the complete requested path, not a prefix of another path."""
    requested = _normalized_host_path(requested_path)
    text = str(latest_user or "").replace("/", "\\").casefold()
    start = 0
    while True:
        index = text.find(requested, start)
        if index < 0:
            return False
        before = text[index - 1] if index else ""
        end = index + len(requested)
        after = text[end] if end < len(text) else ""
        before_ok = not before or not (before.isalnum() or before in "_\\/.-")
        after_ok = not after or not (after.isalnum() or after in "_\\/.-")
        if before_ok and after_ok:
            return True
        start = index + 1


def _system_path_blocked(value: str) -> bool:
    path = _host_path_object(value)
    parts = [str(part).strip("\\/").casefold() for part in path.parts]
    if isinstance(path, PureWindowsPath):
        # Drive roots, administrative shares, and operating-system roots are
        # never inferred from chat text. Narrower user data paths remain valid.
        if len(parts) <= 1 or str(path.drive).rstrip("\\/").endswith("$"):
            return True
        return len(parts) >= 2 and parts[1] in WINDOWS_SYSTEM_DIRECTORIES
    return len(parts) <= 1 or (len(parts) >= 2 and parts[1] in POSIX_SYSTEM_DIRECTORIES)


def _relative(value: str) -> Path:
    raw = str(value or ".").replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        raise AssetError("Path must stay inside the selected workspace or source grant")
    return Path(*[part for part in pure.parts if part not in ("", ".")])


class BridgeClient:
    def __init__(self):
        self.url = os.getenv("HOST_BRIDGE_URL", "http://host.docker.internal:8011").rstrip("/")
        self.key = os.getenv("HOST_BRIDGE_KEY", "")

    @property
    def available(self) -> bool:
        return len(self.key) >= 24

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.available:
            raise AssetError("Host bridge is not configured; run setup.ps1")
        try:
            response = httpx.request(
                method,
                self.url + path,
                headers={"Authorization": "Bearer " + self.key},
                json=payload,
                timeout=35,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("error") or exc.response.text
            except Exception:
                detail = exc.response.text
            raise AssetError(f"Host bridge rejected the request: {detail}") from exc
        except httpx.HTTPError as exc:
            raise AssetError("Host bridge is unavailable") from exc

    def resolve(self, path: str) -> dict:
        return self._request("POST", "/resolve", {"path": path})

    def fs(self, operation: str, payload: dict) -> dict:
        return self._request("POST", "/fs/" + operation, payload)

    def job(self, operation: str, payload: dict) -> dict:
        return self._request("POST", "/jobs/" + operation, payload)


class SourcePathError(AssetError):
    def __init__(self, message: str, *, source_id: str, requested_path: str, suggested_path: str):
        super().__init__(message)
        self.failure_details = {
            "code": "source_path_not_found",
            "operation_started": False,
            "side_effects": "none",
            "source_id": source_id,
            "requested_path": requested_path,
            "suggested_path": suggested_path,
            "suggested_arguments": {
                "source_id": source_id,
                "folder_path": suggested_path,
            },
        }


class WorkspacePathError(AssetError):
    def __init__(self, requested_path: str, suggested_path: str):
        super().__init__(
            "Workspace paths are already relative to the workspace root; "
            f"do not prefix them with 'workspace/'. Retry with path='{suggested_path}'."
        )
        self.failure_details = {
            "code": "workspace_path_has_root_prefix",
            "operation_started": False,
            "side_effects": "none",
            "requested_path": requested_path,
            "suggested_path": suggested_path,
            "suggested_arguments": {"path": suggested_path},
        }


class WorkspaceRegistry:
    def __init__(self, data_root: str | Path):
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "workspaces.sqlite3"
        self.bridge = BridgeClient()
        with self.db() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS grants (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT NOT NULL,
                host_path TEXT NOT NULL, access TEXT NOT NULL, basis TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner, chat_id, host_path, access))"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS grant_chat ON grants(owner, chat_id, active)")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def workspace(store) -> Path:
        path = (store.root / "workspace").resolve()
        path.mkdir(parents=True, exist_ok=True)
        (path / ".runtime" / "actions").mkdir(parents=True, exist_ok=True)
        (path / ".runtime" / "deps").mkdir(parents=True, exist_ok=True)
        return path

    def workspace_path(self, store, relative: str = ".", *, require_exists: bool = False) -> Path:
        root = self.workspace(store)
        raw = str(relative or ".").replace("\\", "/")
        parts = PurePosixPath(raw).parts
        if parts and parts[0].casefold() == "workspace":
            suggested = PurePosixPath(*parts[1:]).as_posix() if len(parts) > 1 else "."
            raise WorkspacePathError(str(relative), suggested)
        target = (root / _relative(relative)).resolve()
        if target != root and root not in target.parents:
            raise AssetError("Path leaves the managed workspace")
        if require_exists and not target.exists():
            raise AssetError("Workspace path does not exist")
        return target

    def host_workspace(self, store):
        configured = os.getenv("RUNTIME_DATA_HOST_ROOT", "")
        if not configured:
            raise AssetError("RUNTIME_DATA_HOST_ROOT is not configured")
        relative = self.workspace(store).relative_to(self.root)
        if re.match(r"^[A-Za-z]:[\\/]", configured):
            return PureWindowsPath(configured, *relative.parts)
        return (Path(configured).resolve() / relative).resolve()

    def list_grants(self, owner: str, chat_id: str, *, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM grants WHERE owner=? AND chat_id=?"
        params: tuple = (owner, chat_id)
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY created_at"
        with self.db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._public(dict(row)) for row in rows]

    def get_grant(self, owner: str, chat_id: str, grant_id: str) -> dict:
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM grants WHERE id=? AND owner=? AND chat_id=? AND active=1",
                (grant_id, owner, chat_id),
            ).fetchone()
        if row is None:
            raise AssetError("Source grant does not exist or has been revoked")
        return self._public(dict(row))

    def covering_read_grant(
        self, owner: str, chat_id: str, host_path: str
    ) -> tuple[dict, str] | None:
        """Return an existing read grant that contains the requested directory."""
        resolved = self.bridge.resolve(host_path)
        if resolved.get("type") != "directory":
            return None
        requested = _host_path_object(resolved["path"])
        matches: list[tuple[int, dict, str]] = []
        for grant in self.list_grants(owner, chat_id):
            if grant["access"] != "read":
                continue
            root = _host_path_object(grant["host_path"])
            try:
                relative = requested.relative_to(root)
            except ValueError:
                continue
            matches.append((
                len(root.parts), grant,
                PurePosixPath(*relative.parts).as_posix() if relative.parts else ".",
            ))
        if not matches:
            return None
        _, grant, relative = max(matches, key=lambda item: item[0])
        return grant, relative

    def resolve_grant_directory(self, grant: dict, requested_path: str = ".") -> str:
        """Resolve a directory inside one specific read grant without widening it."""
        if grant["access"] != "read":
            raise AssetError("A readable directory grant is required")
        root = _host_path_object(grant["host_path"])
        value = str(requested_path or ".").strip()
        if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", value):
            candidate = PureWindowsPath(value)
        else:
            relative = PurePosixPath(value.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise AssetError("Source path leaves the authorized directory")
            candidate = root.joinpath(*[
                part for part in relative.parts if part not in ("", ".")
            ])
        try:
            resolved = self.bridge.resolve(str(candidate))
        except AssetError:
            suggestion = self.suggest_grant_directory(grant, value)
            if suggestion:
                raise SourcePathError(
                    f"Source directory was not found. Use the observed exact path '{suggestion}'.",
                    source_id=grant["id"], requested_path=value,
                    suggested_path=suggestion,
                )
            raise
        if resolved.get("type") != "directory":
            suggestion = self.suggest_grant_directory(grant, value)
            if suggestion:
                raise SourcePathError(
                    f"Source directory was not found. Use the observed exact path '{suggestion}'.",
                    source_id=grant["id"], requested_path=value,
                    suggested_path=suggestion,
                )
            raise AssetError("UAV source must be a directory")
        canonical = _host_path_object(resolved["path"])
        try:
            canonical.relative_to(root)
        except ValueError as exc:
            raise AssetError("Source path leaves the authorized directory") from exc
        return resolved["path"]

    def suggest_grant_directory(self, grant: dict, requested_path: str) -> str | None:
        """Return one observed whitespace-equivalent directory path without using it."""
        value = str(requested_path or ".").strip()
        if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", value):
            return None
        relative = PurePosixPath(value.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            return None
        current = "."
        changed = False
        for requested_part in relative.parts:
            response = self.bridge.fs(
                "list", self.bridge_payload(
                    grant, path=current, page=1, page_size=200
                )
            )
            directories = [
                item for item in response.get("items", [])
                if item.get("type") == "directory"
            ]
            exact = [
                item for item in directories
                if str(item.get("name", "")).casefold() == requested_part.casefold()
            ]
            candidates = exact or [
                item for item in directories
                if _directory_name_key(item.get("name", ""))
                == _directory_name_key(requested_part)
            ]
            if len(candidates) != 1:
                return None
            selected = candidates[0]
            changed = changed or selected.get("name") != requested_part
            current = str(selected["path"])
        return current if changed else None

    def match_granted_directory(
        self, owner: str, chat_id: str, requested_path: str
    ) -> tuple[str, dict, str] | None:
        """Find one exact or suggested relative directory under current grants."""
        if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", str(requested_path)):
            return None
        exact: list[tuple[dict, str]] = []
        suggestions: list[tuple[dict, str]] = []
        for grant in self.list_grants(owner, chat_id):
            if grant["access"] != "read":
                continue
            try:
                resolved = self.resolve_grant_directory(grant, requested_path)
            except SourcePathError as exc:
                suggestions.append((grant, exc.failure_details["suggested_path"]))
            except AssetError:
                continue
            else:
                exact.append((grant, resolved))
        matches = exact or suggestions
        if len(matches) > 1:
            raise AssetError(
                "The relative source directory matches more than one authorized root; "
                "provide source_id to select one."
            )
        if not matches:
            return None
        grant, path = matches[0]
        return ("exact" if exact else "suggestion"), grant, path

    def grant(self, owner: str, chat_id: str, host_path: str, basis: str, access: str = "read") -> dict:
        if access not in {"read", "write"}:
            raise AssetError("Invalid grant access")
        resolved = self.bridge.resolve(host_path)
        expected_type = "file" if access == "write" else "directory"
        if resolved.get("type") != expected_type:
            raise AssetError("Read grants require a directory; write grants are limited to one existing file")
        canonical = resolved["path"]
        grant_id = "grant_" + uuid.uuid4().hex
        with self.db() as conn:
            existing = conn.execute(
                "SELECT id FROM grants WHERE owner=? AND chat_id=? AND host_path=? AND access=?",
                (owner, chat_id, canonical, access),
            ).fetchone()
            if existing:
                grant_id = existing["id"]
                conn.execute(
                    "UPDATE grants SET active=1, basis=? WHERE id=?",
                    (basis[:4000], grant_id),
                )
            else:
                conn.execute(
                    "INSERT INTO grants(id,owner,chat_id,host_path,access,basis) VALUES(?,?,?,?,?,?)",
                    (grant_id, owner, chat_id, canonical, access, basis[:4000]),
                )
        return self.get_grant(owner, chat_id, grant_id)

    def revoke(self, owner: str, chat_id: str, grant_id: str) -> dict:
        with self.db() as conn:
            changed = conn.execute(
                "UPDATE grants SET active=0 WHERE id=? AND owner=? AND chat_id=?",
                (grant_id, owner, chat_id),
            ).rowcount
        if not changed:
            raise AssetError("Source grant does not exist")
        return {"grant_id": grant_id, "revoked": True}

    def authorize_latest_request(self, owner: str, chat_id: str, requested_path: str, latest_user: str) -> dict:
        if _system_path_blocked(requested_path):
            raise AssetError(
                "System directories cannot be authorized implicitly from a chat request"
            )
        intent_words = (
            "检查", "查看", "看看", "列出", "读取", "使用", "处理", "打开", "搜索",
            "有什么", "有哪些", "目录下", "文件夹下",
            "inspect", "list", "read", "use", "process", "open", "search",
        )
        authorized = _explicit_path_mention(requested_path, latest_user) and any(
            word in latest_user.casefold() for word in intent_words
        )
        if not authorized:
            raise AssetError(
                "This host path is not authorized by the latest user request. "
                "The user must explicitly ask to inspect or use the directory."
            )
        return self.grant(owner, chat_id, requested_path, latest_user, "read")

    def authorize_latest_write(self, owner: str, chat_id: str, requested_path: str, latest_user: str) -> dict:
        normalized_request = requested_path.replace("/", "\\").casefold()
        normalized_user = latest_user.replace("/", "\\").casefold()
        verbs = ("修改", "编辑", "替换", "修复", "改写", "modify", "edit", "replace", "fix")
        if normalized_request not in normalized_user or not any(word in latest_user.casefold() for word in verbs):
            raise AssetError("Writing this host file is not explicitly authorized by the latest user request")
        return self.grant(owner, chat_id, requested_path, latest_user, "write")

    def source_token(self, grant: dict) -> str:
        if not self.bridge.available:
            raise AssetError("Host bridge is not configured")
        message = f"{grant['chat_id']}\n{grant['host_path']}\n{grant['access']}".encode("utf-8")
        return hmac.new(self.bridge.key.encode("utf-8"), message, hashlib.sha256).hexdigest()

    @staticmethod
    def _public(row: dict) -> dict:
        return {
            "id": row["id"],
            "chat_id": row["chat_id"],
            "host_path": row["host_path"],
            "access": row["access"],
            "basis": row["basis"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
        }

    def bridge_payload(self, grant: dict, **values) -> dict:
        return {
            "grant_id": grant["id"],
            "chat_id": grant["chat_id"],
            "root": grant["host_path"],
            "access": grant["access"],
            "grant_token": self.source_token(grant),
            **values,
        }

    def cleanup_session(self, chat_id: str) -> None:
        execution_db = self.root / "sessions" / chat_id / "workspace" / ".runtime" / "executions.sqlite3"
        if execution_db.is_file() and self.bridge.available:
            conn = sqlite3.connect(execution_db, timeout=10)
            try:
                job_ids = [row[0] for row in conn.execute("SELECT id FROM jobs").fetchall()]
            finally:
                conn.close()
            for job_id in job_ids:
                try:
                    self.bridge.job("cleanup", {"job_id": job_id})
                except AssetError:
                    pass
        with self.db() as conn:
            conn.execute("UPDATE grants SET active=0 WHERE chat_id=?", (chat_id,))
