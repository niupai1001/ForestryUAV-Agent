"""Sandboxed code execution with explicit admission failures.

A ``job_id`` in a successful response means the job *started*.  Two admission
failures are reported distinctly rather than being collapsed into an unknown
submission outcome:

* ``resource_busy`` -- every execution slot is occupied; retrying later is safe;
* ``blocked_by`` -- a dependency installation is unsettled or the code imports
  modules the environment has not verified.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from ...kernel.protocol import SubmissionRefused
from ...storage import AssetError
from ...store.executions import snapshot_workspace
from ...workspace import BridgeRequestError


class CodeRunRefused(SubmissionRefused):
    """Code execution was refused before anything was submitted."""


def sandbox_unavailable(reason: str) -> CodeRunRefused:
    """The execution sandbox is not configured, so nothing can be submitted.

    This is a deployment condition, not an unknown outcome. Reporting it as a plain
    ``AssetError`` gave it the generic code ``AssetError``, which reads like an
    unexplained tool fault rather than "this Runtime has no sandbox wired up".
    """
    return CodeRunRefused(
        f"Execution is unavailable: {reason}",
        code="sandbox_unavailable",
        retryable=False,
        operation_started=False,
        guidance=(
            "This Runtime has no execution sandbox configured. Report that the "
            "computation cannot run here instead of retrying the same call."
        ),
    )


class CodeRunCapability:
    def _start(self, kind: str, payload: dict, fingerprint: str):
        # Admission is decided before a job record or an action file exists, so a
        # refusal leaves nothing behind that looks like an in-flight submission.
        try:
            host_workspace = str(self.workspaces.host_workspace(self.store))
        except Exception as exc:
            raise sandbox_unavailable(str(exc)) from exc
        before = snapshot_workspace(self.workspace)
        job_id = "job_" + uuid.uuid4().hex
        self.records.add(job_id, fingerprint, kind, before)
        try:
            result = self.workspaces.bridge.job("start", {"job_id": job_id, "workspace": host_workspace, "chat_id": self.chat_id, **payload})
            self.records.update(job_id, result.get("state", "running"))
            return result
        except BridgeRequestError as submission_error:
            if submission_error.code == "resource_busy":
                # Nothing was launched.  This is a retryable admission decision,
                # not an unknown outcome, so it must not be recorded as uncertainty.
                self.records.update(job_id, "not_started")
                raise CodeRunRefused(
                    "No execution slot was free, so the job was not started and "
                    "nothing was submitted.",
                    data={
                        "job_id": job_id,
                        "active_jobs": submission_error.details.get("active_jobs"),
                        "max_jobs": submission_error.details.get("max_jobs"),
                    },
                    code="resource_busy",
                    retryable=True,
                    guidance=(
                        "Wait for the running job to finish with job_wait, then submit "
                        "again. Do not cancel a healthy installation job to free the slot."
                    ),
                ) from submission_error
            return self._reconcile(job_id, submission_error)
        except Exception as submission_error:
            return self._reconcile(job_id, submission_error)

    def _reconcile(self, job_id: str, submission_error: Exception):
        try:
            reconciled = self.workspaces.bridge.job("status", {"job_id": job_id, "offset": 0})
        except Exception:
            self.records.update(job_id, "submission_uncertain")
            raise submission_error
        self.records.update(job_id, reconciled.get("state", "running"))
        return reconciled | {"submission_reconciled": True}

    def code_run(self, language, code, source_ids=None, timeout_seconds=14400,
                 required_packages=None):
        # Every refusal is decided *before* a job record or an action file exists,
        # so a refused submission leaves no unsettled job behind.
        preflight = self.execution_preflight(code)
        if preflight is not None:
            failure = {
                key: value for key, value in preflight["failure"].items()
                if key not in {"operation_started", "side_effects"}
            }
            raise CodeRunRefused(preflight["error"], data=dict(failure), **failure)
        for package in required_packages or []:
            allowed, evidence = self.require_verified([package])
            if not allowed:
                raise CodeRunRefused(
                    "Execution was not started: the requested dependency has no "
                    "verified installation in this environment.",
                    data={"evidence": evidence, "package": package},
                    code="blocked_by",
                    blocked_by=evidence.get("blocked_by", "dependency_install"),
                    evidence=evidence,
                    retryable=True,
                    suggested_tool=(
                        "dependency_install"
                        if evidence.get("missing_from_manifest")
                        else "environment_check"
                    ),
                    suggested_arguments={"packages": [package]},
                )
        suffix = ".py" if language == "python" else ".sh"
        fingerprint = json.dumps({"language": language, "code": code, "sources": source_ids or []}, ensure_ascii=False, sort_keys=True)
        action_name = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:20] + suffix
        action = self.workspace / ".runtime" / "actions" / action_name
        action.write_text(code.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
        sources = []
        for source_id in source_ids or []:
            grant = self.workspaces.get_grant(self.owner, self.chat_id, source_id)
            sources.append(self.workspaces.bridge_payload(grant, id=grant["id"]))
        return self._start(language, {"kind": language, "action_path": action.relative_to(self.workspace).as_posix(), "sources": sources, "timeout_seconds": timeout_seconds}, fingerprint)
