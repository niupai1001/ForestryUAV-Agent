"""Legacy in-process Qwen smoke suite; not part of independent evaluation."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from runtime.agent import stream_agent
from runtime.memory import MemoryManager
from runtime.storage import Store
from runtime.workspace import WorkspaceRegistry


async def run_case(
    store: Store,
    registry: WorkspaceRegistry,
    memory: MemoryManager,
    owner: str,
    prompt: str,
) -> dict:
    events = [
        event async for event in stream_agent(
            store,
            owner,
            [],
            [{"role": "user", "content": prompt}],
            workspace_registry=registry,
            chat_id=store.chat_id,
            project_context=lambda: memory.context(owner, store.chat_id),
            memory_manager=memory,
            max_requests=12,
        )
    ]
    starts = [
        {"name": item.get("name"), "arguments": item.get("arguments")}
        for item in events if item.get("type") == "tool_start"
    ]
    ends = [item for item in events if item.get("type") == "tool_end"]
    return {
        "state": next((
            item.get("state", "completed") for item in reversed(events)
            if item.get("type") == "done"
        ), "missing"),
        "model_calls": sum(item.get("type") == "model_call" for item in events),
        "tool_calls": starts,
        "tool_results": [item.get("result") for item in ends],
        "answer": "".join(
            str(item.get("content") or "")
            for item in events if item.get("type") == "message"
        ),
    }


def citations(case: dict) -> list[str]:
    found = []
    for result in case["tool_results"]:
        data = (result or {}).get("data") or {}
        for item in data.get("results") or []:
            if item.get("citation"):
                found.append(str(item["citation"]))
        for item in data.get("chunks") or []:
            if item.get("citation"):
                found.append(str(item["citation"]))
    return list(dict.fromkeys(found))


def public_case(case: dict) -> dict:
    summaries = []
    for result in case["tool_results"]:
        result = result or {}
        data = result.get("data") or {}
        summaries.append({
            "ok": result.get("ok"),
            "failure_code": (result.get("failure") or {}).get("code"),
            "ready": data.get("ready"),
            "image_file_count": data.get("image_file_count"),
            "capture_count": data.get("capture_count"),
            "gps_file_count": data.get("gps_file_count"),
            "band_image_counts": data.get("band_image_counts"),
            "roles": {
                role: {"file_count": values.get("file_count")}
                for role, values in (data.get("roles") or {}).items()
            } or None,
            "role_counts": data.get("role_counts"),
            "chm_input_suitability": data.get("chm_input_suitability"),
            "result_count": len(data.get("results") or []),
            "chunk_count": len(data.get("chunks") or []),
        })
    answer = str(case.get("answer") or "")
    public = {
        key: value for key, value in case.items()
        if key not in {"tool_results", "answer"}
    }
    return public | {
        "answer": answer[:4000],
        "answer_chars": len(answer),
        "tool_results": summaries,
        "citations": citations(case),
    }


async def main(args) -> int:
    dataset = Path(args.dataset).resolve()
    products = Path(args.products).resolve()
    knowledge = Path(args.knowledge).resolve()
    if not dataset.is_dir() or not products.is_dir() or not knowledge.is_dir():
        raise SystemExit("dataset, products and knowledge must be existing directories")

    with tempfile.TemporaryDirectory(prefix="forestry-qwen-acceptance-") as temporary:
        root = Path(temporary)
        store = Store(root / "session")
        store.chat_id = "qwen-uav-rag-acceptance"
        registry = WorkspaceRegistry(root / "runtime")
        memory = MemoryManager(root / "memory")
        owner = "acceptance"
        project = memory.create_project(owner, "林业UAV验收")
        indexed = memory.index_source(
            owner, project["id"], "local", str(knowledge)
        )
        memory.select_project(owner, store.chat_id, project["id"])
        grant = registry.grant(
            owner, store.chat_id, str(dataset),
            f"检查 {dataset} 的真实无人机数据，但不要启动摄影测量任务。",
            "read",
        )
        product_grant = registry.grant(
            owner, store.chat_id, str(products),
            f"检查 {products} 中已有遥感成果，但不要启动摄影测量任务。",
            "read",
        )

        audit = None
        inventory = None
        product_case = None
        chm_case = None
        knowledge_case = None
        direct = None
        if args.case in {"all", "audit"}:
            audit = await run_case(
                store, registry, memory, owner,
                f"请只读检查目录 {dataset} 的林业无人机航片基础元数据条件。"
                "不要检索项目知识，不要启动正射或任何摄影测量任务；"
                "报告影像数、曝光组数、波段、GPS、问题以及ready字段的限定含义。",
            )
        if args.case in {"all", "inventory"}:
            inventory = await run_case(
                store, registry, memory, owner,
                f"请只读盘点林业UAV目录 {dataset}。使用数据集盘点能力，明确区分"
                "主航线、起飞前参考板、起飞后参考板和已有地理成果；不要启动任何处理。",
            )
        if args.case in {"all", "products"}:
            product_case = await run_case(
                store, registry, memory, owner,
                f"请使用已有成果质量检查能力，只读检查目录 {products} 中名称包含1605的"
                "正射/DSM/DTM/CHM成果。报告投影、像元、范围、波段、NoData、自动QA边界，"
                "不要启动正射，也不要把可读GeoTIFF说成已验证测量精度。",
            )
        if args.case in {"all", "chm"}:
            chm_case = await run_case(
                store, registry, memory, owner,
                f"请只读检查目录 {products} 中名称包含1605的已有成果，判断当前是否具有"
                "可生成CHM的DSM和DTM条件。必须使用成果检查工具；缺失就报告缺失，不能猜测。",
            )
        if args.case in {"all", "knowledge"}:
            knowledge_case = await run_case(
                store, registry, memory, owner,
                "请依据项目知识解释：为什么CHM分水岭得到的候选单木不能直接当作林木总株数？"
                "请主动检索项目知识并逐字给出citation；不要检查文件。",
            )
        if args.case in {"all", "direct"}:
            direct = await run_case(
                store, registry, memory, owner,
                "只回答2+2的结果，不要检索项目知识，也不要调用文件或遥感工具。",
            )

        checks = {}
        if audit is not None:
            audit_tools = [item["name"] for item in audit["tool_calls"]]
            checks |= {
                "audit_called_inspect_uav_source": "inspect_uav_source" in audit_tools,
                "no_local_photogrammetry_tool_exposed": not any(
                name in audit_tools for name in (
                    "start_orthomosaic", "wait_for_job", "finalize_job"
                )
                ),
                "audit_did_not_invent_rtk_success": not any(
                    phrase in audit["answer"]
                    for phrase in (
                        "表示RTK定位成功", "说明RTK定位成功",
                        "RTK定位成功（", "RTK 固定解：", "RTK固定解：",
                    )
                ),
            }
        if inventory is not None:
            inventory_tools = [item["name"] for item in inventory["tool_calls"]]
            inventory_results = [
                (result or {}).get("data") or {}
                for result in inventory["tool_results"]
            ]
            roles = next(
                (item.get("roles") for item in inventory_results if item.get("roles")),
                {},
            )
            checks |= {
                "inventory_completed_with_answer": (
                    inventory["state"] == "completed"
                    and bool(inventory["answer"].strip())
                ),
                "inventory_called_dataset_tool": "inspect_uav_dataset" in inventory_tools,
                "inventory_separated_flight_and_panels": all(
                    role in roles for role in (
                        "flight_imagery", "reference_panel_before",
                        "reference_panel_after",
                    )
                ),
            }
        if product_case is not None:
            product_tools = [item["name"] for item in product_case["tool_calls"]]
            product_results = [
                (result or {}).get("data") or {}
                for result in product_case["tool_results"]
            ]
            role_counts = next(
                (item.get("role_counts") for item in product_results if item.get("role_counts")),
                {},
            )
            checks |= {
                "products_completed_with_answer": (
                    product_case["state"] == "completed"
                    and bool(product_case["answer"].strip())
                ),
                "products_called_quality_tool": "inspect_uav_products" in product_tools,
                "products_found_orthomosaic": role_counts.get("orthomosaic") == 1,
                "products_did_not_claim_survey_accuracy": not any(
                    phrase in product_case["answer"] for phrase in (
                        "已达到测量级精度", "厘米级精度已验证", "测量精度合格",
                    )
                ),
                "products_kept_geographic_pixel_units": not any(
                    phrase in product_case["answer"]
                    for phrase in ("0.00000041 m", "4.1e-7 m", "4.10344e-07 m")
                ),
            }
        if chm_case is not None:
            chm_tools = [item["name"] for item in chm_case["tool_calls"]]
            chm_results = [
                (result or {}).get("data") or {}
                for result in chm_case["tool_results"]
            ]
            suitability = next((
                item.get("chm_input_suitability") for item in chm_results
                if item.get("chm_input_suitability")
            ), {})
            checks |= {
                "chm_completed_with_answer": (
                    chm_case["state"] == "completed"
                    and bool(chm_case["answer"].strip())
                ),
                "chm_called_quality_tool": "inspect_uav_products" in chm_tools,
                "chm_truthfully_reported_missing_dsm_dtm": set(
                    suitability.get("missing_roles") or []
                ) == {"dsm", "dtm"},
            }
        if knowledge_case is not None:
            knowledge_tools = [item["name"] for item in knowledge_case["tool_calls"]]
            source_citations = citations(knowledge_case)
            checks |= {
                "knowledge_case_called_search": "knowledge_search" in knowledge_tools,
                "knowledge_answer_copied_citation": any(
                    citation in knowledge_case["answer"] for citation in source_citations
                ),
            }
        if direct is not None:
            direct_tools = [item["name"] for item in direct["tool_calls"]]
            checks["direct_case_used_no_tools"] = not direct_tools
        report = {
            "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
            "indexed_state": indexed["state"],
            "source_id": grant["id"],
            "product_source_id": product_grant["id"],
            "audit": public_case(audit) if audit is not None else None,
            "inventory": public_case(inventory) if inventory is not None else None,
            "products": public_case(product_case) if product_case is not None else None,
            "chm": public_case(chm_case) if chm_case is not None else None,
            "knowledge": public_case(knowledge_case) if knowledge_case is not None else None,
            "direct": public_case(direct) if direct is not None else None,
            "checks": checks,
            "passed": all(checks.values()),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--products", required=True)
    parser.add_argument("--knowledge", required=True)
    parser.add_argument(
        "--case", choices=(
            "all", "audit", "inventory", "products", "chm",
            "knowledge", "direct",
        ),
        default="all",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
