"""本地 OpenDroneMap 命令适配、任务状态和结果检查。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import psutil

from .project import now_iso, save_json


def gdal_environment(
    odm_home: Path,
    proj_data_directory: Path | None = None,
) -> dict[str, str]:
    """返回项目内 ODM/GDAL 所需环境变量。"""
    odm_home = Path(odm_home)
    environment = os.environ.copy()
    # PyCharm 会把外层虚拟环境的激活变量传给子进程。ODM 的
    # win32env.bat 遇到这些变量时会恢复错误的 PATH，继而找不到
    # Rasterio 依赖的 GDAL DLL。子进程应只激活 ODM 自己的 venv。
    for name in (
        "_OLD_VIRTUAL_PATH",
        "_OLD_VIRTUAL_PROMPT",
        "_OLD_VIRTUAL_PYTHONHOME",
        "VIRTUAL_ENV",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYENVCFG",
    ):
        environment.pop(name, None)
    gdal_bin = odm_home / "SuperBuild" / "install" / "bin"
    environment["GDAL_DATA"] = str(gdal_bin / "data" / "gdal")
    proj_data = Path(proj_data_directory or gdal_bin / "data" / "proj")
    environment["PROJ_LIB"] = str(proj_data)
    environment["PROJ_DATA"] = str(proj_data)
    # OpenSfM 会另起未带 ``-X utf8`` 的 Python 子进程。在中文 Windows
    # 上，该进程会用 GBK 读取含中文绝对路径的 cv2/config.py。
    environment["PYTHONUTF8"] = "1"
    return environment


def build_odm_command(
    odm_home: Path,
    project_root: Path,
    project_name: str,
    options: Mapping[str, Any] | None = None,
) -> list[str]:
    """由普通字典生成 ODM 参数；未给出的项目沿用 ODM 自身默认值。"""
    run_bat = Path(odm_home) / "run.bat"
    if not run_bat.is_file():
        raise FileNotFoundError(f"ODM 入口不存在：{run_bat}")
    command = [
        str(run_bat),
        "--project-path",
        str(Path(project_root)),
        project_name,
    ]
    for raw_name, value in (options or {}).items():
        name = str(raw_name).strip().replace("-", "_")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"无效的 ODM 参数名：{raw_name}")
        if name in {"project_path", "project_name"}:
            raise ValueError(f"ODM 参数由任务框架管理，不能覆盖：{raw_name}")
        if value is None or value is False:
            continue
        command.append("--" + name.replace("_", "-"))
        if value is not True:
            if isinstance(value, (list, tuple)):
                command.extend(str(item) for item in value)
            else:
                command.append(str(value))
    return command


def _read_job(job_path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(job_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_odm_status(paths: dict[str, Path]) -> dict[str, Any]:
    """查询本地 ODM 状态；新任务以后台工作进程的退出状态为准。"""
    job = _read_job(paths["job"])
    active_pid = None
    pid = job.get("pid")
    create_time = job.get("pid_create_time")
    if pid and create_time is not None:
        try:
            process = psutil.Process(int(pid))
            if abs(process.create_time() - float(create_time)) < 0.1:
                active_pid = process.pid
        except (psutil.AccessDenied, psutil.NoSuchProcess, ValueError):
            pass

    recorded_state = job.get("state")
    schema_version = str(job.get("schema_version") or "")
    if recorded_state == "succeeded" and paths["orthophoto"].is_file():
        state = "succeeded"
    elif recorded_state == "failed":
        state = "failed"
    elif active_pid is not None:
        state = "running"
    elif schema_version == "2.0" and recorded_state in {"starting", "running"}:
        state = "interrupted"
    elif schema_version != "2.0" and paths["orthophoto"].is_file():
        # 兼容迁移前没有退出码的历史任务。
        state = "succeeded"
    elif job:
        state = "interrupted"
    else:
        state = "not_started"

    return {
        **job,
        "state": state,
        "active_pid": active_pid,
        "orthophoto": str(paths["orthophoto"]),
        "orthophoto_exists": paths["orthophoto"].is_file(),
        "dsm": str(paths["dsm"]),
        "dsm_exists": paths["dsm"].is_file(),
        "odm_report": str(paths["odm_report"]),
        "odm_report_exists": paths["odm_report"].is_file(),
        "log": str(paths["log"]),
    }


def start_odm(
    command: list[str],
    paths: dict[str, Path],
    odm_home: Path,
    context: dict[str, Any] | None = None,
    proj_data_directory: Path | None = None,
) -> dict[str, Any]:
    """通过独立工作进程启动 ODM，并把最终退出码写回 job.json。"""
    current = get_odm_status(paths)
    if current["state"] in {"running", "succeeded"}:
        return current
    if current["state"] in {"failed", "interrupted"}:
        raise RuntimeError(f"同名任务此前失败或中断，请查看：{paths['log']}")

    paths["project"].mkdir(parents=True, exist_ok=True)
    job = {
        "schema_version": "2.0",
        "state": "starting",
        "started_at": now_iso(),
        "command": command,
        "context": context or {},
    }
    save_json(paths["job"], job)

    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    worker = [
        sys.executable,
        "-m",
        "uav_tools._odm_worker",
        str(paths["job"]),
        str(paths["log"]),
        str(Path(odm_home)),
        str(proj_data_directory) if proj_data_directory else "",
    ]
    process = subprocess.Popen(
        worker,
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creation_flags,
        close_fds=True,
    )

    try:
        pid_create_time = psutil.Process(process.pid).create_time()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pid_create_time = None
    job.update(
        {
            "state": "running",
            "pid": process.pid,
            "pid_create_time": pid_create_time,
        }
    )
    save_json(paths["job"], job)
    return job


def wait_for_odm(
    paths: dict[str, Path],
    poll_interval_seconds: float = 15.0,
    on_wait: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """等待任务结束；状态通知由调用方决定是否输出。"""
    poll_interval_seconds = max(2.0, poll_interval_seconds)
    while True:
        status = get_odm_status(paths)
        if status["state"] == "succeeded":
            return status
        if status["state"] in {"failed", "interrupted"}:
            raise RuntimeError(f"ODM 任务失败或中断，请查看：{paths['log']}")
        if status["state"] == "not_started":
            raise RuntimeError("ODM 任务尚未启动。")
        if on_wait:
            on_wait(status)
        time.sleep(poll_interval_seconds)


def validate_orthomosaic(
    paths: dict[str, Path],
    odm_home: Path,
    proj_data_directory: Path | None = None,
) -> dict[str, Any]:
    """用 GDAL 检查正射 GeoTIFF；返回结果但不额外生成报告文件。"""
    if not paths["orthophoto"].is_file():
        raise FileNotFoundError(f"正射影像不存在：{paths['orthophoto']}")
    gdalinfo = (
        Path(odm_home) / "SuperBuild" / "install" / "bin" / "gdalinfo.exe"
    )
    if not gdalinfo.is_file():
        raise FileNotFoundError(f"gdalinfo 不存在：{gdalinfo}")

    completed = subprocess.run(
        [str(gdalinfo), "-json", str(paths["orthophoto"])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=gdal_environment(odm_home, proj_data_directory),
    )
    info = json.loads(completed.stdout)
    size = info.get("size", [None, None])
    transform = info.get("geoTransform", [])
    coordinate_system = info.get("coordinateSystem", {})
    coordinate_wkt = str(coordinate_system.get("wkt") or "")
    coordinate_name_match = re.search(
        r'(?:PROJCRS|GEOGCRS)\["([^"]+)"',
        coordinate_wkt,
    )
    bands = [
        {
            "index": index,
            "description": band.get("description"),
            "color_interpretation": band.get("colorInterpretation"),
            "data_type": band.get("type"),
        }
        for index, band in enumerate(info.get("bands", []), start=1)
    ]
    warnings: list[str] = []
    if not paths["dsm"].is_file():
        warnings.append("未生成 DSM；正射 GeoTIFF 仍可独立使用。")
    if not paths["odm_report"].is_file():
        warnings.append("ODM PDF 报告未生成；不影响正射 GeoTIFF 的基础可用性。")
    report = {
        "qa_pass": bool(
            len(size) == 2
            and all(isinstance(value, int) and value > 0 for value in size)
            and coordinate_system.get("wkt")
            and len(transform) >= 6
            and bands
        ),
        "raster_size": size,
        "pixel_size": (
            [abs(transform[1]), abs(transform[5])]
            if len(transform) >= 6
            else None
        ),
        "coordinate_system": (
            coordinate_name_match.group(1) if coordinate_name_match else "unknown"
        ),
        "bands": bands,
        "dsm_exists": paths["dsm"].is_file(),
        "odm_report_exists": paths["odm_report"].is_file(),
        "warnings": warnings,
    }
    return report
