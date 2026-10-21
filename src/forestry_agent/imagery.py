"""Legacy imagery recipes on the existing persistent, offline Jobs executor."""
from pathlib import Path
from typing import Literal

from pydantic import Field, model_serializer

from .contracts import Boundary, Failure

METHODS = {"imagery.prosail_legacy_nn.v1": "imagery_image", "imagery.odm.v1": "orthomosaic_image"}


class ImageryRequest(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_spec_revision: int = Field(ge=1)
    operation: Literal["inspect", "build_lut", "invert", "orthomosaic"]
    input_refs: dict[str, str] = Field(default_factory=dict, max_length=5000)
    declaration_ref: str | None = None
    resume_job_id: str | None = None
    seconds: int = Field(default=900, ge=1, le=14400)

    @model_serializer(mode="wrap")
    def preserve_original_request_identity(self, handler):
        value = handler(self)
        if self.resume_job_id is None:
            value.pop("resume_job_id", None)
        return value


def prepare_imagery(engine, task_id, request):
    state = engine.store.task(task_id)
    if state["spec_revision"] != request.expected_spec_revision:
        raise Failure("VERSION_CONFLICT", "影像处理须匹配当前会话修订", 409)
    method = "imagery.odm.v1" if request.operation == "orthomosaic" else "imagery.prosail_legacy_nn.v1"
    if state.get("requested_method_id") not in (None, method):
        raise Failure("PRECONDITION_MISSING", "不替换用户指定的科学方法", 422)
    key = METHODS[method]
    if not state["config_snapshot"].get(key) or not getattr(engine.config, key):
        raise Failure("DEPENDENCY_MISSING", "尚未配置并冻结此影像运行镜像，请准备后创建新会话", 422)
    expected = {"inspect": {"raster"}, "build_lut": set(), "invert": {"raster", "lut"}}
    if request.operation in expected and set(request.input_refs) != expected[request.operation]:
        raise Failure("SCHEMA_INVALID", "影像输入角色不完整或存在多余角色", 422)
    if request.resume_job_id and request.operation != "orthomosaic":
        raise Failure("SCHEMA_INVALID", "此恢复入口仅用于ODM已失败作业", 422)
    if request.operation == "orthomosaic" and not request.resume_job_id and len(request.input_refs) < 3:
        raise Failure("PRECONDITION_MISSING", "拼接至少需要3张已授权航片", 422)
    if request.operation != "inspect" and not request.declaration_ref:
        raise Failure("PRECONDITION_MISSING", "计算需要有来源的参数/波段/定标声明文件", 422)
    paths = {}
    for role, ref in request.input_refs.items():
        path = engine.resources.resolve_input(ref, task_id)
        if not path.is_file() or path.is_symlink():
            raise Failure("PRECONDITION_MISSING", "影像作业只接受已授权普通文件", 422)
        paths[role] = {"path": "/inputs/" + ref, "name": path.name}
    declaration = None
    if request.declaration_ref:
        if request.operation == "inspect":
            raise Failure("SCHEMA_INVALID", "元数据检查不需要计算声明", 422)
        path = engine.resources.resolve_input(request.declaration_ref, task_id)
        if path.stat().st_size > 32768:
            raise Failure("INPUT_LIMIT", "科学参数声明限32KiB", 422)
        import json
        declaration = json.loads(path.read_text(encoding="utf-8-sig"))
        from .imagery_schema import validate_declaration
        declaration = validate_declaration(request.operation, declaration)
    payload = {"operation": request.operation, "method_id": method, "inputs": paths,
               "declaration": declaration, "cpu_threads": engine.config.max_code_cpus,
               "resume_job_id": request.resume_job_id,
               "image": state["config_snapshot"][key]}
    worker = Path(__file__).with_name("imagery_worker.py").read_text(encoding="utf-8")
    if request.operation == "orthomosaic":
        legacy = Path(__file__).with_name("legacy_flight_metadata.py").read_text(encoding="utf-8")
        # The injected request precedes definitions, so remove the module-only future directive.
        worker = legacy.replace("from __future__ import annotations", "") + "\n" + worker
    return "REQUEST = " + repr(payload) + "\n" + worker


def submit_imagery(engine, deps, action_id, request):
    if request.resume_job_id:
        from .imagery_resume import resume_archive
        archive = resume_archive(engine, deps.task_id, action_id, request)
        request = request.model_copy(update={"input_refs": {"resume_archive": archive["artifact_id"]}})
    elif request.operation == "orthomosaic" and set(request.input_refs) == {"flight"}:
        # Expand one already-granted folder server-side. No pixel content or long
        # per-image tool argument list is needed in the language context.
        parent = request.input_refs["flight"]
        root = engine.resources.resolve(parent, task_id=deps.task_id)
        if not root.is_dir():
            raise Failure("PRECONDITION_MISSING", "flight须为已授权航片文件夹", 422)
        files = []
        for path in root.iterdir():
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".jpg", ".jpeg", ".tif", ".tiff", ".dng"}:
                files.append(path.name)
                if len(files) > 200:
                    raise Failure("INPUT_LIMIT", "当前整航次迁移入口限200张；不静默截取，较大航次待文件清单作业入口", 422)
        selected = []
        files.sort()
        for start in range(0, len(files), 100):
            selected.extend(engine.resources.select_files(deps.task_id, parent, files[start:start+100]))
        request = request.model_copy(update={"input_refs": {str(i): f["resource_id"] for i, f in enumerate(selected)}})
    code = prepare_imagery(engine, deps.task_id, request)
    refs = [*request.input_refs.values(), *([request.declaration_ref] if request.declaration_ref else [])]
    result = engine.jobs.submit(deps.task_id, deps.revision, action_id, code, refs, request.seconds,
        runtime_profile="orthomosaic" if request.operation == "orthomosaic" else "imagery", reuse_equivalent=True)
    return {**result, "science_status": "queued_not_validated", "operation": request.operation}
