"""Package verified outputs of one failed ODM job, without recalculating them."""
import json
import shutil
import zipfile
from pathlib import Path

from .contracts import Failure
from .resources import native_path


def resume_archive(engine, task_id, action_id, request):
    if request.operation != "orthomosaic":
        raise Failure("SCHEMA_INVALID", "仅支持ODM恢复", 422)
    state = engine.store.task(task_id)
    if state["spec_revision"] != request.expected_spec_revision:
        raise Failure("VERSION_CONFLICT", "恢复须匹配当前修订", 409)
    previous = engine.jobs.read(request.resume_job_id, task_id)
    if previous["status"] != "failed" or previous.get("runtime_profile") != "orthomosaic":
        raise Failure("PRECONDITION_MISSING", "只能复用同会话已失败的ODM作业", 422)
    if previous["image_reference"] != state["config_snapshot"].get("orthomosaic_image"):
        raise Failure("VERSION_CONFLICT", "恢复不替换ODM镜像", 409)
    engine.jobs.verify_inputs(previous)
    records = [engine.resources.artifact(a, task_id) for a in previous["artifact_ids"]]
    source_request = next((a for a in records if a["name"] == "request.json"), None)
    if not source_request or not request.declaration_ref:
        raise Failure("PRECONDITION_MISSING", "原作业请求与当前ODM声明必须完整", 422)
    original = json.loads(native_path(Path(source_request["locator"])).read_text(encoding="utf-8"))
    from .imagery_schema import validate_declaration
    path = engine.resources.resolve_input(request.declaration_ref, task_id)
    if path.stat().st_size > 32768:
        raise Failure("INPUT_LIMIT", "恢复声明超过32KiB", 422)
    current = validate_declaration("orthomosaic", json.loads(path.read_text(encoding="utf-8-sig")))
    if original["declaration"] != current:
        raise Failure("VERSION_CONFLICT", "恢复不改变原定标/分辨率/方法声明", 409)
    prefix = ("assignments/" + previous["assignment_id"] + "/" if previous.get("assignment_id") else "")
    prefix += "jobs/" + previous["job_id"] + "/"
    files = [(a, a["relative_path"][len(prefix):]) for a in records
             if a["relative_path"].startswith(prefix + "project/")]
    names = {relative for _, relative in files}
    if not {"project/opensfm/reconstruction.json", "project/odm_meshing/odm_25dmesh.ply"} <= names:
        raise Failure("PRECONDITION_MISSING", "当前恢复需要完整已完成的重建/网格；不自动重算缺失阶段", 422)
    relative = "imagery-resume/" + previous["job_id"] + ".zip"
    with engine.store.transaction() as db:
        for row in db.execute("SELECT payload FROM artifacts WHERE task_id=?", (task_id,)):
            artifact = json.loads(row[0])
            if artifact["producer_action_id"] == action_id and artifact["relative_path"] == relative:
                return engine.resources.artifact(artifact["artifact_id"], task_id)
    folder = engine.resources.task_root(task_id) / ".imagery-resume"
    folder.mkdir(exist_ok=True)
    target = folder / (previous["job_id"] + ".zip")
    total = sum(a["size_bytes"] for a, _ in files)
    if shutil.disk_usage(folder).free < 2*total + engine.config.code_disk_reserve_bytes:
        raise Failure("RESOURCE_EXHAUSTED", "恢复归档/封存需要足够磁盘；已有结果保留", 422)
    # ZIP_STORED avoids spending scientific compute on recompressing image caches.
    try:
        with zipfile.ZipFile(native_path(target), "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for artifact, name in files:
                archive.write(native_path(Path(artifact["locator"])), name)
            archive.writestr("resume-provenance.json", json.dumps({"source_job_id":previous["job_id"],
                "source_code_sha256":previous["code_sha256"],
                "resume_stage": "odm_report" if "project/odm_orthophoto/odm_orthophoto.tif" in names else "existing_checkpoints",
                "files":[{"path":name,"sha256":a["sha256"]} for a,name in files]}))
        result = engine.resources.import_file(task_id, action_id, target, relative)
    finally:
        # One known file inside the dedicated current-task staging directory.
        if target.exists() and target.resolve().is_relative_to(folder.resolve()):
            target.unlink()
    return result
