"""Domain recipes submit to the existing Jobs; paths come from granted resources."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from .cloud_inputs import CloudDeclaration, inspect_cloud
from .contracts import Boundary, Failure

METHOD_ID = "lidar.smrf_idw.v1"
RESUME_FILES = {"clean-input.las", "classified.las", "hag.las", "dtm-raw.tif", "dsm.tif", "dtm.tif",
                "ground-cell-count.tif", "nearest-ground-distance.tif", "terrain-support.tif", "chm-demdiff.tif",
                "annotation-isolation.json", "versions-pdal.log", "versions-r.log", "science-explicit-lock.txt"}


class TerrainRequest(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_spec_revision: int = Field(ge=1)
    cloud_ref: str = Field(min_length=1)
    declaration_ref: str = Field(min_length=1)
    method_id: str = METHOD_ID
    seconds: int = Field(default=900, ge=1, le=14400)
    resume_job_id: str | None = None
    resume_refs: dict[str, str] = Field(default_factory=dict, max_length=20)


def prepare_terrain(engine, task_id, request: TerrainRequest):
    state = engine.store.task(task_id)
    if state["spec_revision"] != request.expected_spec_revision:
        raise Failure("VERSION_CONFLICT", "点云处理须匹配当前任务修订", 409)
    if request.method_id != METHOD_ID or state.get("requested_method_id") not in (None, METHOD_ID):
        raise Failure("PRECONDITION_MISSING", "指定方法不可用，不自动替换", 422)
    if not engine.config.science_image or not state["config_snapshot"].get("science_image"):
        raise Failure("PRECONDITION_MISSING", "任务没有冻结科学运行时", 422)
    source = engine.resources.resolve(request.cloud_ref, task_id=task_id)
    declared_source = engine.resources.resolve(request.declaration_ref, task_id=task_id)
    if declared_source.stat().st_size > 32768:
        raise Failure("INPUT_LIMIT", "点云声明超过32KiB", 422)
    declaration = CloudDeclaration.model_validate_json(declared_source.read_bytes())
    inspection = inspect_cloud(source, declaration)
    conditions = inspection["prerequisites"]
    if conditions["crs_conflict"] or not all(conditions[name] for name in (
            "file_projected_xy_metres", "declared_elevation_metres", "declared_vertical_reference")):
        raise Failure("PRECONDITION_MISSING", "SMRF要求文件米制投影、明确高程/米制Z和垂直基准且无CRS冲突", 422)
    if inspection["observed"]["compressed"]:
        raise Failure("PRECONDITION_MISSING", "当前完整处理入口尚未验证LAZ解压，保留原方法而不替换输入", 422)
    if not 0 < inspection["observed"]["point_count"] <= 5_000_000:
        raise Failure("PRECONDITION_MISSING", "当前整架次路线限500万点；更大输入需要分块路线，不自动抽稀", 422)
    bounds = inspection["observed"]["bounds"]
    if (bounds[1][0] - bounds[0][0] + 2) * (bounds[1][1] - bounds[0][1] + 2) > 1_000_000:
        raise Failure("INPUT_LIMIT", "当前1米网格超过完整作业空间预算，需分块规划", 422)
    payload = {"schema_version": 1, "task_id": task_id, "spec_revision": state["spec_revision"],
               "method_id": METHOD_ID, "cloud_ref": request.cloud_ref, "declaration_ref": request.declaration_ref,
               "cloud_path": "/inputs/" + request.cloud_ref, "declaration_path": "/inputs/" + request.declaration_ref,
               "expected_point_count": inspection["observed"]["point_count"],
               "crs": inspection["observed"]["horizontal_crs"],
               "vertical_ref": declaration.vertical_ref, "z_unit": declaration.z_unit,
               "z_semantics": declaration.z_semantics,
               "science_image": state["config_snapshot"]["science_image"],
               "limitations": ["发布者垂直声明未由独立控制点验证", "500万点整架次bootstrap；分块/边界尚未验收",
                               "地面支持规则为预声明bootstrap，不是精度保证", "没有独立树高/树种/生物量验证"]}
    if request.resume_job_id or request.resume_refs:
        if not request.resume_job_id or set(request.resume_refs) != RESUME_FILES:
            raise Failure("SCHEMA_INVALID", "地形后处理恢复须提供原Job及完整已生成产品集合", 422)
        previous = engine.jobs.read(request.resume_job_id, task_id)
        if previous["status"] != "failed" or previous["image_reference"] != payload["science_image"]:
            raise Failure("PRECONDITION_MISSING", "只能复用本任务同镜像已失败Job的已生成地形/HAG，不重新计算", 422)
        previous_artifacts = [engine.resources.artifact(a, task_id) for a in previous["artifact_ids"]]
        previous_request = next((a for a in previous_artifacts if a["name"] == "request.json"), None)
        if not previous_request:
            raise Failure("PRECONDITION_MISSING", "原作业缺少科学来源请求记录", 422)
        old = json.loads(Path(previous_request["locator"]).read_text(encoding="utf-8"))
        if any(old.get(k) != payload[k] for k in ("method_id", "cloud_ref", "declaration_ref", "crs", "vertical_ref")):
            raise Failure("VERSION_CONFLICT", "恢复不能替换原源、方法或垂直声明", 409)
        inputs = {}
        for name, ref in request.resume_refs.items():
            path = engine.resources.resolve(ref, task_id=task_id)
            artifact = next((a for a in previous_artifacts if a["name"] == name and Path(a["locator"]).resolve() == path), None)
            if not artifact:
                raise Failure("PERMISSION_DENIED", "恢复文件必须是原Job已发布产物，并被明确授权为当前输入", 403)
            inputs[name] = {"path": "/inputs/" + ref, "sha256": artifact["sha256"], "artifact_id": artifact["artifact_id"]}
        payload["reuse"] = {"job_id": request.resume_job_id, "files": inputs,
                           "scope": "mapping repair and remaining postprocessing; no PDAL pipeline replay"}
    worker = Path(__file__).with_name("lidar_terrain_worker.py").read_text(encoding="utf-8")
    code = "REQUEST = " + repr(payload) + "\n" + worker
    return code, inspection


def submit_terrain(engine, deps, action_id, request: TerrainRequest):
    code, inspection = prepare_terrain(engine, deps.task_id, request)
    result = engine.jobs.submit(deps.task_id, deps.revision, action_id, code,
                                [request.cloud_ref, request.declaration_ref, *request.resume_refs.values()], request.seconds,
                                runtime_profile="scientific", reuse_equivalent=True)
    return {**result, "method_id": METHOD_ID,
            "input_prerequisites": inspection["prerequisites"], "science_status": "queued_not_validated"}
