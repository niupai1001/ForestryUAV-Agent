"""Capability composition and protocol dispatch for the agent adapter."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
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

#: Tools that change nothing, read from the same declarations the scheduler uses
#: rather than listed here. Their identical repeat already carries its own answer
#: (`already_read` from the agent's read cache) or is a legitimate second question
#: about something still moving (`job_status` while a job runs), so repeating one
#: is not what repetition feedback is for. A tool that gains a side effect starts
#: being counted without anyone remembering to edit a list.
_SIDE_EFFECT_FREE_TOOLS = frozenset(
    spec.name for spec in _SPECS if spec.side_effect.value == "none"
)


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
        self._knowledge_queries: list[str] = []
        # What this workspace can cite as evidence: one entry per tool call that
        # returned, plus every document body that was actually handed over. The plan
        # refuses a citation that is not in here, which is what makes "according to
        # the guide" checkable rather than rhetorical.
        self.ledger = ObservationLedger(self.workspace / ".runtime" / "observations.json")
        self._work_plan_store = None
        # The code-intelligence service, when this Run has one. ``None`` is the
        # normal state: a Run that never asks a code question does not pay for an
        # index. It is created by the retrieval fabric when code answers are
        # enabled, and handed back here so file writes can invalidate it -- the one
        # channel a capability has to a source registered inside the router.
        self._code_intelligence = None
        self.input_paths = self._materialise_attachments()

    # ---------------------------------------------------------------- evidence

    def _invalidate_code_index(self, target) -> None:
        """A workspace file changed; what the code index says about it is stale.

        Only a service this Run already holds is notified. Creating one here would
        index the whole workspace on every write -- and would be pointless, because
        a Run with no index has nothing to invalidate: the first index it ever
        builds reads the current bytes and is correct by construction. Non-Python
        files are skipped for the same reason: the symbol index never held them.
        """
        service = self._code_intelligence
        if service is None:
            return
        if str(getattr(target, "suffix", "")).casefold() != ".py":
            return
        try:
            service.invalidate(str(target))
        except Exception:
            # Stale code answers are worse than none but never worse than a failed
            # write: the edit succeeded, and the index must not take that down.
            pass

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
        identifier, occurrences = self.ledger.record_with_occurrences(
            name, arguments or {}, ok=bool(result.get("ok")) if isinstance(result, dict) else False,
            summary=summary, documents=documents,
        )
        if isinstance(result, dict):
            # Always at the top level, so a refusal carries a citable id too -- "this
            # file is not a GeoTIFF" is an observation a plan may rest on -- and again
            # inside `data` when there is one, where a result rendered shows it.
            result["observation_id"] = identifier
            if isinstance(result.get("data"), dict):
                result["data"].setdefault("observation_id", identifier)
            repeat = self._repetition_note(name, identifier, occurrences, result)
            if repeat is not None:
                result["repeat"] = repeat
                if isinstance(result.get("data"), dict):
                    result["data"].setdefault("repeat", repeat)
        if isinstance(result, dict) and result.get("ok"):
            # Settle the plan here, not only when the model next calls `work_plan`.
            # Settlement used to wait for the model to revise its own plan, so a
            # requirement whose evidence already existed stayed open until the model
            # remembered to look -- and a model that believed it was done had every
            # reason not to look. A failed call settles nothing: it is a fact, not a
            # completion.
            self._settle_requirements()
        return result

    @staticmethod
    def _repetition_note(name: str, identifier: str, occurrences: int, result: dict) -> dict | None:
        """Tell a repeated successful call that it is a repeat -- nothing more.

        The case this fills is the one that had no feedback at all. A repeated
        *failed* call is declined by the failure preflight, and a repeated read is
        answered from the Run's own cache with `already_read`. But a call that
        succeeded and is issued again byte-for-byte was answered "ok" every time,
        so from inside the Run each repetition looked indistinguishable from the
        first: in one Run a 4b model wrote the same report to the same path
        nineteen times and never once read a line saying it had already been
        written, until the request budget ran out and the Run stopped for a
        reason unrelated to the work.

        Refusing the second write would be wrong -- writing the same bytes twice
        is sometimes exactly what a Run intends, and only the model knows whether
        it meant to change something. So nothing is declined and no Run is paused;
        what changes is that the loop can no longer be invisible. A write whose
        bytes differ gets a different observation id, so it is never counted here.
        """
        if occurrences < 2 or not (isinstance(result, dict) and result.get("ok")):
            return None
        if name in _SIDE_EFFECT_FREE_TOOLS:
            # See the note on the constant: these already carry their own answer.
            return None
        return {
            "count": occurrences,
            "observation_id": identifier,
            "note": (
                f"重复调用：本次调用（工具与参数完全相同）在本 Run 中已成功执行 "
                f"{occurrences} 次，结果已记账为 {identifier}。再执行一次会得到完全相同的"
                f"结果，也不会对目标产生任何新的改变。若你期望的变化没有发生，需要改变的是"
                f"参数内容；否则请直接引用上面的 observation_id 继续后续步骤，不要再重复本调用。"
            ),
        }

    def _settle_requirements(self) -> None:
        """Re-settle plan requirements from the evidence this Run now holds.

        Best effort and silent on absence: most Runs have no plan, and an
        observational bookkeeping failure must never fail the tool call that
        produced the evidence.
        """
        plan_store = getattr(self, "_plan_store", None)
        if not callable(plan_store):
            return
        try:
            store = plan_store()
            plan = store.load()
            if not plan.requirements:
                return
            if not any(item.get("status") != "satisfied" for item in plan.requirements):
                return
            store.settle_requirements()
        except Exception:
            return

    # An uploaded asset is stored *beside* the workspace, and a job container mounts
    # only the workspace. Without the copy below every raster, archive or binary the
    # user attached is unreachable from model-written code: the agent can list it,
    # preview it, and read it as text, but `open()` inside the sandbox fails. That
    # reads downstream as "the model cannot handle a GeoTIFF" when the real fact is
    # that the bytes were never in the sandbox -- and the agent then spends its whole
    # budget walking the filesystem looking for a file that was never mounted.
    # Materialising attachments into one obvious directory is what makes an
    # attachment and a workspace file the same thing to the agent.
    @staticmethod
    def _content_digest(path: Path) -> str:
        """SHA-256 of a file, streamed so a large raster is never loaded whole."""
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _materialise_attachments(self) -> dict[str, str]:
        """Copy this chat's attached assets to ``<workspace>/inputs/<asset_id>/`` once.

        The target path is derived from the asset id, not the file name, so two
        attachments that share a name -- or a name and a size -- cannot collide, and
        the mapping is stable across turns and enumeration order: the name never
        carries the identity of the content. A manifest records each asset's id,
        content hash, target path and size. Reuse is decided by comparing the
        recorded identity and size against the source (the asset is immutable per id,
        so the hash is recorded for provenance rather than recomputed every turn); a
        fresh copy goes through a temporary file and an atomic rename so an
        interrupted copy never leaves a half-written file the agent then reads as the
        attachment.
        """
        if not self.allowed:
            return {}
        directory = self.workspace / "inputs"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return {}
        manifest_path = directory / ".manifest.json"
        manifest: dict = {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                manifest = data
        except (OSError, ValueError):
            manifest = {}
        placed: dict[str, str] = {}
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
            relative = f"inputs/{asset_id}/{name}"
            target = directory / asset_id / name
            previous = manifest.get(asset_id) or {}
            if (
                previous.get("path") == relative
                and target.is_file()
                and target.stat().st_size == source.stat().st_size
            ):
                placed[asset_id] = relative
                continue
            digest = self._content_digest(source)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-")
                os.close(fd)
                shutil.copyfile(source, tmp_name)
                os.replace(tmp_name, target)
            except OSError:
                continue
            manifest[asset_id] = {
                "asset_id": asset_id, "name": name, "hash": digest,
                "path": relative, "size": source.stat().st_size,
            }
            placed[asset_id] = relative
        try:
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
        return placed

    @staticmethod
    def _version(path: Path) -> str:
        stat = path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def observation_key(self, name: str, arguments: dict) -> str | None:
        """A version-bearing identity for a read, recomputed on every call.

        The version is the file's current size and mtime, so a read of a changed
        file -- or of a file that has since been deleted -- yields a different key.
        The key is deliberately not memoised: caching the first version would lock a
        later identical read to the stale identity of a file that has since moved.
        """
        if name not in {"fs_read", "fs_list", "fs_search", "tool_result_read"}:
            return None
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
