"""Real local-model, two-turn chain with independent long-context checks.

Synthetic fixtures test software behavior only, never forestry accuracy.
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


def chain_request_check(events):
    request = 0
    reads = []
    for event in events:
        if event["type"] == "model.request_started":
            request += 1
        if event["type"] == "action.observed" and event["payload"]["tool"] == "document.read" and event["payload"]["result"]["kind"] == "completed":
            reads.append(request)
    return len(reads) == 21 and len(set(reads)) == 21


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--model", default="small")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    source = root / "fixtures" / "general" / "context-chain"
    source.mkdir(parents=True, exist_ok=True)
    # Deterministic opaque links force genuine sequential tool observations.
    names = [hashlib.sha256(f"context-chain-{i}".encode()).hexdigest()[:12] + ".md" for i in range(21)]
    for index, name in enumerate(names):
        text = f"这是软件验收夹具第 {index + 1} 份，共21份，不是现场数据。\n"
        if index == 0:
            text += "权威演示项目代号：CEDAR-42。演示值：23.125 米。检索关键词：青杉上下文验收。\n"
        text += f"本段验证只读来源和长对话恢复，不构成科学精度证据。记录序号 {index + 1}。\n"
        text += "下一份相对文件名：" + (names[index + 1] if index < 20 else "END") + "\n"
        if index == 11:
            text += "这是第一阶段的停止点；保存 chain-progress.json（next_file 与已读份数12），结束本回合。\n"
        path = source / name
        if path.exists() and path.read_text(encoding="utf-8") != text:
            raise RuntimeError("Existing fixture differs; preserve previous trial evidence")
        path.write_text(text, encoding="utf-8")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.md")}
    start = time.monotonic()
    with httpx.Client(base_url=args.base_url, headers={"origin": args.base_url}, timeout=30, trust_env=False) as client:
        client.get("/session").raise_for_status()
        grant = client.post("/resources/register", json={"request_id": str(uuid4()), "path": str(source), "classification": "private"})
        grant.raise_for_status()
        resource_id = grant.json()["resource_id"]
        objective = (f"执行分两回合的21份串联软件验收夹具。首份文件名为 {names[0]}，必须实际逐份用 read_document 读取；"
            "仅按当前正文给出的下一文件名继续，每次读取返回正文后才能发起下一份读取；不得执行代码。目录列举不代替实际读取。第一份用 index_document 建立索引。"
            "本回合读到第12份停止点，保存 chain-progress.json 并调用 final_result。后续用户消息才继续余9份。"
            "第二回合必须先用 read_artifact 读取进度，再串联读余9份到END，最后实际用 search_knowledge 检索青杉上下文验收，"
            "依据检索返回的引用写 chain-result.json，字段 project_code（仅代号字符串），demo_value（JSON数字，无单位字符串），"
            "source_ref（复制检索返回的resource_id、source_sha256、unit、relative_path），另写中文 chain-report.md，必须包含项目代号、演示数值、"
            "逐字复制的来源引用，以及此数据为软件验收夹具、不构成现场观测或科学精度证明的局限。"
            "源文件只读，不联网，产物必须检查并引用真实ID。")
        response = client.post("/tasks", json={"request_id": str(uuid4()), "input_refs": [resource_id],
            "model_profile": args.model, "objective": objective})
        response.raise_for_status()
        task_id = response.json()["task_id"]
        print(json.dumps({"task_id": task_id, "model": args.model, "phase": "first_turn"}), flush=True)

        def wait():
            while time.monotonic() - start < 600:
                value = client.get(f"/tasks/{task_id}")
                value.raise_for_status()
                state = value.json()
                if state["status"] not in {"created", "active", "waiting_job"}:
                    return state
                time.sleep(1)
            return state

        first = wait()
        if first["status"] == "waiting_user":
            response = client.post(f"/tasks/{task_id}/messages", json={"request_id": str(uuid4()),
                "expected_spec_revision": 1, "text": "现在执行第二回合：先读取已保存的 chain-progress.json，再串联读剩余9份至END。随后检索最早来源，保存最终JSON和中文报告并检查。"})
            response.raise_for_status()
            print(json.dumps({"task_id": task_id, "phase": "second_turn"}), flush=True)
            state = wait()
        else:
            state = first
        artifacts = client.get(f"/tasks/{task_id}/artifacts").json()
        events = collect_events(client, task_id)
        observations = [e["payload"] for e in events if e["type"] == "action.observed" and e["payload"]["result"]["kind"] == "completed"]
        reads = [e["result"].get("citation", {}).get("relative_path") for e in observations if e["tool"] == "document.read"]
        compiled = [e["payload"] for e in events if e["type"] == "context.compiled"]
        measured = [e["payload"] for e in events if e["type"] == "context.measured"]
        checks = {"source_unchanged": hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.md")},
            "both_turns_returned": first["status"] == state["status"] == "waiting_user",
            "revision_unchanged": state["spec_revision"] == 1,
            "all_sources_read": set(names) <= set(reads),
            "chain_order_followed": reads == names,
            "each_link_read_in_separate_request": chain_request_check(events),
            "progress_read_back": any(e["tool"] == "artifact.read" for e in observations),
            "early_source_retrieved": any(e["tool"] == "knowledge.search" and e["result"].get("matches") for e in observations),
            "history_reduced": any(c["history_message_count_reduced"] and c["prior_message_refs_absent"] for c in compiled),
            "usage_measured_and_covered": len(compiled) == len(measured) and bool(measured) and all(c["estimate_covers_observed_usage"] for c in measured),
            "facts_correct": False, "citation_correct": False, "report_exists": False}
        for artifact in artifacts:
            if artifact["name"] not in {"chain-result.json", "chain-report.md"}:
                continue
            content = client.get(artifact["preview_url"])
            content.raise_for_status()
            if artifact["name"] == "chain-result.json":
                data = content.json()
                checks["facts_correct"] = data.get("project_code") == "CEDAR-42" and data.get("demo_value") == 23.125
                ref = data.get("source_ref", {})
                expected_unit = f"bytes:0:{(source / names[0]).stat().st_size}"
                checks["citation_correct"] = ref.get("resource_id") == resource_id and ref.get("source_sha256") == hashes[names[0]] and ref.get("relative_path") == names[0] and ref.get("unit") == expected_unit
            else:
                checks["report_exists"] = "CEDAR-42" in content.text and "23.125" in content.text and "夹具" in content.text
        outcome = {"task_id": task_id, "model": args.model, "seconds": round(time.monotonic() - start, 2),
            "passed": all(checks.values()), "checks": checks, "first_state": first, "state": state,
            "source_hashes": hashes, "artifacts": artifacts, "events": events}
        evidence = root / "workspace" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / f"context-{args.model}-{task_id}.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: outcome[k] for k in ["task_id", "seconds", "checks", "passed"]}), flush=True)
        raise SystemExit(0 if outcome["passed"] else 1)


if __name__ == "__main__":
    main()
