"""本地一键流程：只排列稳定工具接口，不包含处理实现。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from uav_tools import (
    finalize_orthomosaic,
    inspect_flight,
    submit_orthomosaic,
    wait_for_orthomosaic,
)


def run_workflow(
    input_directory: Path,
    workspace_root: Path,
    odm_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    inspection = inspect_flight(str(input_directory))
    if not inspection["ready"]:
        raise RuntimeError("数据检查未通过：" + "；".join(inspection["problems"]))

    camera = ", ".join(inspection["camera_models"]) or "未知设备"
    print(
        f"[航次] {camera} | {inspection['capture_count']} 组 | "
        f"{inspection['processing_mode']} | {inspection['source_directory']}"
    )

    job = submit_orthomosaic(
        str(input_directory),
        str(workspace_root),
        odm_options=odm_options,
        inspection=inspection,
    )
    if job["state"] == "running":
        job = wait_for_orthomosaic(job["job_id"], str(workspace_root))
    if job["state"] != "succeeded":
        raise RuntimeError(f"ODM 未成功完成，当前状态：{job['state']}")

    result = finalize_orthomosaic(job["job_id"], str(workspace_root))
    print(f"[完成] {result['result_file']}")
    return result
