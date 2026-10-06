"""Create real-model repeated-read trials; preserve paused tasks for UI recovery.

This deliberately requests repeated reads to exercise the configured guard.
It does not measure ordinary task quality or scientific accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from forestry_agent.client import collect_events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--models", nargs="+", default=["small", "large"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    fixture = root / "fixtures/general/heights.csv"
    original = fixture.read_bytes()
    directory = root / "workspace/evidence"
    directory.mkdir(parents=True, exist_ok=True)
    outcomes = []
    with httpx.Client(base_url=args.base_url, headers={"origin": args.base_url}, timeout=30, trust_env=False) as client:
        client.get("/session").raise_for_status()
        response = client.post("/resources/register", json={"request_id": str(uuid4()),
            "path": str(fixture), "classification": "public", "read_only": True})
        response.raise_for_status()
        resource_id = response.json()["resource_id"]
        for model in args.models:
            started = time.monotonic()
            response = client.post("/tasks", json={"request_id": str(uuid4()), "model_profile": model,
                "harness_mode": "H1", "context_classification": "public", "input_refs": [resource_id],
                "objective": "这是无进展保护的软件故障测试。请连续六次真实调用read_file读取同一个已授权资源"
                    + resource_id + "，relative_path为空字符串、offset=0。每次只发起一个工具调用并等待结果；"
                    "即使结果相同也再次读取。不要写文件、不要换工具、不要委派、不要联网；"
                    "不要在六次读取之前提交final_result。应用的实际资源限制优先，若工具拒绝须如实停止。"})
            response.raise_for_status()
            task_id = response.json()["task_id"]
            print(json.dumps({"model": model, "task_id": task_id, "phase": "submitted"}), flush=True)
            while True:
                state = client.get(f"/tasks/{task_id}").json()
                if state["status"] not in {"created", "active", "waiting_job"} or time.monotonic()-started > 300:
                    break
                time.sleep(0.5)
            events = collect_events(client, task_id)
            checks = {"paused_by_guard": state["status"] == "paused" and any(b["code"] == "NO_PROGRESS" for b in state["blockers"]),
                "actual_reads": sum(e["type"] == "action.observed" and e["payload"].get("tool") == "fs.read"
                    and e["payload"]["result"]["kind"] == "completed" for e in events) == 4,
                "durable_limit": any(e["type"] == "progress.observed" and e["payload"]["stalled"] == e["payload"]["limit"] == 3 for e in events),
                "source_unchanged": fixture.read_bytes() == original,
                "not_auto_accepted": state["status"] != "completed"}
            record = {"schema_version": 1, "model": model, "task_id": task_id, "checks": checks,
                "all_passed": all(checks.values()), "state": state, "events": events,
                "source_sha256": hashlib.sha256(original).hexdigest(), "seconds": round(time.monotonic()-started, 2),
                "scientific_accuracy": "not_assessed", "general_stage_acceptance": "incomplete"}
            (directory / f"progress-{model}-{task_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"model": model, "task_id": task_id, "checks": checks, "all_passed": record["all_passed"]}), flush=True)
            outcomes.append(record["all_passed"])
            if state["status"] in {"created", "active", "waiting_job"}:
                break  # Observation timeouts never restart or cancel the live task.
    raise SystemExit(0 if all(outcomes) else 1)


if __name__ == "__main__":
    main()
