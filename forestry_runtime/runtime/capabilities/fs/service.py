from __future__ import annotations

import fnmatch
from pathlib import Path

from ...kernel.protocol import ToolPreconditionError
from ...storage import AssetError
from ...workspace import BridgeRequestError, SourcePathError, is_host_path
from ..inputs import InputResolution


class FilesystemCapability(InputResolution):
    """Workspace, attachment, and explicitly granted source file operations."""

    def _grant(self, source_id: str | None, path: str, access: str = "read") -> tuple[dict, str]:
        """The write path only; reads use the shared resolver unchanged.

        Writing is the one case the shared rule deliberately does not cover: a read
        grant may be inferred from the latest user request, while a write grant must
        name the file the user asked to change.
        """
        if access == "write":
            if source_id:
                grant = self.workspaces.get_grant(self.owner, self.chat_id, source_id)
                if grant["access"] != "write":
                    raise AssetError("This source grant is read-only")
                return grant, path
            if not is_host_path(path):
                raise AssetError("source_id is required for a relative source path")
            grant = self.workspaces.authorize_latest_write(
                self.owner, self.chat_id, path, self.latest_user
            )
            return grant, "."
        return self._source_grant(source_id, path)

    def fs_list(self, scope="workspace", path=".", source_id=None, page=1, page_size=100):
        if is_host_path(path):
            scope = "source"
        if scope == "assets":
            rows = self.attachment_context()
            start = (page - 1) * page_size
            return {"items": rows[start:start + page_size], "page": page, "page_size": page_size, "total": len(rows), "has_more": start + page_size < len(rows)}
        if scope == "source":
            grant, relative = self._grant(source_id, path)
            try:
                data = self.workspaces.bridge.fs("list", self.workspaces.bridge_payload(grant, path=relative, page=page, page_size=page_size))
            except BridgeRequestError as exc:
                if exc.code != "path_not_found":
                    raise
                suggestion = self.workspaces.suggest_grant_directory(grant, relative)
                if suggestion:
                    raise SourcePathError(
                        f"Source directory was not found. Retry with path='{suggestion}' "
                        f"and source_id='{grant['id']}'.",
                        source_id=grant["id"], requested_path=relative,
                        suggested_path=suggestion, argument_key="path",
                    )
                raise ToolPreconditionError(
                    "Source directory was not found under the authorized grant.",
                    code="path_not_found", requested_path=relative,
                    source_id=grant["id"],
                    checked_scope={"source_id": grant["id"], "path": relative},
                    missing=[{"kind": "directory", "path": relative}],
                    candidates=[], control_verified=None,
                ) from exc
            return data | {"source": grant}
        target = self.workspaces.workspace_path(self.store, path, require_exists=True)
        if not target.is_dir():
            raise AssetError("Workspace path is not a directory")
        rows = []
        for item in sorted(target.iterdir(), key=lambda p: p.name.casefold()):
            stat = item.stat()
            relative = item.relative_to(self.workspace).as_posix()
            rows.append({"name": item.name, "path": relative, "exact_reference": {"scope": "workspace", "path": relative}, "type": "directory" if item.is_dir() else "file", "size": stat.st_size if item.is_file() else None, "modified": stat.st_mtime, "version": self._version(item) if item.is_file() else None})
        start = (page - 1) * page_size
        return {"items": rows[start:start + page_size], "page": page, "page_size": page_size, "total": len(rows), "has_more": start + page_size < len(rows)}

    @staticmethod
    def _read_local(path: Path, start_line: int, max_lines: int, max_chars: int) -> dict:
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except UnicodeDecodeError as exc:
            return {"text": False, "error": f"binary or non-UTF-8 file: {exc}"}
        selected = "".join(lines[start_line - 1:start_line - 1 + max_lines])[:max_chars]
        return {"text": True, "start_line": start_line, "content": selected, "truncated": start_line - 1 + max_lines < len(lines) or len(selected) >= max_chars}

    def fs_read(self, scope="workspace", path="", asset_id=None, source_id=None, start_line=1, max_lines=200, max_chars=32000):
        if is_host_path(path):
            scope = "source"
        if scope == "asset":
            if not asset_id or asset_id not in self.allowed:
                raise ToolPreconditionError(
                    "An attached asset_id is required.",
                    code="asset_not_available", reason="inapplicable",
                    missing=[{"kind": "asset_id", "value": asset_id}],
                    checked_scope={"scope": "chat_assets"},
                    candidates=[{"asset_id": item["id"], "name": item["name"]}
                                for item in self.attachment_context()[:10]],
                )
            asset = self.store.get(asset_id, self.owner)
            return self._read_local(self.store.path(asset_id, self.owner), start_line, max_lines, max_chars) | {"asset": asset}
        if scope == "source":
            grant, relative = self._grant(source_id, path)
            try:
                data = self.workspaces.bridge.fs("read", self.workspaces.bridge_payload(grant, path=relative, start_line=start_line, max_lines=max_lines, max_chars=max_chars))
            except BridgeRequestError as exc:
                if exc.code != "path_not_found":
                    raise
                raise SourcePathError(
                    "Source file was not found under the authorized grant.",
                    source_id=grant["id"], requested_path=relative,
                    argument_key="path", kind="file",
                ) from exc
            return data | {"source_id": grant["id"]}
        target = self.workspaces.workspace_path(self.store, path, require_exists=True)
        if not target.is_file():
            raise AssetError("Workspace path is not a file")
        relative = target.relative_to(self.workspace).as_posix()
        return self._read_local(target, start_line, max_lines, max_chars) | {
            "path": relative, "version": self._version(target),
            "exact_reference": {"scope": "workspace", "path": relative},
        }

    def fs_search(self, query, scope="workspace", path=".", source_id=None, glob="*", max_results=50):
        if is_host_path(path):
            scope = "source"
        if scope == "source":
            grant, relative = self._grant(source_id, path)
            try:
                data = self.workspaces.bridge.fs("search", self.workspaces.bridge_payload(grant, path=relative, query=query, glob=glob, max_results=max_results))
            except BridgeRequestError as exc:
                if exc.code != "path_not_found":
                    raise
                raise SourcePathError(
                    "Source search path was not found under the authorized grant.",
                    source_id=grant["id"], requested_path=relative,
                    argument_key="path", kind="path",
                ) from exc
            return data | {"source_id": grant["id"]}
        root = self.workspaces.workspace_path(self.store, path, require_exists=True)
        matches = []
        paths = [root] if root.is_file() else root.rglob("*")
        for item in paths:
            if len(matches) >= max_results or not item.is_file() or not fnmatch.fnmatch(item.name, glob) or item.stat().st_size > 4 * 1024 * 1024:
                continue
            try:
                for line_no, line in enumerate(item.read_text(encoding="utf-8").splitlines(), 1):
                    if query.casefold() in line.casefold():
                        matches.append({"path": item.relative_to(self.workspace).as_posix(), "line": line_no, "text": line[:500]})
                        if len(matches) >= max_results:
                            break
            except (UnicodeDecodeError, OSError):
                continue
        return {"matches": matches, "truncated": len(matches) >= max_results}

    def fs_write(self, path, content, overwrite=False):
        target = self.workspaces.workspace_path(self.store, path)
        if target.exists() and not overwrite:
            raise AssetError("Workspace file already exists; set overwrite=true only when replacement is intended")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": target.relative_to(self.workspace).as_posix(), "size": target.stat().st_size}

    def fs_edit(self, path, old, new, replace_all=False, scope="workspace", source_id=None, expected_version=None):
        if scope == "source":
            grant, _ = self._grant(source_id, path, "write")
            return self.workspaces.bridge.fs("edit", self.workspaces.bridge_payload(
                grant, old=old, new=new, replace_all=replace_all
            )) | {"source_id": grant["id"]}
        target = self.workspaces.workspace_path(self.store, path, require_exists=True)
        current_version = self._version(target)
        if expected_version is not None and expected_version != current_version:
            raise ToolPreconditionError(
                "Workspace file changed after it was observed; read it again before editing.",
                code="file_version_conflict", path=path,
                expected_version=expected_version, actual_version=current_version,
            )
        content = target.read_text(encoding="utf-8")
        count = content.count(old)
        if count == 0:
            raise AssetError("Exact text to replace was not found")
        if count > 1 and not replace_all:
            raise AssetError(f"Exact text occurs {count} times; set replace_all=true or provide a unique block")
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        target.write_text(updated, encoding="utf-8")
        return {"path": target.relative_to(self.workspace).as_posix(), "replacements": count if replace_all else 1, "size": target.stat().st_size, "version": self._version(target)}
