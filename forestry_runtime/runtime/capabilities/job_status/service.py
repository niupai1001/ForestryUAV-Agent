import json
import mimetypes
import re
import time

from ...storage import AssetError
from ...store.executions import snapshot_workspace


class JobStatusCapability:
    def _domain_job_data(self, name: str, arguments: dict) -> dict:
        job_id = str(arguments["job_id"])
        if re.fullmatch(r"job_[0-9a-f]{32}", job_id):
            raise AssetError("Code job is not registered in this workspace")
        return {
            "job_id": job_id, "job_type": "legacy-external",
            "state": "backend_removed", "terminal": True,
            "needs_finalization": False,
            "failure": {
                "stage": "backend", "code": "local_photogrammetry_backend_removed",
                "operation_started": False, "side_effects": "none",
                "message": (
                    "The local photogrammetry backend is not part of this Runtime. "
                    "Existing outputs may be imported; future execution can be provided by MCP."
                ),
            },
        }

    def _register_job_artifacts(self, job_id: str) -> list[dict]:
        record = self.records.get(job_id)
        existing = json.loads(record["artifacts_json"] or "[]")
        if existing:
            return existing
        before = {key: tuple(value) for key, value in json.loads(record["before_json"]).items()}
        after = snapshot_workspace(self.workspace)
        changed = [path for path, value in after.items() if before.get(path) != value]
        artifacts = []
        for relative in changed[:500]:
            target = self.workspace / relative
            media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            asset = self.store.register_path(target, target.name, self.owner, media_type, artifact_kind="job-output", metadata={"job_id": job_id, "workspace_path": relative})
            artifacts.append(asset)
            if asset["id"] not in {item["id"] for item in self.created}:
                self.created.append(asset)
                self.allowed.add(asset["id"])
        self.records.update(job_id, record["state"], artifacts, after)
        return artifacts

    def job_status(self, job_id, offset=0, wait_seconds=20):
        try:
            record = self.records.get(job_id)
        except AssetError:
            return self._domain_job_data("get_job_status", {"job_id": job_id})
        deadline = time.monotonic() + wait_seconds
        while True:
            result = self.workspaces.bridge.job("status", {"job_id": job_id, "offset": offset})
            if result["terminal"] or result.get("output") or time.monotonic() >= deadline:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        self.records.update(job_id, result["state"])
        result["job_type"] = record["kind"] if record["kind"] == "install" else "code"
        if result["terminal"]:
            result["artifacts"] = self._register_job_artifacts(job_id)
            if record["kind"] == "install":
                manifest = self.workspace / ".runtime" / "deps" / "installed-packages.json"
                if manifest.is_file():
                    result["installed_packages"] = json.loads(manifest.read_text(encoding="utf-8"))[:500]
        return result
