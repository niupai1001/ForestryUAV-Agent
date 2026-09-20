import hashlib
import json
import uuid

from ...store.executions import snapshot_workspace


class CodeRunCapability:
    def _start(self, kind: str, payload: dict, fingerprint: str):
        before = snapshot_workspace(self.workspace)
        job_id = "job_" + uuid.uuid4().hex
        self.records.add(job_id, fingerprint, kind, before)
        try:
            result = self.workspaces.bridge.job("start", {"job_id": job_id, "workspace": str(self.workspaces.host_workspace(self.store)), "chat_id": self.chat_id, **payload})
            self.records.update(job_id, result.get("state", "running"))
            return result
        except Exception as submission_error:
            try:
                reconciled = self.workspaces.bridge.job("status", {"job_id": job_id, "offset": 0})
            except Exception:
                self.records.update(job_id, "submission_uncertain")
                raise submission_error
            self.records.update(job_id, reconciled.get("state", "running"))
            return reconciled | {"submission_reconciled": True}

    def code_run(self, language, code, source_ids=None, timeout_seconds=14400):
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
