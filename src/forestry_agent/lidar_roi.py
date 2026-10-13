"""Explicit projected ROI postprocessing, reusing frozen canopy products."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pyproj import CRS
from pyproj.exceptions import CRSError

from .contracts import Boundary, Failure

METHOD_ID = "lidar.roi_clip.v1"
INPUT_ROLES = {"segmented", "chm", "trees", "vectors", "quality", "instance_record", "roi"}


class RoiDefinition(Boundary):
    schema_version: Literal[1] = 1
    horizontal_crs: str = Field(min_length=1, max_length=5000)
    source_note: str = Field(min_length=1, max_length=4000)
    geometry: dict

    @model_validator(mode="after")
    def projected_polygon(self):
        try:
            crs = CRS.from_user_input(self.horizontal_crs)
        except CRSError as error:
            raise ValueError("ROI horizontal CRS is not parseable") from error
        if not crs.is_projected or any(axis.unit_name != "metre" for axis in crs.axis_info[:2]):
            raise ValueError("ROI requires explicitly projected metric XY")
        if set(self.geometry) != {"type", "coordinates"} or self.geometry["type"] not in {"Polygon", "MultiPolygon"}:
            raise ValueError("ROI must be an explicit Polygon or MultiPolygon coordinate geometry")
        polygons = [self.geometry["coordinates"]] if self.geometry["type"] == "Polygon" else self.geometry["coordinates"]
        if not polygons or not isinstance(polygons, list):
            raise ValueError("ROI has no polygon")
        vertices = 0
        for polygon in polygons:
            if not polygon or not isinstance(polygon, list):
                raise ValueError("ROI polygon has no rings")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError("ROI rings must be closed with at least four positions")
                for point in ring:
                    if (not isinstance(point, list) or len(point) != 2
                            or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)):
                        raise ValueError("ROI positions require two finite XY numbers")
                vertices += len(ring)
        if vertices > 4096:
            raise ValueError("ROI exceeds the 4096-position boundary budget")
        return self


class RoiRequest(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_spec_revision: int = Field(ge=1)
    input_refs: dict[str, str] = Field(max_length=10)
    method_id: Literal["lidar.roi_clip.v1"] = METHOD_ID
    seconds: int = Field(default=600, ge=1, le=14400)

    @model_validator(mode="after")
    def exact_inputs(self):
        if set(self.input_refs) != INPUT_ROLES or any(not ref for ref in self.input_refs.values()):
            raise ValueError(f"ROI requires precisely these granted file roles: {sorted(INPUT_ROLES)}")
        return self


def prepare_roi(engine, task_id, request: RoiRequest):
    state = engine.store.task(task_id)
    if state["spec_revision"] != request.expected_spec_revision:
        raise Failure("VERSION_CONFLICT", "ROI处理须匹配当前任务修订", 409)
    if state.get("requested_method_id") not in (None, METHOD_ID):
        raise Failure("PRECONDITION_MISSING", "任务指定方法不同，不替换原科学方法", 422)
    if not engine.config.science_image or not state["config_snapshot"].get("science_image"):
        raise Failure("PRECONDITION_MISSING", "任务未冻结科学镜像", 422)
    for ref in request.input_refs.values():
        if not engine.resources.resolve_input(ref, task_id).is_file():
            raise Failure("PRECONDITION_MISSING", "ROI阶段须明确授权单文件", 422)
    path = engine.resources.resolve_input(request.input_refs["roi"], task_id)
    if path.stat().st_size > 65536:
        raise Failure("INPUT_LIMIT", "ROI声明超过64KiB", 422)
    roi = RoiDefinition.model_validate_json(path.read_bytes())
    payload = {"schema_version": 1, "task_id": task_id, "spec_revision": request.expected_spec_revision,
               "method_id": METHOD_ID, "science_image": state["config_snapshot"]["science_image"],
               "roi": roi.model_dump(), "inputs": {role: {"resource_id": ref, "path": "/inputs/" + ref}
                                                    for role, ref in sorted(request.input_refs.items())}}
    body = Path(__file__).with_name("lidar_roi_worker.py").read_text(encoding="utf-8")
    script = Path(__file__).with_name("lidar_roi.R").read_text(encoding="utf-8")
    return "REQUEST = " + repr(payload) + "\nR_SCRIPT = " + repr(script) + "\n" + body


def submit_roi(engine, deps, action_id, request: RoiRequest):
    code = prepare_roi(engine, deps.task_id, request)
    result = engine.jobs.submit(deps.task_id, deps.revision, action_id, code,
                               [request.input_refs[r] for r in sorted(request.input_refs)], request.seconds,
                               runtime_profile="scientific", reuse_equivalent=True)
    return {**result, "method_id": METHOD_ID, "science_status": "submitted_not_accepted"}
