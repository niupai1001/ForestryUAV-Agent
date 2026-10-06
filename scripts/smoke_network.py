"""Real model/source reader evidence; scripted approvals contain only public fixture URLs.

This checks permission mechanics, not actual human consent or search availability.
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
from forestry_agent.contracts import Failure
from forestry_agent.network import PublicHTTP, decode_text, search_request, search_results


def wait(client, task_id, deadline):
    while time.monotonic() < deadline:
        state = client.get(f"/tasks/{task_id}").json()
        if state["status"] not in {"created", "active", "waiting_job"}:
            return state
        time.sleep(0.5)
    return client.get(f"/tasks/{task_id}").json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--models", nargs="+", default=["small", "large"])
    parser.add_argument("--pending-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    evidence = root / "workspace/evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    page_url = "https://www.python.org/"
    outcomes = []
    with httpx.Client(base_url=args.base_url, headers={"origin": args.base_url}, timeout=30, trust_env=False) as client:
        client.get("/session").raise_for_status()
        for model in args.models:
            started = time.monotonic()
            response = client.post("/tasks", json={"request_id": str(uuid4()), "model_profile": model,
                "harness_mode": "H1", "context_classification": "private", "objective":
                f"这是公开软件网页的权限机制测试，不含真实私密数据。请只用 read_web_page 实际读取 {page_url}，未经审批时立即调用final_result说明等待审批，不反复申请。批准后继续实际读取，然后write_file保存中文network-report.md，明确网页标题或主题、真实URL、source_sha256、snapshot_artifact_id及网页来源的局限。保存后read_artifact读回。最终引用真实产物，不执行代码、不搜索、不自动验收。"})
            response.raise_for_status()
            task_id = response.json()["task_id"]
            print(json.dumps({"model": model, "task_id": task_id, "phase": "proposed"}), flush=True)
            state = wait(client, task_id, started+300)
            approvals = client.get(f"/tasks/{task_id}/approvals").json()
            initial_events = collect_events(client, task_id)
            initial_checks = {"pending_exact_public_url": len(approvals) == 1 and approvals[0]["status"] == "pending" and approvals[0]["request"]["value"] == page_url,
                "returned_for_approval": state["status"] == "waiting_user" and any(b["code"] == "APPROVAL_REQUIRED" for b in state["blockers"]),
                "no_web_snapshot_before_approval": not any(e["type"] == "action.observed" and e["payload"].get("result", {}).get("citation") for e in initial_events)}
            record = {"schema_version": 1, "task_id": task_id, "model": model, "initial_checks": initial_checks,
                "initial_state": state, "initial_events": initial_events, "approvals_before": approvals,
                "approval_actor": "scripted_local_operator_for_public_fixture_only", "not_scientific_evidence": True}
            if not args.pending_only and all(initial_checks.values()):
                approval = approvals[0]
                decision = {"request_id": str(uuid4()), "expected_spec_revision": state["spec_revision"],
                    "request_sha256": approval["request_sha256"], "verdict": "approve"}
                approved = client.post(f"/tasks/{task_id}/approvals/{approval['approval_id']}", json=decision)
                approved.raise_for_status()
                assert client.post(f"/tasks/{task_id}/approvals/{approval['approval_id']}", json=decision).json() == approved.json()
                state = wait(client, task_id, time.monotonic()+300)
                events = collect_events(client, task_id)
                web = [e["payload"]["result"] for e in events if e["type"] == "action.observed" and e["payload"].get("tool") == "web.read" and e["payload"]["result"]["kind"] == "completed"]
                artifacts = client.get(f"/tasks/{task_id}/artifacts").json()
                checks = {"returned": state["status"] == "waiting_user", "exactly_one_successful_fetch": len(web) == 1,
                    "consumed_once": len(client.get(f"/tasks/{task_id}/approvals").json()) == 1 and client.get(f"/tasks/{task_id}/approvals").json()[0]["status"] == "consumed",
                    "snapshot_sha_matches_actual_bytes": False, "report_cites_snapshot": False, "report_read_back": False,
                    "native_continuation": bool(approved.json().get("continuation_turn_id")), "not_auto_accepted": state["status"] != "completed"}
                if len(web) == 1:
                    citation = web[0]["citation"]
                    raw = next((a for a in artifacts if a["artifact_id"] == citation["snapshot_artifact_id"]), None)
                    if raw:
                        payload = client.get(raw["download_url"]).content
                        checks["snapshot_sha_matches_actual_bytes"] = hashlib.sha256(payload).hexdigest() == citation["source_sha256"] == raw["sha256"] and b"Python" in payload
                    reports = [a for a in artifacts if a["name"] == "network-report.md"]
                    if reports:
                        latest = reports[-1]
                        text = client.get(latest["preview_url"]).text
                        checks["report_cites_snapshot"] = all(value in text for value in [citation["url"], citation["source_sha256"], citation["snapshot_artifact_id"], "Python"]) and any(word in text for word in ["局限", "限制"])
                        checks["report_read_back"] = any(e["type"] == "action.started" and e["payload"].get("tool") == "artifact.read" and e["payload"].get("arguments", {}).get("artifact_id") == latest["artifact_id"] for e in events)
                record.update(checks=checks, state=state, events=events, approval_resolution=approved.json(), artifacts=artifacts)
            record["seconds"] = round(time.monotonic()-started, 2)
            record["all_passed"] = all(initial_checks.values()) and (args.pending_only or all(record.get("checks", {"not_run": False}).values()))
            (evidence / f"network-{model}-{task_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"model": model, "task_id": task_id, "all_passed": record["all_passed"], "checks": record.get("checks", initial_checks), "seconds": record["seconds"]}), flush=True)
            outcomes.append(record["all_passed"])
    if not args.pending_only:
        observation = {"provider": "bing_rss", "query": "OpenDroneMap", "not_model_workflow_evidence": True}
        try:
            url, origins = search_request("bing_rss", "https://www.bing.com", observation["query"])
            response = PublicHTTP(timeout_seconds=10).get(url, allowed_origins=origins)
            results = search_results("bing_rss", decode_text(response.pop("body"), response["headers"].get("content-type", "")))
            observation.update(status="available" if results else "empty_not_verified", results=results, response=response)
        except (Failure, OSError, ValueError) as error:
            observation.update(status="failed", code=getattr(error, "code", type(error).__name__), message=str(error))
        (evidence / f"search-probe-{uuid4().hex}.json").write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"search_probe": observation["status"]}), flush=True)
    raise SystemExit(0 if all(outcomes) else 1)


if __name__ == "__main__":
    main()
