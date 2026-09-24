"""Drive a deployed Runtime and save raw observations; never read gold data."""

from __future__ import annotations

import argparse
from http.client import HTTPException, RemoteDisconnected
import json
import mimetypes
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

from .trace import normalize_trace


TERMINAL = {"completed", "failed", "canceled", "cancel_incomplete", "paused"}

# A dropped connection is usually transient: the service is restarting, the Docker
# VM is under pressure, or a keep-alive connection went stale. Treating the first one
# as a permanent infrastructure failure threw away whole slots that would have
# succeeded seconds later, and marked them "unknown" rather than re-attempting.
TRANSIENT_ATTEMPTS = max(1, int(os.getenv("EVAL_HTTP_ATTEMPTS", "5")))
TRANSIENT_BACKOFF_SECONDS = max(1.0, float(os.getenv("EVAL_HTTP_BACKOFF_SECONDS", "5")))

# A fixed retry count cannot tell "the service is gone" from "the service is coming
# back". Both look like a dropped connection, and only the second is worth waiting
# for: a container restart takes tens of seconds, so five attempts with a 5s base
# delay gave up while the Runtime was still starting and recorded a whole slot as an
# infrastructure error. After the fixed attempts are exhausted the client therefore
# asks `/health` until it reports ok, up to this budget, and only then gives up.
HEALTH_WAIT_SECONDS = max(0.0, float(os.getenv("EVAL_HEALTH_WAIT_SECONDS", "600")))
HEALTH_POLL_SECONDS = max(1.0, float(os.getenv("EVAL_HEALTH_POLL_SECONDS", "5")))


class RuntimeApiClient:
    def __init__(self, base_url: str, api_key: str, owner: str, chat_id: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "X-User-ID": owner,
            "X-Chat-ID": chat_id,
        }
        self.transient_retries = 0
        self.health_waits = 0

    def wait_until_healthy(self) -> bool:
        """Wait for a Runtime that is reachable but not yet serving.

        Returns True when the service answered its health endpoint, so the caller can
        retry the original request. Returns False when the budget expired, which means
        the deployment really is down and the slot is an infrastructure failure.
        """
        deadline = time.monotonic() + HEALTH_WAIT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(HEALTH_POLL_SECONDS)
            try:
                request = Request(self.base_url + "/health", method="GET")
                with urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                continue
            if str(payload.get("status")) in {"ok", "degraded"}:
                self.health_waits += 1
                return True
        return False

    def request(
        self, method: str, path: str, payload: Any = None,
        *, content_type: str = "application/json", timeout: float = 60,
    ) -> Any:
        body = None
        headers = dict(self.headers)
        if payload is not None:
            body = (
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if content_type == "application/json" else payload
            )
            headers["Content-Type"] = content_type
        last: Exception | None = None
        attempt = 0
        waited_for_health = False
        while attempt < TRANSIENT_ATTEMPTS:
            attempt += 1
            request = Request(
                self.base_url + path, data=body, headers=headers, method=method
            )
            try:
                with urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    return json.loads(raw.decode("utf-8"))
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                # 5xx is the server's problem and often transient too; 4xx is ours.
                if exc.code < 500 or attempt >= TRANSIENT_ATTEMPTS:
                    raise RuntimeError(f"Runtime HTTP {exc.code}: {detail}") from exc
                last = RuntimeError(f"Runtime HTTP {exc.code}: {detail}")
            except (RemoteDisconnected, HTTPException, ConnectionError, URLError,
                    TimeoutError) as exc:
                # TimeoutError belongs here: a slow answer is not a failed experiment.
                # The event stream is polled with a bounded server-side long-poll, and
                # under load a single read can exceed the client timeout while the Run
                # is perfectly healthy -- the poll resumes from the last offset, so the
                # only cost of retrying is the wait. Treating it as permanent recorded
                # two `supervised` slots as `infra_error` with 32 and 24 steps of real
                # work already on disk.
                last = exc
                if attempt >= TRANSIENT_ATTEMPTS:
                    if waited_for_health or not self.wait_until_healthy():
                        # Either the service did not come back within its budget, or it
                        # already did once and the connection dropped again -- both mean
                        # this request is not going to succeed by waiting longer.
                        break
                    # The service answered its health endpoint, so the original request
                    # is worth one more round of the fixed attempts.
                    waited_for_health = True
                    attempt = 0
                    continue
            self.transient_retries += 1
            time.sleep(TRANSIENT_BACKOFF_SECONDS * attempt)
        raise RuntimeError(f"Runtime unavailable after {TRANSIENT_ATTEMPTS} attempts: {last}")

    def upload(self, path: Path) -> dict[str, Any]:
        boundary = "----forestry-eval-" + uuid.uuid4().hex
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode("utf-8") + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self.request(
            "POST", "/assets", body,
            content_type=f"multipart/form-data; boundary={boundary}", timeout=300,
        )


