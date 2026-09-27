"""Capability composition and protocol dispatch for the agent adapter."""
from __future__ import annotations

import json
from pathlib import Path

from ..kernel.protocol import ToolPreconditionError, execution_failure, normalize_result, parse_arguments
from ..kernel.registry import build_registry, install_runtime_registry
from ..plan import ObservationLedger
from ..storage import AssetError
from ..store.executions import ExecutionRecords
from ..workspace import WorkspaceRegistry, is_host_path
from .artifacts.delivery import attach_deliveries
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
from .work_plan.service import WorkPlanCapability


_SPECS = load_specs()
_REGISTRY = build_registry(_SPECS)
install_runtime_registry(_REGISTRY, if_empty=True)
GENERIC_DEFINITIONS = _REGISTRY.as_legacy_definitions()


def documents_in_result(name: str, data) -> list[str]:
    """Document bodies a tool result actually handed over, by citation.

    A listing names documents; only a body can be cited as read. The test for that
    is deliberately mechanical -- an entry counts only when it carries both a
    citation and its own content -- because the distinction is exactly what "the
    guide says so" used to skip. A guide catalogue entry has no content and so does
    not count; a guide body, a knowledge chunk and a search hit with text do.
    """
    documents: list[str] = []

    def add(citation, content):
        text = str(citation or "").strip()
        if text and content:
            documents.append(text)

    if isinstance(data, dict):
        add(data.get("citation"), data.get("content"))
        for key in ("chunks", "results", "matches", "items"):
            for entry in data.get(key) or []:
                if isinstance(entry, dict):
                    add(entry.get("citation"), entry.get("content"))
    return documents


class RuntimeTools(
    FilesystemCapability, ArtifactCapability, KnowledgeCapability,
    DomainGuideCapability, WorkPlanCapability, CodeRunCapability,
    DependencyInstallCapability, EnvironmentCapability, JobStatusCapability,
    JobCancelCapability,
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
        # What this workspace can cite as evidence: one entry per tool call that
        # returned, plus every document body that was actually handed over. The plan
        # refuses a citation that is not in here, which is what makes "according to
        # the guide" checkable rather than rhetorical.
        self.ledger = ObservationLedger(self.workspace / ".runtime" / "observations.json")
        self._work_plan_store = None

    # ---------------------------------------------------------------- evidence

    def record_observation(self, name: str, arguments: dict, result: dict) -> dict:
        """Issue an observation id for a call that returned, and attach it.

        Called for every dispatched tool call, successful or not: a failed call is
        also an observation, and a plan may legitimately say "the file is not a
        GeoTIFF" on the strength of one.
        """
        data = result.get("data") if isinstance(result, dict) else None
        documents = documents_in_result(name, data)
        summary = ""
        if isinstance(data, dict):
            for key in ("path", "name", "guide_id", "query", "job_id", "state", "code"):
                if isinstance(data.get(key), str):
                    summary = f"{key}={data[key]}"
                    break
        failure = result.get("failure") if isinstance(result, dict) else None
        if not summary and isinstance(failure, dict):
            summary = f"failure={failure.get('code')}"
        identifier = self.ledger.record(
            name, arguments or {}, ok=bool(result.get("ok")) if isinstance(result, dict) else False,
            summary=summary, documents=documents,
        )
        if isinstance(result, dict):
            # Always at the top level, so a refusal carries a citable id too -- "this
            # file is not a GeoTIFF" is an observation a plan may rest on -- and again
            # inside `data` when there is one, where a rendered result shows it.
            result["observation_id"] = identifier
            if isinstance(result.get("data"), dict):
                result["data"].setdefault("observation_id", identifier)
        return result

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
        values = args.model_dump()
        try:
            with self.store.operation():
                data = getattr(self, name)(**values)
            result = {"ok": True, "data": data}
            if name in {"fs_read", "fs_list", "fs_search", "tool_result_read"}:
                self._observations[self._call_key(name, values)] = self.observation_key(name, values)
            if changes:
                result["argument_normalization"] = changes
            result = normalize_result(result)
            result = self.record_observation(name, values, result)
            return self.attach_deliveries(result)
        except AssetError as exc:
            path = str(args.path) if hasattr(args, "path") else ""
            scope = str(args.scope) if hasattr(args, "scope") else "workspace"
            details = getattr(exc, "failure_details", None) or {}
            # Both spellings of "the workspace file is not there": the raw
            # `workspace_path(...)` message and the coded precondition the shared
            # input resolver raises, which reaches here from the artifact tools.
            not_found = (
                str(exc) == "Workspace path does not exist"
                or details.get("code") == "workspace_path_not_found"
            )
            if (
                not_found
                and scope in ("auto", "workspace")
                and path not in ("", ".")
                and not is_host_path(path)
                and path.replace("\\", "/").casefold()
                in self.latest_user.replace("\\", "/").casefold()
            ):
                return self.record_observation(name, values, execution_failure(ToolPreconditionError(
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
                )))
            if not_found and scope in ("auto", "workspace"):
                return self.record_observation(name, values, execution_failure(ToolPreconditionError(
                    "Workspace path does not exist.",
                    code="workspace_path_not_found", reason="not_found",
                    requested_path=path,
                    checked_scope={"scope": "workspace", "path": path},
                    missing=[{"kind": "workspace_path", "path": path}],
                    candidates=[], control_verified=None,
                )))
            return self.record_observation(name, values, execution_failure(exc))
        except Exception as exc:
            return self.record_observation(name, values, execution_failure(exc))

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
        result = self.record_observation(name, arguments or {}, result)
        return self.attach_deliveries(result)

    # -------------------------------------------------------------- deliverables

    def attach_deliveries(self, result: dict) -> dict:
        """Give every artifact in this result its URLs and its separate checks.

        Applied at the dispatch boundary for both toolboxes, so a capability does not
        have to know how a deliverable is addressed -- and so no capability can report
        an output without one.
        """
        try:
            return attach_deliveries(
                result, self.created, chat_id=self.chat_id, store=self.store,
                owner=self.owner,
            )
        except Exception:
            # A description that cannot be built must not fail the call that produced
            # a real artifact; the artifact itself is already registered.
            return result
