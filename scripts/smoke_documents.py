"""Real model document/index/retrieval loop with independent source and citation checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx

from forestry_agent.client import collect_events


def fixture(path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        package.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        package.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>项目代号：青杉42。</w:t></w:r></w:p><w:p><w:r><w:t>演示值为 18.75，单位为米。此值是通用软件验收夹具，不是现场观测或科学精度验证。</w:t></w:r></w:p><w:p><w:r><w:t>交付报告必须附上来源引用，说明演示数据的局限。</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--models", nargs="+", default=["small", "large"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    source = root / "fixtures" / "general" / "knowledge-note.docx"
    fixture(source)
    original = source.read_bytes()
    source_hash = hashlib.sha256(original).hexdigest()
    evidence = root / "workspace" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    outcomes = []
    with httpx.Client(base_url=args.base_url, headers={"origin": args.base_url}, timeout=30, trust_env=False) as client:
        client.get("/session").raise_for_status()
        resource = client.post("/resources/register", json={"request_id": str(uuid4()), "path": str(source), "classification": "private"})
        resource.raise_for_status()
        resource_id = resource.json()["resource_id"]
        for model in args.models:
            start = time.monotonic()
            response = client.post("/tasks", json={"request_id": str(uuid4()), "input_refs": [resource_id],
                "model_profile": model, "objective": "实际用 read_document 读取已选 DOCX，再用 index_document 建立本地索引，用 search_knowledge 检索项目代号。依据实际检索结果保存 knowledge-result.json，字段为 project_code（仅代号字符串，无句末标点）、demo_value（JSON 数字，不能含单位字符串）、source_ref（包含 resource_id、source_sha256、unit，逐字复制真实来源引用）。另存中文 knowledge-report.md 说明项目代号、演示值、来源和不代表现场观测的局限。检查产物并调用最终输出工具。不能只靠工具列表猜结果，不执行代码。"})
            response.raise_for_status()
            task_id = response.json()["task_id"]
            print(json.dumps({"model": model, "task_id": task_id, "status": "submitted"}), flush=True)
            while time.monotonic() - start < 300:
                state = client.get(f"/tasks/{task_id}").json()
                if state["status"] not in {"created", "active", "waiting_job"}:
                    break
                time.sleep(1)
            artifacts = client.get(f"/tasks/{task_id}/artifacts").json()
            events = collect_events(client, task_id)
            observations = [e["payload"] for e in events if e["type"] == "action.observed" and e["payload"]["result"]["kind"] == "completed"]
            checks = {"source_unchanged": source.read_bytes() == original, "model_returned": state["status"] == "waiting_user",
                "document_read": any(e["tool"] == "document.read" for e in observations),
                "knowledge_indexed": any(e["tool"] == "knowledge.index" for e in observations),
                "knowledge_retrieved": any(e["tool"] == "knowledge.search" and e["result"].get("matches") for e in observations),
                "facts_correct": False, "citation_correct": False, "report_exists": False}
            for artifact in artifacts:
                content = client.get(artifact["preview_url"])
                content.raise_for_status()
                if artifact["name"] == "knowledge-result.json":
                    data = content.json()
                    checks["facts_correct"] = data.get("project_code") == "青杉42" and data.get("demo_value") == 18.75
                    pointer = data.get("source_ref", {})
                    checks["citation_correct"] = pointer.get("resource_id") == resource_id and pointer.get("source_sha256") == source_hash and pointer.get("unit") == "document_text"
                if artifact["name"] == "knowledge-report.md":
                    checks["report_exists"] = "青杉42" in content.text and "18.75" in content.text and "来源" in content.text
            outcome = {"model": model, "task_id": task_id, "seconds": round(time.monotonic() - start, 2), "passed": all(checks.values()),
                "checks": checks, "state": state, "artifacts": artifacts, "events": events}
            outcomes.append(outcome)
            (evidence / f"documents-{model}-{task_id}.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: outcome[k] for k in ["model", "task_id", "seconds", "checks", "passed"]}), flush=True)
    raise SystemExit(0 if all(o["passed"] for o in outcomes) else 1)


if __name__ == "__main__":
    main()