def collect_trial(
    *, client: RuntimeApiClient, case_id: str, repeat: int, prompt: str,
    fixture_files: list[Path], output: Path, configuration: dict[str, Any],
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    raw_dir = output / "raw"
    artifact_dir = output / "artifacts"
    raw_dir.mkdir()
    artifact_dir.mkdir()
    status = "infra_error"
    run: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    try:
        client.request("POST", "/sessions", {"chat_id": client.headers["X-Chat-ID"]})
        assets = [client.upload(path) for path in fixture_files]
        run = client.request("POST", "/runs", {
            "messages": [{"role": "user", "content": prompt}],
            "asset_ids": [asset["id"] for asset in assets],
            "use_tools": True,
        })
        deadline = time.monotonic() + timeout_seconds
        after = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # The deadline is checked before the request, not only by the loop
                # condition: a request that itself takes time can otherwise push the
                # loop past its budget and exit without ever recording a timeout.
                status = "timeout"
                break
            page = client.request(
                "GET", f"/runs/{run['id']}/events?after={after}&limit=1000&wait_seconds=20",
                # Generous on purpose: the server long-polls for up to 20s and may be
                # busy with another session's request, so a client timeout near the
                # poll window turns healthy waiting into a spurious failure.
                timeout=min(120, max(30, int(remaining))),
            )
            events.extend(page.get("events") or [])
            after = int(page.get("next") or after)
            run = page.get("run") or run
            if run.get("state") in TERMINAL and not page.get("has_more"):
                break
        if status == "timeout":
            # A Run still holding a live job when the collection budget expires must
            # be stopped, or it keeps consuming the single execution slot and the
            # next collection blocks behind it. The timeout is recorded either way.
            try:
                client.request("POST", f"/runs/{run['id']}/cancel", {})
            except Exception:
                pass
            (raw_dir / "collector_timeout.txt").write_text(
                f"Collection exceeded {timeout_seconds}s in state "
                f"{run.get('state')!r}; the Run was cancelled.\n",
                encoding="utf-8",
            )
        else:
            status = "evaluated" if run.get("state") in TERMINAL else "infra_error"
        turns_payload = client.request("GET", f"/runs/{run['id']}/turns")
        turns = turns_payload.get("turns") or []
        artifact_records: dict[str, dict[str, Any]] = {}
        for event in events:
            if event.get("type") == "done":
                for artifact in event.get("artifacts") or []:
                    if isinstance(artifact, dict) and artifact.get("id"):
                        artifact_records[artifact["id"]] = artifact
            if event.get("type") == "tool_end":
                result = event.get("result")
                pending = [result]
                while pending:
                    value = pending.pop()
                    if isinstance(value, dict):
                        if str(value.get("id") or "").startswith("asset_"):
                            artifact_records.setdefault(value["id"], value)
                        pending.extend(value.values())
                    elif isinstance(value, list):
                        pending.extend(value)
        for artifact in artifact_records.values():
                asset_id = artifact.get("id")
                if not asset_id:
                    continue
                target = artifact_dir / f"{asset_id}-{Path(artifact.get('name') or asset_id).name}"
                request = Request(
                    client.base_url + f"/assets/{asset_id}/content",
                    headers=client.headers,
                )
                with urlopen(request, timeout=300) as response:
                    target.write_bytes(response.read())
    except Exception as exc:
        (raw_dir / "collector_error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
        )

    (raw_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (raw_dir / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (raw_dir / "turns.json").write_text(
        json.dumps(turns, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (raw_dir / "transport.json").write_text(
        json.dumps({
            "transient_retries": getattr(client, "transient_retries", 0),
            "health_waits": getattr(client, "health_waits", 0),
            "collected_status": status,
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not run.get("id"):
        # Collection never reached the Runtime. Writing a trace anyway produced a
        # record whose trial_id was the empty string; the scorecard then rejected
        # the whole run as "duplicate trial id", which surfaced as a gate failure
        # for what was really an unreachable service. Leave the truth in place and
        # let the caller mark the slot instead of fabricating a graded trial.
        error_file = raw_dir / "collector_error.txt"
        detail = (
            error_file.read_text(encoding="utf-8").strip()
            if error_file.is_file() else "collection produced no Runtime run"
        )
        return {"status": "infra_error", "run": run, "trace": None, "error": detail}
    trace = normalize_trace(
        events=events, run=run, turns=turns, case_id=case_id,
        repeat=repeat, configuration=configuration,
    )
    (output / "trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"status": status, "run": run, "trace": trace}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default=os.environ.get("RUNTIME_API_KEY"))
    parser.add_argument("--owner", default="evaluator")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    if not args.api_key:
        parser.error("--api-key or RUNTIME_API_KEY is required")
    client = RuntimeApiClient(
        args.base_url, args.api_key, args.owner, str(uuid.uuid4())
    )
    result = collect_trial(
        client=client, case_id=args.case_id, repeat=args.repeat,
        prompt=args.prompt_file.read_text(encoding="utf-8"),
        fixture_files=args.fixture, output=args.output,
        configuration=json.loads(args.configuration.read_text(encoding="utf-8")),
    )
    print(json.dumps({"status": result["status"]}, ensure_ascii=False))
    return 0 if result["status"] == "evaluated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
