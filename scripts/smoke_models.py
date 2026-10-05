"""Real local models, real registered input, real artifacts and independent numerical checks."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--models", nargs="+", default=["small", "large"])
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    inputs = root / "fixtures" / "general"
    inputs.mkdir(parents=True, exist_ok=True)
    fixture = inputs / "heights.csv"
    fixture.write_text("tree_id,height_m\nT001,8.25\nT002,12.50\nT003,16.75\n", encoding="utf-8")
    evidence = root / "workspace" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    outcomes = []
    with httpx.Client(base_url=args.base_url, timeout=30, trust_env=False,
                      headers={"origin": args.base_url}) as client:
        client.get("/session").raise_for_status()
        resource = client.post("/resources/register", json={"request_id":str(uuid4()),
            "path":str(fixture),"classification":"private","read_only":True})
        resource.raise_for_status()
        resource_id = resource.json()["resource_id"]
        original = fixture.read_bytes()
        for model in args.models:
            start = time.monotonic()
            response = client.post("/tasks", json={"request_id":str(uuid4()), "model_profile":model,
                "input_refs":[resource_id], "objective":
                "实际读取已授权 heights.csv。计算记录数、height_m 总和与算术平均值。"
                "保存 result.json，字段必须是 tree_count、total_height_m、mean_height_m；"
                "另保存中文 report.md，说明输入来源、计算结果与不代表现场精度。"
                "用工具检查产物，最终返回这两个真实产物的 artifact_ids。不要运行代码。"})
            response.raise_for_status()
            task_id = response.json()["task_id"]
            print(json.dumps({"model":model,"task_id":task_id,"status":"submitted"}), flush=True)
            while time.monotonic() - start < args.timeout:
                state = client.get(f"/tasks/{task_id}").json()
                if state["status"] not in {"created","active","waiting_job"}:
                    break
                time.sleep(1)
            artifacts = client.get(f"/tasks/{task_id}/artifacts").json()
            checks = {"source_unchanged":fixture.read_bytes() == original,
                      "model_returned":state["status"]=="waiting_user", "numeric_result":False,
                      "report_exists":False,"real_read":False,"real_write":False}
            for artifact in artifacts:
                content = client.get(artifact["preview_url"])
                content.raise_for_status()
                if artifact["name"] == "result.json":
                    numbers = json.loads(content.text)
                    checks["numeric_result"] = numbers == {
                        "tree_count":3,"total_height_m":37.5,"mean_height_m":12.5}
                if artifact["name"] == "report.md":
                    checks["report_exists"] = "12.5" in content.text and bool(re.search("[\u4e00-\u9fff]", content.text))
            events = client.get(f"/tasks/{task_id}/events", params={"format":"json"}).json()
            checks["real_read"] = any(e["type"]=="action.observed" and e["payload"].get("tool")=="fs.read"
                                      and e["payload"]["result"]["kind"]=="completed" for e in events)
            checks["real_write"] = any(e["type"]=="action.observed" and e["payload"].get("tool")=="fs.write_workspace"
                                      and e["payload"]["result"]["kind"]=="completed" for e in events)
            outcome = {"model":model,"task_id":task_id,"seconds":round(time.monotonic()-start,2),
                       "checks":checks,"passed":all(checks.values()),"state":state,"events":events,
                       "artifacts":artifacts}
            outcomes.append(outcome)
            (evidence/f"model-{model}-{task_id}.json").write_text(json.dumps(outcome,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps({k:outcome[k] for k in ["model","task_id","seconds","checks","passed"]}),flush=True)
    (evidence/"model-smoke-latest.json").write_text(json.dumps(outcomes,ensure_ascii=False,indent=2),encoding="utf-8")
    raise SystemExit(0 if all(o["passed"] for o in outcomes) else 1)


if __name__ == "__main__":
    main()
