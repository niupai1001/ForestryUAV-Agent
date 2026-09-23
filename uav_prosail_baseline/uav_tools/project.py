"""正射工作区、输入暂存和 JSON 状态工具。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def save_json(path: Path, value: dict[str, Any]) -> Path:
    """以 UTF-8 原子写入 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def workspace_paths(workspace_root: Path) -> dict[str, Path]:
    """返回统一工作区路径；原生运行时要求工作区为纯 ASCII 路径。"""
    workspace_root = Path(workspace_root).absolute()
    if not str(workspace_root).isascii():
        raise ValueError(f"工作区必须使用纯英文路径：{workspace_root}")
    return {
        "workspace": workspace_root,
        "runtime": workspace_root / "runtime",
        "odm_home": workspace_root / "runtime" / "ODM",
        "python_home": workspace_root / "runtime" / "python",
        "jobs": workspace_root / "jobs",
    }


def project_paths(workspace_root: Path, project_name: str) -> dict[str, Path]:
    """返回工作区内一个 ODM 任务的全部标准路径。"""
    workspace = workspace_paths(workspace_root)
    project_root = workspace["jobs"]
    project = project_root / project_name
    return {
        **workspace,
        "project_root": project_root,
        "project": project,
        "images": project / "images",
        "job": project / "job.json",
        "log": project / "processing.log",
        "manifest": project / "input_manifest.json",
        "orthophoto": project / "odm_orthophoto" / "odm_orthophoto.tif",
        "dsm": project / "odm_dem" / "dsm.tif",
        "odm_report": project / "odm_report" / "report.pdf",
        "previews": project / "previews",
        "result": project / "result.json",
    }


def ensure_free_space(path: Path, minimum_gb: float = 15.0) -> float:
    """检查磁盘空间并返回可用 GB。"""
    path = Path(path)
    anchor = path if path.exists() else Path(path.anchor)
    free_gb = shutil.disk_usage(anchor).free / 1024**3
    if free_gb < minimum_gb:
        raise RuntimeError(
            f"至少需要 {minimum_gb:.1f} GB 可用空间，当前只有 {free_gb:.1f} GB。"
        )
    return free_gb


def prepare_proj_data_directory(
    odm_home: Path,
    ascii_directory: Path,
) -> Path:
    """为不能读取非 ASCII 路径的 PROJ/PDAL 准备数据目录。"""
    source = (
        Path(odm_home)
        / "SuperBuild"
        / "install"
        / "bin"
        / "data"
        / "proj"
    ).resolve()
    if not (source / "proj.db").is_file():
        raise FileNotFoundError(f"PROJ 数据库不存在：{source / 'proj.db'}")
    if str(source).isascii():
        return source

    target = Path(ascii_directory).absolute()
    if not str(target).isascii():
        raise ValueError(f"PROJ 兼容目录必须是纯 ASCII 路径：{target}")
    if target.exists():
        if not (target / "proj.db").is_file():
            raise RuntimeError(f"PROJ 兼容目录不完整：{target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    return target


def stage_images(
    images: Iterable[Path],
    target_directory: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """硬链接或复制指定影像，不扫描额外目录，不改写原文件。"""
    sources = [Path(path) for path in images]
    if not sources:
        raise RuntimeError("没有需要暂存的影像。")
    target_directory = Path(target_directory)
    target_directory.mkdir(parents=True, exist_ok=True)

    source_names = {path.name.lower() for path in sources}
    unexpected = [
        path.name
        for path in target_directory.iterdir()
        if path.is_file() and path.name.lower() not in source_names
    ]
    if unexpected:
        raise RuntimeError(
            "目标 images 目录存在其他航次文件：" + ", ".join(unexpected[:10])
        )

    linked = copied = reused = 0
    records = []
    for source in sources:
        target = target_directory / source.name
        if target.exists():
            if target.stat().st_size != source.stat().st_size:
                raise RuntimeError(f"同名目标文件大小不同：{target}")
            method = "existing"
            reused += 1
        else:
            try:
                os.link(source, target)
                method = "hardlink"
                linked += 1
            except OSError:
                shutil.copy2(source, target)
                method = "copy"
                copied += 1
        records.append(
            {
                "source": str(source),
                "target": str(target),
                "size_bytes": source.stat().st_size,
                "method": method,
            }
        )

    manifest = {
        "created_at": now_iso(),
        "image_count": len(records),
        "hardlinked": linked,
        "copied": copied,
        "reused": reused,
        "files": records,
    }
    save_json(manifest_path, manifest)
    return {key: value for key, value in manifest.items() if key != "files"}


def archive_processing_outputs(
    paths: dict[str, Path],
    backup_root: Path | None = None,
) -> dict[str, Any]:
    """把失败任务的派生结果移到可恢复备份，保留输入和处理日志。"""
    project = Path(paths["project"]).resolve()
    if not project.is_dir():
        raise FileNotFoundError(f"ODM 项目不存在：{project}")

    preserved = {"images", "input_manifest.json", "processing.log"}
    outputs = sorted(
        (path for path in project.iterdir() if path.name not in preserved),
        key=lambda path: path.name.lower(),
    )
    if not outputs:
        raise RuntimeError("项目中没有需要归档的处理结果。")

    backup_root = Path(backup_root or paths["workspace"] / "archive")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = backup_root / f"{project.name}_{stamp}"
    backup.mkdir(parents=True, exist_ok=False)

    moved: list[dict[str, str]] = []
    for source in outputs:
        if source.resolve().parent != project:
            raise RuntimeError(f"拒绝移动项目目录外的路径：{source}")
        target = backup / source.name
        shutil.move(str(source), str(target))
        moved.append({"source": str(source), "backup": str(target)})

    manifest = {
        "created_at": now_iso(),
        "project": str(project),
        "backup": str(backup),
        "preserved": sorted(preserved),
        "moved": moved,
    }
    manifest_path = save_json(backup / "recovery_manifest.json", manifest)
    return {
        "backup": str(backup),
        "manifest": str(manifest_path),
        "moved_count": len(moved),
        "moved_names": [Path(item["source"]).name for item in moved],
    }
