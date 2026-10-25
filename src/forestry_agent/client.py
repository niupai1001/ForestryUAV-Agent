from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

import httpx

from .config import Config
from .contracts import Failure, TaskCreate


def collect_events(client, task_id: str):
    """Follow the bounded event API cursor; never mistake its first page for a journal."""
    result, cursor = [], 0
    while len(result) < 100000:
        response = client.get(f"/tasks/{task_id}/events", params={"format": "json", "cursor": cursor})
        response.raise_for_status()
        page = response.json()
        if not page:
            return result
        next_cursor = page[-1]["sequence"]
        if next_cursor <= cursor or any(e["sequence"] <= cursor for e in page):
            raise Failure("RECONCILIATION_REQUIRED", "事件分页游标未前进", 409)
        result.extend(page)
        cursor = next_cursor
    raise Failure("RESOURCE_EXHAUSTED", "事件导出超出上限；请使用分段游标", 422)


class LocalClient:
    def __init__(self, config: Config):
        key = config.workspace_root / ".forestry" / "local.key"
        if not key.is_file():
            raise Failure("PRECONDITION_MISSING", "请先启动本地 forestry-agent serve", 422)
        self.http = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=30,
            trust_env=False, headers={"authorization": "Bearer " + key.read_text(encoding="utf-8").strip()})

    def request(self, method, path, payload=None, params=None):
        response = self.http.request(method, path, json=payload, params=params)
        if response.is_error:
            error = response.json().get("error", {})
            raise Failure(error.get("code", "API_ERROR"), error.get("message", response.text), response.status_code)
        return response.json()

    def run(self, payload: dict, inputs: list[str], timeout: int = 300):
        # Validate before creating grants or tasks; never accept forged internal IDs.
        spec = TaskCreate.model_validate(payload)
        for source in inputs:
            resource = self.request("POST", "/resources/register", {"request_id": str(uuid4()),
                "path": str(Path(source).resolve(strict=True)), "classification": "private", "read_only": True})
            if resource["resource_id"] not in spec.input_refs:
                spec.input_refs.append(resource["resource_id"])
        state = self.request("POST", "/tasks", spec.model_dump())
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            state = self.request("GET", "/tasks/" + state["task_id"])
            if state["status"] not in {"created", "active", "waiting_job", "publishing"}:
                return {"state": state, "wait_timed_out": False, "accepted": state["status"] == "completed"}
            time.sleep(0.5)
        return {"state": state, "wait_timed_out": True, "accepted": False,
                "notice": "CLI 等待超时，后台任务仍有持久句柄；未自动取消。"}

    def acceptance(self, task_id: str, verdict: str, comment: str):
        candidate = self.request("GET", f"/tasks/{task_id}/acceptance-candidate")
        return self.request("POST", f"/tasks/{task_id}/acceptance", {
            "request_id": str(uuid4()), "expected_state_version": candidate["state_version"],
            "expected_spec_revision": candidate["candidate"]["spec_revision"],
            "candidate_sha256": candidate["candidate_sha256"], "verdict": verdict, "comment": comment})

    def close(self):
        self.http.close()
