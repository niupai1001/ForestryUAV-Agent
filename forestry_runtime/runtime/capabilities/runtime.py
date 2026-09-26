"""Capability composition and protocol dispatch for the agent adapter."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..kernel.protocol import ToolPreconditionError, execution_failure, normalize_result, parse_arguments
from ..kernel.registry import build_registry, install_runtime_registry
from ..storage import AssetError
from ..store.executions import ExecutionRecords
from ..workspace import WorkspaceRegistry, is_host_path
from .artifacts.service import ArtifactCapability
from .code_run.service import CodeRunCapability
from .core_specs import load_specs
from .dependency_install.service import DependencyInstallCapability
from .domain_guides.service import DomainGuideCapability
from .environment.service import EnvironmentCapability
from .fs.service import FilesystemCapability
from .job_cancel.service import JobCancelCapability
from .job_status.service import JobStatusCapability
from .knowledge_search.service import KnowledgeCapability


_SPECS = load_specs()
_REGISTRY = build_registry(_SPECS)
install_runtime_registry(_REGISTRY, if_empty=True)
GENERIC_DEFINITIONS = _REGISTRY.as_legacy_definitions()


class RuntimeTools(
    FilesystemCapability, ArtifactCapability, KnowledgeCapability,
    DomainGuideCapability, CodeRunCapability, DependencyInstallCapability,
    EnvironmentCapability, JobStatusCapability, JobCancelCapability,
):
    def __init__(self, store, owner: str, asset_ids: list[str],
                 workspace_registry: WorkspaceRegistry, latest_user: str = "",
                 memory=None, cancelled=None, pause_requested=None):
        self.store = store
        self.owner = owner
        self.chat_id = store.chat_id
        self.allowed = set(asset_ids)
        for asset_id in self.allowed:
            store.get(asset_id, owner)
        self.workspaces = workspace_registry
        self.workspace = workspace_registry.workspace(store)
        self.records = ExecutionRecords(self.workspace)
        self.latest_user = latest_user
        self.memory = memory
        # Durable-job waiting must observe the Run's own lifecycle so a paused or
        # cancelled Run stops waiting instead of blocking a worker thread.
        self.cancelled = cancelled
        self.pause_requested = pause_requested
        self.publish_job_status = None
        self.created: list[dict] = []
        self._domain = None
        self._observations: dict[str, str | None] = {}
        self._knowledge_queries: list[str] = []
        self.input_paths = self._materialise_attachments()

    # An uploaded asset is stored *beside* the workspace, and a job container mounts
    # only the workspace. Without the copy below every raster, archive or binary the
    # user attached is unreachable from model-written code: the agent can list it,
    # preview it, and read it as text, but `open()` inside the sandbox fails. That
    # reads downstream as "the model cannot handle a GeoTIFF" when the real fact is
    # that the bytes were never in the sandbox -- and the agent then spends its whole
    # budget walking the filesystem looking for a file that was never mounted.
    # Materialising attachments into one obvious directory is what makes an
    # attachment and a workspace file the same thing to the agent.
    def _materialise_attachments(self) -> dict[str, str]:
        """Copy this chat's attached assets to ``<workspace>/inputs/`` once."""
        if not self.allowed:
            return {}
        directory = self.workspace / "inputs"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return {}
        placed: dict[str, str] = {}
        taken: set[str] = set()
        for asset_id in sorted(self.allowed):
            try:
                asset = self.store.get(asset_id, self.owner)
                source = self.store.path(asset_id, self.owner)
            except AssetError:
                continue
            if not source.is_file():
                continue
            name = str(asset.get("name") or source.name).replace("\\", "/").rsplit("/", 1)[-1]
            if not name or name in {".", ".."}:
                name = asset_id
            if name.casefold() in taken:
                name = f"{asset_id[-8:]}-{name}"
            taken.add(name.casefold())
            target = directory / name
            try:
                # Size comparison keeps this idempotent across turns without hashing
                # a multi-hundred-megabyte raster on every model request.
                if not target.is_file() or target.stat().st_size != source.stat().st_size:
                    shutil.copyfile(source, target)
            except OSError:
                continue
            placed[asset_id] = f"inputs/{name}"
        return placed

    @staticmethod
    def _call_key(name: str, arguments: dict) -> str:
        return name + ":" + json.dumps(arguments, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _version(path: Path) -> str:
        stat = path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def observation_key(self, name: str, arguments: dict) -> str | None:
        if name not in {"fs_read", "fs_list", "fs_search", "tool_result_read"}:
            return None
        call_key = self._call_key(name, arguments)
        if call_key in self._observations:
            return self._observations[call_key]
        values = dict(arguments)
        if name == "tool_result_read":
            result_id = str(values.get("result_id") or "")
            target = self.workspace / ".runtime" / "tool-results" / f"{result_id}.json"
            try:
                version = [target.stat().st_size, target.stat().st_mtime_ns]
            except OSError:
                version = ["unavailable"]
            return json.dumps(
                {"tool": name, "arguments": values, "version": version},
                ensure_ascii=False, sort_keys=True,
            )
        path = str(values.get("path") or ".")
        scope = str(values.get("scope") or "workspace")
        if is_host_path(path):
            scope = "source"
        if scope == "source":
            return None
        try:
            if scope == "asset":
                target = self.store.path(str(values.get("asset_id") or ""), self.owner)
                stat = target.stat()
                version = [stat.st_size, stat.st_mtime_ns]
            else:
                target = self.workspaces.workspace_path(self.store, path, require_exists=True)
                if target.is_file():
                    stat = target.stat()
                    version = [stat.st_size, stat.st_mtime_ns]
                elif name == "fs_list":
                    version = [
                        [item.name, item.stat().st_size, item.stat().st_mtime_ns]
                        for item in sorted(target.iterdir(), key=lambda item: item.name.casefold())
                    ]
                else:
                    files = [item for item in target.rglob("*") if item.is_file()]
                    stats = [item.stat() for item in files]
                    version = [len(stats), sum(item.st_size for item in stats),
                               max((item.st_mtime_ns for item in stats), default=0)]
        except (AssetError, OSError):
            version = ["unavailable"]
        return json.dumps(
            {"tool": name, "arguments": values, "version": version},
            ensure_ascii=False, sort_keys=True,
        )

    def attachment_context(self) -> list[dict]:
        return [self.store.get(asset_id, self.owner) for asset_id in sorted(self.allowed)]

    def execute(self, name: str, arguments: dict | None, progress=None) -> dict:
        definition = GENERIC_DEFINITIONS.get(name)
        if definition is None:
            return normalize_result({"ok": False, "error": "Unknown or unavailable tool.",
                    "available_tools": sorted(GENERIC_DEFINITIONS)})
        args, changes, failure = parse_arguments(definition[0], arguments)
        if failure:
            return failure
        try:
            with self.store.operation():
                data = getattr(self, name)(**args.model_dump())
            result = {"ok": True, "data": data}
            if name in {"fs_read", "fs_list", "fs_search", "tool_result_read"}:
                values = args.model_dump()
                self._observations[self._call_key(name, values)] = self.observation_key(name, values)
            if changes:
                result["argument_normalization"] = changes
            return normalize_result(result)
        except AssetError as exc:
            path = str(args.path) if hasattr(args, "path") else ""
            scope = str(args.scope) if hasattr(args, "scope") else "workspace"
            if (
                str(exc) == "Workspace path does not exist"
                and scope == "workspace"
                and path not in ("", ".")
                and not is_host_path(path)
                and path.replace("\\", "/").casefold()
                in self.latest_user.replace("\\", "/").casefold()
            ):
                return execution_failure(ToolPreconditionError(
                    "The requested file is not in this chat's Workspace. A path in the message does not upload or mount a file.",
                    code="requested_input_unavailable",
                    requested_path=path,
                    checked_scope={"scope": "workspace", "path": path},
                    missing=[{"kind": "workspace_file", "path": path}],
                    available_assets=[
                        {"asset_id": item["id"], "name": item["name"]}
                        for item in self.attachment_context()
                    ],
                    source_grant_count=len(self.workspaces.list_grants(self.owner, self.chat_id)),
                    suggested_user_action=(
                        "Upload the file to this chat, or explicitly ask to use "
                        "the absolute Windows path of the directory containing it."
                    ),
                ))
            if str(exc) == "Workspace path does not exist" and scope == "workspace":
                return execution_failure(ToolPreconditionError(
                    "Workspace path does not exist.",
                    code="workspace_path_not_found", reason="not_found",
                    requested_path=path,
                    checked_scope={"scope": "workspace", "path": path},
                    missing=[{"kind": "workspace_path", "path": path}],
                    candidates=[], control_verified=None,
                ))
            return execution_failure(exc)
        except Exception as exc:
            return execution_failure(exc)

    def _execute_domain(self, name, arguments, progress=None):
        if self._domain is None:
            from .domain_runtime import RemoteSensingTools
            self._domain = RemoteSensingTools(
                self.store, self.owner, list(self.allowed),
                self.workspaces, self.latest_user,
            )
        result = self._domain.execute(name, arguments, progress)
        for asset in self._domain.created:
            if asset["id"] not in {item["id"] for item in self.created}:
                self.created.append(asset)
                self.allowed.add(asset["id"])
        return result
