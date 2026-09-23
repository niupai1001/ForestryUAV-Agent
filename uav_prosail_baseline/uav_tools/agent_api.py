"""可直接映射为 Agent Tool 或 MCP Tool 的稳定函数接口。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .metadata import (
    derive_project_name,
    inspect_dataset,
    list_images,
    validate_dataset,
)
from .odm import (
    build_odm_command,
    get_odm_status,
    start_odm,
    validate_orthomosaic,
    wait_for_odm,
)
from .preview import generate_standard_previews
from .project import ensure_free_space, project_paths, save_json, stage_images


def inspect_flight(input_directory: str) -> dict[str, Any]:
    """检查一个航次，返回可直接 JSON 序列化的结果，不写报告文件。"""
    report = inspect_dataset(Path(input_directory))
    problems = validate_dataset(report)
    return {"ready": not problems, "problems": problems, **report}


def default_odm_options(inspection: Mapping[str, Any]) -> dict[str, Any]:
    """只保留与当前任务目的直接相关的 ODM 选项。"""
    options: dict[str, Any] = {"skip_3dmodel": True}
    if inspection.get("processing_mode") == "multispectral":
        options["radiometric_calibration"] = "camera"
    return options


def _status_payload(job_id: str, paths: dict[str, Path]) -> dict[str, Any]:
    status = get_odm_status(paths)
    outputs = {
        "orthophoto": str(paths["orthophoto"])
        if paths["orthophoto"].is_file()
        else None,
        "dsm": str(paths["dsm"]) if paths["dsm"].is_file() else None,
        "odm_report": str(paths["odm_report"])
        if paths["odm_report"].is_file()
        else None,
    }
    return {
        "job_id": job_id,
        "state": status["state"],
        "job_directory": str(paths["project"]),
        "processing_log": str(paths["log"]),
        "return_code": status.get("return_code"),
        "outputs": outputs,
    }


def submit_orthomosaic(
    input_directory: str,
    workspace_root: str,
    job_id: str | None = None,
    odm_options: Mapping[str, Any] | None = None,
    inspection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """准备输入并提交本地 ODM；返回任务 ID，不等待任务完成。"""
    inspection = dict(inspection or inspect_flight(input_directory))
    if not inspection["ready"]:
        raise RuntimeError("数据检查未通过：" + "；".join(inspection["problems"]))

    source = Path(input_directory)
    job_id = job_id or derive_project_name(source, inspection)
    paths = project_paths(Path(workspace_root), job_id)
    current = get_odm_status(paths)
    if current["state"] in {"running", "succeeded"}:
        return _status_payload(job_id, paths)
    if current["state"] in {"failed", "interrupted"}:
        raise RuntimeError(f"同名任务失败或中断：{paths['job']}")

    odm_home = paths["odm_home"]
    if not (odm_home / "run.bat").is_file():
        raise FileNotFoundError(f"ODM 运行时不存在：{odm_home}")
    ensure_free_space(paths["workspace"])
    stage_images(list_images(source), paths["images"], paths["manifest"])

    resolved_options = default_odm_options(inspection)
    resolved_options.update(dict(odm_options or {}))
    command = build_odm_command(
        odm_home,
        paths["project_root"],
        job_id,
        resolved_options,
    )
    start_odm(
        command,
        paths,
        odm_home,
        context={
            "input_directory": str(source.resolve()),
            "inspection": inspection,
            "odm_options": resolved_options,
        },
    )
    return _status_payload(job_id, paths)


def orthomosaic_status(job_id: str, workspace_root: str) -> dict[str, Any]:
    """查询一个任务；适合 Agent 或 MCP 轮询。"""
    return _status_payload(job_id, project_paths(Path(workspace_root), job_id))


def wait_for_orthomosaic(
    job_id: str,
    workspace_root: str,
    poll_interval_seconds: float = 15.0,
) -> dict[str, Any]:
    """供本地流程等待任务；Agent/MCP 应优先调用 orthomosaic_status。"""
    paths = project_paths(Path(workspace_root), job_id)
    wait_for_odm(paths, poll_interval_seconds=poll_interval_seconds)
    return _status_payload(job_id, paths)


def finalize_orthomosaic(job_id: str, workspace_root: str) -> dict[str, Any]:
    """检查结果并生成预览；所有摘要只写入一个 result.json。"""
    paths = project_paths(Path(workspace_root), job_id)
    status = get_odm_status(paths)
    if status["state"] != "succeeded":
        raise RuntimeError(f"任务尚未成功完成，当前状态：{status['state']}")

    if paths["result"].is_file():
        return json.loads(paths["result"].read_text(encoding="utf-8"))

    qa = validate_orthomosaic(paths, paths["odm_home"])
    if not qa["qa_pass"]:
        raise RuntimeError(f"正射 GeoTIFF 基础检查未通过：{paths['orthophoto']}")
    previews = generate_standard_previews(
        paths["orthophoto"],
        paths["previews"],
        paths["odm_home"],
    )
    result = {
        "job_id": job_id,
        "state": "succeeded",
        "job_directory": str(paths["project"]),
        "orthophoto": str(paths["orthophoto"]),
        "dsm": str(paths["dsm"]) if paths["dsm"].is_file() else None,
        "odm_report": str(paths["odm_report"])
        if paths["odm_report"].is_file()
        else None,
        "processing_log": str(paths["log"]),
        "previews": previews,
        "quality": qa,
        "result_file": str(paths["result"]),
    }
    save_json(paths["result"], result)
    return result
