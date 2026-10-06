"""Real local-model delegation, independent source/result and isolation checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

import httpx

from forestry_agent.client import collect_events
from forestry_agent.resources import native_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--models", nargs="+", default=["small"])
    parser.add_argument("--follow-up-task", help="Retain an existing failed report check and audit a bounded follow-up separately.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    source = root / "fixtures/general/role-note.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    if not args.follow_up_task:
        source.write_text("项目代号：青杉42。演示值：18.75米。\n此内容是软件验收夹具，不是现场观测或科学精度证据。\n", encoding="utf-8")
    original = source.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    evidence = root / "workspace/evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    outcomes = []
    with httpx.Client(base_url=args.base_url, headers={"origin": args.base_url}, timeout=30, trust_env=False) as client:
        client.get("/session").raise_for_status()
        prior = None
        if args.follow_up_task:
            response = client.get(f"/tasks/{args.follow_up_task}")
            response.raise_for_status()
            prior = response.json()
            args.models = [prior["model_profile"]]
            ref = prior["input_refs"][0]
        else:
            response = client.post("/resources/register", json={"request_id": str(uuid4()), "path": str(source)})
            response.raise_for_status()
            ref = response.json()["resource_id"]
        for profile in args.models:
            marker = "仅父级保留的内部标记_" + uuid4().hex
            objective = f"{marker}。请完成两个有界独立委派，然后整合报告。先委派 data，仅传已选资源 {ref}，objective 中明确要求实际 read_document 读取文件，保存 data-result.json：project_code 是项目代号字符串，demo_value 是 JSON 数字，source_ref 是包含 resource_id、source_sha256、unit 的 JSON 对象，逐字复制 read_document 的真实 citation 对象，不能使用版本字符串替代。再委派 quality，授予同一个输入资源及 data 实际产物 ID，objective 中明确要求用 read_document 独立读取来源，用 read_artifact 读 JSON，逐项核对代号、演示值和 source_ref 对象三个字段，保存 quality-report.md 并在 checks 给出观察。委派 task 参数为 AssignmentRequest JSON，必须转达上述工具、字段和检查判据，不能复制本父级目标或内部标记。只需各一个委派。你负责实际读回两个角色产物并写 parent-report.md，包含代号、数值、来源引用及软件夹具局限。最终调用 final_result，引用真实产物；不自动验收、不执行代码。"
            start = time.monotonic()
            if prior:
                marker = prior["objective"].split("。")[0]
                response = client.post(f"/tasks/{prior['task_id']}/messages", json={"request_id": str(uuid4()),
                    "expected_spec_revision": prior["spec_revision"], "text": "保持已有两个委派结果，不新增委派。实际读回它们，把 parent-report.md 改为完整中文报告，明确用‘来源’标题说明 resource_id、source_sha256 和 unit，保留代号青杉42、数值18.75、独立检查观察及软件夹具局限。使用 write_file 写入新版本，最终输出引用完整产物 ID。"})
            else:
                response = client.post("/tasks", json={"request_id": str(uuid4()), "objective": objective,
                    "input_refs": [ref], "model_profile": profile, "harness_mode": "H2"})
            response.raise_for_status()
            task_id = response.json()["task_id"]
            print(json.dumps({"model": profile, "task_id": task_id, "status": "submitted"}), flush=True)
            while time.monotonic() - start < 420:
                state = client.get(f"/tasks/{task_id}").json()
                if state["status"] not in {"created", "active", "waiting_job"}:
                    break
                time.sleep(1)
            assignments = client.get(f"/tasks/{task_id}/assignments").json()
            artifacts = client.get(f"/tasks/{task_id}/artifacts").json()
            events = collect_events(client, task_id)
            checks = {"source_unchanged": source.read_bytes() == original,
                "model_returned": state["status"] == "waiting_user", "not_auto_accepted": state["status"] != "completed",
                "two_roles": len(assignments) == 2 and {a["role_id"] for a in assignments} == {"data", "quality"},
                "settled_successfully": len(assignments) == 2 and all(a["status"] == "succeeded" for a in assignments),
                "isolated_runs": len({a["run_id"] for a in assignments}) == len(assignments) == 2,
                "scoped_outputs": False, "facts_correct": False, "citation_correct": False,
                "independent_quality_read": False, "parent_report": False, "child_context_isolated": False}
            if prior:
                checks["same_spec_revision"] = state["spec_revision"] == prior["spec_revision"]
            for artifact in artifacts:
                reply = client.get(artifact["preview_url"])
                reply.raise_for_status()
                if artifact["name"] == "data-result.json":
                    try:
                        data = reply.json()
                        checks["facts_correct"] = data.get("project_code") == "青杉42" and data.get("demo_value") == 18.75
                        pointer = data.get("source_ref", {})
                        checks["citation_correct"] = pointer.get("resource_id") == ref and pointer.get("source_sha256") == digest
                    except (ValueError, AttributeError):
                        pass
                if artifact["name"] == "parent-report.md":
                    checks["parent_report"] = all(word in reply.text for word in ["青杉42", "18.75", "来源", "夹具"])
            owned_outputs = []
            for assignment in assignments:
                refs = (assignment.get("result") or {}).get("artifact_ids", [])
                owned_outputs.append(bool(refs) and all(any(f["artifact_id"] == output and f["relative_path"].startswith(f"assignments/{assignment['assignment_id']}/") for f in artifacts) for output in refs))
            checks["scoped_outputs"] = len(owned_outputs) == 2 and all(owned_outputs)
            with sqlite3.connect(root / "workspace/.forestry/state.sqlite") as db:
                db.row_factory = sqlite3.Row
                quality = next((a for a in assignments if a["role_id"] == "quality"), None)
                if quality:
                    calls = db.execute("SELECT kind FROM actions WHERE turn_id=? AND status='confirmed'", (quality["run_id"],)).fetchall()
                    checks["independent_quality_read"] = {"document.read", "artifact.read"} <= {r["kind"] for r in calls}
                rows = db.execute("SELECT payload FROM contexts WHERE task_id=?", (task_id,)).fetchall()
                snapshots = [json.loads(r["payload"]) for r in rows if json.loads(r["payload"]).get("assignment_id")]
                checks["child_context_isolated"] = bool(snapshots) and all(marker not in native_path(Path(s["prepared_input_ref"])).read_text(encoding="utf-8") for s in snapshots)
            outcome = {"model": profile, "task_id": task_id, "seconds": round(time.monotonic() - start, 2),
                "passed": all(checks.values()), "checks": checks, "state": state, "assignments": assignments,
                "artifacts": artifacts, "events": events}
            outcomes.append(outcome)
            tag = "roles-follow-up" if prior else "roles"
            suffix = "-" + uuid4().hex if prior else ""
            (evidence / f"{tag}-{profile}-{task_id}{suffix}.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: outcome[k] for k in ["model", "task_id", "seconds", "passed", "checks"]}), flush=True)
    raise SystemExit(0 if all(o["passed"] for o in outcomes) else 1)


if __name__ == "__main__":
    main()
