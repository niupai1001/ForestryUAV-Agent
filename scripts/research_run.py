"""Single-pass research driver for the 12 frozen tasks.

This reuses the running Agent API (same tasks, grants, Jobs, events, artifacts);
it adds no execution framework, service or database. Every run is one attempt of
one task at one frozen model + harness, with an explicit budget; a budget stop is
recorded as a result and is never retried under the same identity.

    python scripts/research_run.py list
    python scripts/research_run.py run --task 03
    python scripts/research_run.py send --task 06            # next queued follow-up
    python scripts/research_run.py record --task 03          # re-check a finished task
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from forestry_agent.client import LocalClient, collect_events  # noqa: E402
from forestry_agent.config import load_config  # noqa: E402
from forestry_agent.contracts import Failure  # noqa: E402

SUITE = ROOT / "research" / "tasks.research-v1.json"
RECORDS = ROOT / "workspace" / "research-20261021" / "records"
TERMINAL = {"completed", "failed", "cancelled", "blocked", "waiting_user", "paused"}


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha_tree(path: Path) -> str:
    if path.is_file():
        return sha_file(path)
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(bytes.fromhex(sha_file(item)))
    return digest.hexdigest()


def suite() -> dict:
    return json.loads(SUITE.read_text(encoding="utf-8"))


def task_spec(task_id: str) -> dict:
    data = suite()
    for task in data["tasks"]:
        if task["id"] == task_id:
            return {**task, "frozen": data["frozen"], "suite_version": data["suite_version"]}
    raise SystemExit(f"unknown task {task_id}")


def record_path(task_id: str) -> Path:
    RECORDS.mkdir(parents=True, exist_ok=True)
    return RECORDS / f"task-{task_id}.json"


def load_record(task_id: str) -> dict | None:
    path = record_path(task_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def save_record(record: dict) -> None:
    record_path(record["task"]).write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


def input_paths(spec: dict) -> list[Path]:
    return [ROOT / item for item in spec.get("inputs", [])]


def summarise_events(events: list[dict]) -> dict:
    stages = []
    actions: list[dict] = []
    tools: list[str] = []
    requests = 0
    for event in events:
        kind, payload = event.get("type") or event.get("kind"), event.get("payload") or {}
        if kind == "model.request_started":
            requests += 1
            stages.append({"at": event["at"], "kind": kind, "cumulative": payload.get("cumulative_requests")})
        elif kind in {"model.stream_opened", "model.request_finished", "model.request_timed_out"}:
            stages.append({"at": event["at"], "kind": kind, **{k: v for k, v in payload.items()
                          if k in {"headers_seconds", "first_fragment_seconds", "total_seconds",
                                   "fragments", "layer", "input_tokens", "output_tokens"}}})
        elif kind == "action.started":
            tool = payload.get("tool", "")
            tools.append(tool)
            actions.append({"at": event["at"], "tool": tool, "arguments": payload.get("arguments")})
    return {"requests": requests, "stages": stages, "tools": tools, "actions": actions}


def wait_task(client: LocalClient, task_id: str, budget: dict, followups: list[str]) -> dict:
    started = time.monotonic()
    stop_reason = None
    cursor = 0
    seen = 0
    all_events: list[dict] = []
    while True:
        state = client.request("GET", f"/tasks/{task_id}")
        page = client.request("GET", f"/tasks/{task_id}/events", params={"format": "json", "cursor": cursor})
        if page:
            all_events.extend(page)
            cursor = page[-1]["sequence"]
        summary = summarise_events(all_events)
        elapsed = time.monotonic() - started
        status = state["status"]
        if status in TERMINAL:
            break
        if summary["requests"] > budget["model_requests"]:
            stop_reason = "model_request_budget"
        elif len(summary["actions"]) > budget["tool_actions"]:
            stop_reason = "tool_action_budget"
        elif elapsed > budget["seconds"]:
            stop_reason = "wall_budget"
        if stop_reason:
            try:
                client.request("POST", f"/tasks/{task_id}/pause", {"request_id": str(uuid4()),
                                                               "expected_state_version": state["state_version"]})
            except Failure as error:
                stop_reason += f" (pause failed: {error.code})"
            break
        if status in {"waiting_user", "paused"} and followups:
            break
        time.sleep(2)
    return {"state": state, "events": all_events, "stop_reason": stop_reason, "elapsed": time.monotonic() - started}


NUM = re.compile(r"-?\d+(?:\.\d+)?")


def answer_text(state: dict) -> str:
    messages = state.get("messages") or []
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return message.get("text", "") or ""
    return ""


def input_has_image(workspace_root: Path, task_id: str) -> tuple[bool, str]:
    """A single image attachment is sent inline; verify it reached the model input."""
    database = workspace_root / ".forestry" / "state.sqlite"
    if not database.is_file():
        return False, "state database missing"
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("select payload from contexts where task_id=?", (task_id,)).fetchall()
    finally:
        connection.close()
    for (payload,) in rows:
        value = json.loads(payload)
        prepared = value.get("prepared_input_ref")
        if not prepared or not Path(prepared).is_file():
            continue
        body = Path(prepared).read_text(encoding="utf-8", errors="ignore")
        compact = body.replace(" ", "")
        if ('"image_url"' in body or '"input_image"' in body or "data:image/" in body
                or '"media_type":"image/' in compact):
            return True, f"image part present in {Path(prepared).name}"
    return False, "no image part found in prepared model inputs"


def normalise(text: str) -> str:
    """去掉数字千分位，避免 4935083 与 4,935,083 被当成不同结论。"""
    return re.sub(r"(?<=\d)[,，_](?=\d{3}\b)", "", text or "")


def artifact_texts(workspace_root: Path | None, task_id: str, pattern: str) -> list[str]:
    """按文件名正则找出该任务产物原文，用于校验交付文件本身而不是回答里的措辞。"""
    if workspace_root is None:
        return []
    base = Path(workspace_root) / "tasks" / task_id
    if not base.is_dir():
        return []
    texts = []
    for path in base.rglob("*"):
        if path.is_file() and re.search(pattern, path.name, re.I):
            try:
                texts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return texts


def run_checks(spec: dict, state: dict, events: list[dict], artifacts: list[dict], jobs: list[dict],
               baseline: dict, before_inputs: dict, workspace_root: Path | None = None) -> list[dict]:
    answer = normalise(answer_text(state))
    summary = summarise_events(events)
    names = [a.get("name", "") for a in artifacts]
    results = []
    for check in spec.get("checks", []):
        kind = check["type"]
        passed, detail = False, ""
        if kind == "status_is":
            passed, detail = state["status"] == check["value"], f"status={state['status']}"
        elif kind == "status_in":
            passed, detail = state["status"] in check["values"], f"status={state['status']}"
        elif kind == "answer_min_chars":
            passed, detail = len(answer) >= check["value"], f"chars={len(answer)}"
        elif kind == "answer_contains_all":
            missing = [v for v in check["values"] if v.lower() not in answer.lower()]
            passed, detail = not missing, f"missing={missing}"
        elif kind == "answer_contains_any":
            found = [v for v in check["values"] if v.lower() in answer.lower()]
            passed, detail = bool(found), f"found={found}"
        elif kind == "answer_not_contains":
            found = [v for v in check["values"] if v.lower() in answer.lower()]
            passed, detail = not found, f"found={found}"
        elif kind == "answer_numbers_all":
            numbers = [float(x) for x in NUM.findall(answer)]
            tolerance = float(check.get("tolerance", 0))
            missing = [v for v in check["values"] if not any(abs(n - v) <= tolerance for n in numbers)]
            passed, detail = not missing, f"missing={missing} numbers={numbers[:12]}"
        elif kind == "artifact_name":
            hits = [n for n in names if re.search(check["pattern"], n, re.I)]
            passed, detail = len(hits) >= check.get("min", 1), f"hits={hits}"
        elif kind == "artifact_text_all":
            # 验收对象是交付文件本身：有的任务必须在回答里说明"删掉了什么"，
            # 用回答正文做否定匹配会把正确的说明判成未删。
            texts = [normalise(t) for t in artifact_texts(workspace_root, state["task_id"], check["name"])]
            missing = [v for v in check["values"] if not any(normalise(v).lower() in t.lower() for t in texts)]
            passed, detail = (bool(texts) and not missing), f"files={len(texts)} missing={missing}"
        elif kind == "artifact_text_not_asserted":
            # 交付文件里出现某结论不等于断言它：正确的修正报告会引用旧结论并否定。
            # 只有当某次出现附近没有任何否定措辞时，才判为“仍在断言”。
            texts = [normalise(t) for t in artifact_texts(workspace_root, state["task_id"], check["name"])]
            negations = check.get("negations") or ["无法得出", "不能", "无依据", "未经验证", "不足以",
                                                   "没有依据", "不支持", "不宜", "删除", "不成立"]
            asserted = []
            for text in texts:
                for value in check["values"]:
                    start = text.lower().find(value.lower())
                    while start >= 0:
                        window = text[max(0, start - 80): start + len(value) + 80]
                        if not any(marker in window for marker in negations):
                            asserted.append(value)
                            break
                        start = text.lower().find(value.lower(), start + 1)
            passed, detail = (bool(texts) and not asserted), f"files={len(texts)} asserted={asserted}"
        elif kind == "input_has_image":
            passed, detail = input_has_image(workspace_root, state["task_id"])
        elif kind == "actions_used":
            # 契约里写的是模型侧工具名（code_execute），事件里记录的是域工具名（code.execute）。
            seen = {t.replace(".", "_") for t in summary["tools"]}
            missing = [t for t in check["values"] if t.replace(".", "_") not in seen]
            passed, detail = not missing, f"missing={missing} tools={summary['tools']}"
        elif kind == "actions_not_used":
            seen = {t.replace(".", "_") for t in summary["tools"]}
            hit = [t for t in check["values"] if t.replace(".", "_") in seen]
            passed, detail = not hit, f"hit={hit}"
        elif kind == "jobs_status":
            bad = [j.get("status") for j in jobs if j.get("status") not in check["allowed"]]
            passed, detail = (not jobs and False) or (not bad), f"jobs={[j.get('status') for j in jobs]}"
            if not jobs:
                passed, detail = False, "no jobs were created"
        elif kind == "no_jobs":
            passed, detail = not jobs, f"jobs={[j.get('status') for j in jobs]}"
        elif kind == "input_unchanged":
            changed = []
            for path_text, digest in before_inputs.items():
                current = sha_tree(ROOT / path_text)
                if current != digest:
                    changed.append(path_text)
            passed, detail = not changed, f"changed={changed}"
        else:
            detail = f"unknown check {kind}"
        results.append({"check": check, "passed": passed, "detail": detail})
    return results


def job_list(client: LocalClient, task_id: str) -> list[dict]:
    try:
        value = client.request("GET", f"/tasks/{task_id}/jobs")
        return value.get("jobs", []) if isinstance(value, dict) else value
    except Failure:
        return []


def build_record(spec: dict, task_id: str, model: str, harness: str, before_inputs: dict,
                 baseline: dict, started: str) -> dict:
    client = LocalClient(load_config(ROOT / "config" / "app.research.yaml"))
    try:
        state = client.request("GET", f"/tasks/{task_id}")
        events = collect_events(client.http, task_id)
        artifacts = client.request("GET", f"/tasks/{task_id}/artifacts")
        jobs = job_list(client, task_id)
    finally:
        client.close()
    summary = summarise_events(events)
    checks = run_checks(spec, state, events, artifacts, jobs, baseline, before_inputs,
                        load_config(ROOT / "config" / "app.research.yaml").workspace_root)
    usage = state.get("last_usage") or {}
    all_passed = all(c["passed"] for c in checks)
    answered = state["status"] in {"completed", "waiting_user"} and not state.get("blockers")
    if answered and all_passed:
        verdict = "completed"
    elif answered:
        verdict = "partial"
    elif state["status"] in {"paused", "waiting_user"}:
        verdict = "failed" if state.get("blockers") else "blocked"
    else:
        verdict = state["status"]
    record = {
        "suite_version": spec["suite_version"],
        "task": spec["id"],
        "title": spec["title"],
        "frozen": {"model": model, "harness": harness, "repeats": 1, "budget": spec["frozen"]["budget"]},
        "task_id": task_id,
        "started": started,
        "updated": now(),
        "status": state["status"],
        "blockers": state.get("blockers"),
        "verdict": verdict,
        "answer": answer_text(state),
        "metrics": {
            "requests": summary["requests"],
            "tool_actions": len(summary["actions"]),
            "tools": summary["tools"],
            "last_usage": usage,
            "wall_seconds": baseline.get("elapsed"),
            "stop_reason": baseline.get("stop_reason"),
        },
        "stages": summary["stages"],
        "actions": summary["actions"],
        "jobs": [{"job_id": j.get("job_id"), "status": j.get("status")} for j in jobs],
        "artifacts": [{"name": a.get("name"), "sha256": a.get("sha256"), "size": a.get("size_bytes")}
                      for a in artifacts],
        "checks": checks,
        "manual": spec.get("manual", []),
        "inputs": before_inputs,
    }
    save_record(record)
    return record


def print_record(record: dict) -> None:
    print(f"\n[{record['task']}] {record['title']}  verdict={record['verdict']}  status={record['status']}")
    print(f"  requests={record['metrics']['requests']} actions={record['metrics']['tool_actions']} "
          f"wall={round(record['metrics']['wall_seconds'] or 0, 1)}s stop={record['metrics']['stop_reason']}")
    print(f"  tools={record['metrics']['tools']}")
    print(f"  usage={record['metrics']['last_usage']}")
    for check in record["checks"]:
        flag = "PASS" if check["passed"] else "FAIL"
        print(f"  {flag} {check['check']['type']}: {check['detail']}")
    if record["manual"]:
        print("  manual review:")
        for item in record["manual"]:
            print(f"    - {item}")
    print("  answer head:", (record["answer"] or "")[:220].replace("\n", " "))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="research")
    parser.add_argument("--config", default="config/app.research.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    run = commands.add_parser("run")
    run.add_argument("--task", required=True)
    run.add_argument("--model", default=None, help="默认取冻结值；small 仅用于协议核对")
    run.add_argument("--harness", default=None)
    send = commands.add_parser("send")
    send.add_argument("--task", required=True)
    send.add_argument("--text", default=None)
    record = commands.add_parser("record")
    record.add_argument("--task", required=True)
    adopt = commands.add_parser("adopt")
    adopt.add_argument("--task", required=True)
    adopt.add_argument("--task-id", required=True)
    adopt.add_argument("--note", default="")
    args = parser.parse_args()

    if args.command == "list":
        for item in suite()["tasks"]:
            existing = load_record(item["id"])
            mark = f" -> {existing['verdict']}" if existing else ""
            print(f"{item['id']}  {item['title']}{mark}")
        return

    config_path = ROOT / args.config
    if args.command == "adopt":
        spec = task_spec(args.task)
        client = LocalClient(load_config(config_path))
        try:
            state = client.request("GET", f"/tasks/{args.task_id}")
            events = collect_events(client.http, args.task_id)
            before_inputs = {}
            for path in input_paths(spec):
                if path.exists():
                    before_inputs[path.relative_to(ROOT).as_posix()] = sha_tree(path)
            times = [e["at"] for e in events if e.get("type") in {"run.started", "model.request_started"}]
            elapsed = None
            if times:
                first = datetime.fromisoformat(times[0])
                last = datetime.fromisoformat(events[-1]["at"])
                elapsed = (last - first).total_seconds()
            fresh = build_record(spec, args.task_id, spec["frozen"]["model"], spec["frozen"]["harness"],
                                 before_inputs, {"elapsed": elapsed, "stop_reason": None}, times[0] if times else now())
            fresh["note"] = args.note
            save_record(fresh)
            print_record(fresh)
        finally:
            client.close()
        return

    if args.command == "record":
        spec = task_spec(args.task)
        existing = load_record(args.task)
        if not existing:
            raise SystemExit("no record yet")
        fresh = build_record(spec, existing["task_id"], existing["frozen"]["model"], existing["frozen"]["harness"],
                             existing["inputs"], existing["metrics"], existing["started"])
        print_record(fresh)
        return

    client = LocalClient(load_config(config_path))
    try:
        if args.command == "run":
            spec = task_spec(args.task)
            model = args.model or spec["frozen"]["model"]
            harness = args.harness or spec["frozen"]["harness"]
            refs = []
            before_inputs = {}
            for path in input_paths(spec):
                if not path.exists():
                    raise SystemExit(f"missing input {path}")
                resource = client.request("POST", "/resources/register", {"request_id": str(uuid4()),
                    "path": str(path), "classification": "private", "read_only": True})
                refs.append(resource["resource_id"])
                before_inputs[path.relative_to(ROOT).as_posix()] = sha_tree(path)
            started = now()
            state = client.request("POST", "/tasks", {"request_id": f"research-{spec['id']}-{uuid4()}",
                "objective": spec["objective"], "input_refs": refs, "model_profile": model,
                "harness_mode": harness, "output_preferences": {"report_language": "zh"}})
            task_id = state["task_id"]
            print(f"task {task_id} started for {spec['id']} ({model}/{harness})")
            outcome = wait_task(client, task_id, spec["frozen"]["budget"], spec.get("followups", []))
            print(f"stopped: status={outcome['state']['status']} reason={outcome['stop_reason']} "
                  f"elapsed={round(outcome['elapsed'], 1)}s")
            pending = list(spec.get("followups", []))
            while pending and outcome["state"]["status"] in {"waiting_user", "paused"} and not outcome["stop_reason"]:
                text = pending.pop(0)
                current = client.request("GET", f"/tasks/{task_id}")
                client.request("POST", f"/tasks/{task_id}/messages", {"request_id": str(uuid4()), "text": text,
                                                                     "expected_spec_revision": current["spec_revision"]})
                print(f"follow-up sent: {text[:30]}…")
                outcome = wait_task(client, task_id, spec["frozen"]["budget"], pending)
                print(f"stopped: status={outcome['state']['status']} reason={outcome['stop_reason']}")
            record = build_record(spec, task_id, model, harness, before_inputs,
                                  {"elapsed": outcome["elapsed"], "stop_reason": outcome["stop_reason"]}, started)
            print_record(record)
        elif args.command == "send":
            spec = task_spec(args.task)
            existing = load_record(args.task) or {}
            task_id = existing.get("task_id") or sys.exit("no task id recorded; run first")
            current = client.request("GET", f"/tasks/{task_id}")
            text = args.text or (spec.get("followups") or sys.exit("no follow-up text"))[0]
            client.request("POST", f"/tasks/{task_id}/messages", {"request_id": str(uuid4()), "text": text,
                                                                 "expected_spec_revision": current["spec_revision"]})
            outcome = wait_task(client, task_id, spec["frozen"]["budget"], [])
            record = build_record(spec, task_id, existing["frozen"]["model"], existing["frozen"]["harness"],
                                  existing.get("inputs", {}), {"elapsed": outcome["elapsed"],
                                  "stop_reason": outcome["stop_reason"]}, existing.get("started", now()))
            print_record(record)
    finally:
        client.close()


if __name__ == "__main__":
    main()
