"""Staged lidR recipe on existing Jobs, with explicit grants and product lineage."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from .contracts import Boundary, Failure

METHOD_ID = "lidar.p2r_lmf_dalponte.v1"
REGISTERED_METHODS = {METHOD_ID, "lidar.smrf_idw.v1", "lidar.roi_clip.v1"}


def method_spec():
    packaged = Path(__file__).parent / "methods/lidar-lidr-canopy.yaml"
    location = packaged if packaged.is_file() else Path(__file__).parents[2] / "config/methods/lidar-lidr-canopy.yaml"
    return yaml.safe_load(location.read_text(encoding="utf-8"))


class CanopyStageRequest(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_spec_revision: int = Field(ge=1)
    stage: Literal["chm", "seeds", "instances"]
    input_refs: dict[str, str] = Field(max_length=10)
    method_id: Literal["lidar.p2r_lmf_dalponte.v1"] = METHOD_ID
    seconds: int = Field(default=900, ge=1, le=14400)

    @model_validator(mode="after")
    def exact_stage_inputs(self):
        expected = {
            "chm": {"working", "terrain_quality", "terrain_support"},
            "seeds": {"chm", "chm_record"},
            "instances": {"working", "original", "hag", "chm", "chm_record", "seeds", "seed_record"},
        }[self.stage]
        if set(self.input_refs) != expected or any(not value for value in self.input_refs.values()):
            raise ValueError(f"{self.stage} requires precisely these granted file roles: {sorted(expected)}")
        return self


def prepare_canopy(engine, task_id: str, request: CanopyStageRequest):
    state = engine.store.task(task_id)
    if state["spec_revision"] != request.expected_spec_revision:
        raise Failure("VERSION_CONFLICT", "冠层处理须匹配当前任务修订", 409)
    if state.get("requested_method_id") not in (None, METHOD_ID):
        raise Failure("PRECONDITION_MISSING", "任务指定方法不同，不自动替换", 422)
    if not engine.config.science_image or not state["config_snapshot"].get("science_image"):
        raise Failure("PRECONDITION_MISSING", "任务未冻结科学镜像", 422)
    for ref in request.input_refs.values():
        path = engine.resources.resolve_input(ref, task_id)
        if not path.is_file():
            raise Failure("PRECONDITION_MISSING", "科学阶段须明确授权单文件", 422)
    spec = method_spec()
    spec["max_cr_pixels"] = math.ceil(spec["max_crown_diameter_m"] / spec["chm_resolution_m"])
    payload = {"schema_version": 1, "task_id": task_id, "spec_revision": request.expected_spec_revision,
               "stage": request.stage, "method_id": METHOD_ID, "parameters": spec,
               "science_image": state["config_snapshot"]["science_image"],
               "inputs": {role: {"resource_id": ref, "path": "/inputs/" + ref}
                          for role, ref in sorted(request.input_refs.items())}}
    body = Path(__file__).with_name("lidar_canopy_worker.py").read_text(encoding="utf-8")
    script = Path(__file__).with_name("lidar_canopy.R").read_text(encoding="utf-8")
    return "REQUEST = " + repr(payload) + "\nR_SCRIPT = " + repr(script) + "\n" + body


def submit_canopy(engine, deps, action_id, request: CanopyStageRequest):
    code = prepare_canopy(engine, deps.task_id, request)
    result = engine.jobs.submit(deps.task_id, deps.revision, action_id, code,
                               [request.input_refs[role] for role in sorted(request.input_refs)], request.seconds,
                               runtime_profile="scientific", reuse_equivalent=True)
    return {**result, "method_id": METHOD_ID, "stage": request.stage,
            "science_status": "submitted_not_accepted"}
