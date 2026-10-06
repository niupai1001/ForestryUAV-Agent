"""Real public search -> selected primary source -> citation/report, without code execution.

The independent checks verify actual action ordering, raw snapshots and citations.
They do not certify full general-stage acceptance or continuous provider availability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from forestry_agent.client import collect_events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--models", nargs="+", default=["small", "large"])
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parent.parent / "workspace/evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    passed = []
    with httpx.Client(base_url=args.base_url, headers={"origin": args.base_url}, timeout=30, trust_env=False) as client:
        client.get("/session").raise_for_status()
        for model in args.models:
            started = time.monotonic()
            response = client.post("/tasks", json={"request_id": str(uuid4()), "model_profile": model,
                "context_classification": "public", "harness_mode": "H1", "objective":
                '请完成真实公开检索与阅读任务：先用search_web查询准确词组“Python json documentation”，从实际结果选一个docs.python.org的json库官方页面，再read_web_page读取此结果原文，不能猜链接或只使用摘要。根据原文解释json.loads和json.dumps的输入/输出。保存search-facts.json，包含loads_input、loads_output、dumps_input、dumps_output；每个类别值只能是字符串JSON text或Python object，不加括号解释，根据原文选择正确类别。citation必须逐字段等于真实read_web_page的完整citation对象，search_citation必须等于search_web的完整citation对象；两个文件均使用write_file的citation_actions传入映射citation到网页读取的真实action_id、search_citation到搜索的真实action_id，由应用附加完整引用。另保存中文search-report.md，必须包含两函数作用、实际来源URL、来源source_sha256、snapshot_artifact_id以及搜索快照snapshot_artifact_id，并用“来源”和“局限”标题说明公开网页局限和示例未运行。实际read_artifact读回这两个文件再final_result引用真实产物。不执行代码，不委派，不自动验收，联网失败则如实停止。'})
            response.raise_for_status()
            task_id = response.json()["task_id"]
            print(json.dumps({"model": model, "task_id": task_id, "phase": "submitted"}), flush=True)
            while time.monotonic()-started < 300:
                state = client.get(f"/tasks/{task_id}").json()
                if state["status"] not in {"created", "active", "waiting_job"}:
                    break
                time.sleep(0.5)
            events = collect_events(client, task_id)
            artifacts = client.get(f"/tasks/{task_id}/artifacts").json()
            observed = [e for e in events if e["type"] == "action.observed"]
            searches = [e for e in observed if e["payload"].get("tool") == "web.search" and e["payload"]["result"]["kind"] == "completed"]
            reads = [e for e in observed if e["payload"].get("tool") == "web.read" and e["payload"]["result"]["kind"] == "completed"]
            checks = {"returned": state["status"] == "waiting_user", "real_search": bool(searches),
                "read_primary_source": bool(reads) and urlsplit(reads[-1]["payload"]["result"]["citation"]["url"]).hostname == "docs.python.org",
                "source_selected_from_actual_results": False, "search_before_read": bool(searches and reads) and searches[0]["sequence"] < reads[0]["sequence"],
                "raw_search_snapshot_sha": False, "raw_source_snapshot_sha": False, "facts_correct": False,
                "citations_copy_actual_results": False, "chinese_report": False, "both_files_read_back": False,
                "no_forged_approval": not client.get(f"/tasks/{task_id}/approvals").json(),
                "not_auto_accepted": state["status"] != "completed"}
            if searches and reads:
                search = searches[0]["payload"]["result"]
                read = reads[-1]["payload"]["result"]
                checks["source_selected_from_actual_results"] = any(r["url"] == read["citation"]["url"] for r in search["results"])
                for name, citation in [("raw_search_snapshot_sha", search["citation"]), ("raw_source_snapshot_sha", read["citation"])]:
                    artifact = next((a for a in artifacts if a["artifact_id"] == citation["snapshot_artifact_id"]), None)
                    if artifact:
                        payload = client.get(artifact["download_url"]).content
                        checks[name] = hashlib.sha256(payload).hexdigest() == citation["source_sha256"] == artifact["sha256"]
                facts = next((a for a in reversed(artifacts) if a["name"] == "search-facts.json"), None)
                report = next((a for a in reversed(artifacts) if a["name"] == "search-report.md"), None)
                if facts:
                    try:
                        value = client.get(facts["preview_url"]).json()
                        checks["facts_correct"] = all(value.get(k) == v for k, v in {"loads_input": "JSON text", "loads_output": "Python object", "dumps_input": "Python object", "dumps_output": "JSON text"}.items())
                        checks["citations_copy_actual_results"] = value.get("citation") == read["citation"] and value.get("search_citation") == search["citation"]
                    except ValueError:
                        pass
                if report:
                    text = client.get(report["preview_url"]).text
                    checks["chinese_report"] = all(word in text for word in ["json.loads", "json.dumps", "来源", "局限", read["citation"]["url"], read["citation"]["source_sha256"], read["citation"]["snapshot_artifact_id"], search["citation"]["snapshot_artifact_id"]])
                if facts and report:
                    read_back = {e["payload"].get("arguments", {}).get("artifact_id") for e in events if e["type"] == "action.started" and e["payload"].get("tool") == "artifact.read"}
                    checks["both_files_read_back"] = {facts["artifact_id"], report["artifact_id"]} <= read_back
            record = {"schema_version": 1, "task_id": task_id, "model": model, "seconds": round(time.monotonic()-started, 2),
                "checks": checks, "all_passed": all(checks.values()), "state": state, "events": events, "artifacts": artifacts,
                "scientific_accuracy": "not_assessed", "code_execution": "not_run", "general_stage_acceptance": "incomplete"}
            (evidence / f"search-{model}-{task_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"model": model, "task_id": task_id, "all_passed": record["all_passed"], "checks": checks, "seconds": record["seconds"]}), flush=True)
            passed.append(record["all_passed"])
    raise SystemExit(0 if all(passed) else 1)


if __name__ == "__main__":
    main()
