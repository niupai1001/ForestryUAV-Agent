"""文件与产物读取能力。

能力组件只依赖资源仓储、任务状态和唯一的受守卫调用通道（``journal``），不导入
运行循环，也不自己写数据库。注册函数返回清理句柄，装配失败时可以撤回。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext

from ..contracts import Failure
from ..resources import Resources
from .scheduling import EXCLUSIVE, PARALLEL
from .tools import ToolRegistry, registrar


def register_file_tools(registry: ToolRegistry, *, resources: Resources, store,
                        journal: Callable[[Any, str, dict, Callable], Any],
                        assignment: dict | None = None) -> Callable[[], None]:
    """注册会话输入、工作区文件与封存产物的读取工具。

    读取工具明确只读且并发安全，声明为并行；选择的动作会改动索引，保持串行。
    """
    register, cleanup = registrar(registry, "file_tools")

    def execute(ctx, kind, args, operation):
        return journal(ctx, kind, args, operation)

    def list_inputs(ctx: RunContext, offset: int = 0, limit: int = 20) -> dict:
        """分页列出当前任务获授权的输入ID/文件名，offset从0开始，每页1–20项；不授予新权限。"""
        if offset < 0 or not 1 <= limit <= 20:
            raise Failure('SCHEMA_INVALID', '来源分页offset须非负，每页1至20项')

        def operation(_):
            refs = assignment["effective_input_refs"] if assignment else store.task(ctx.deps.task_id)["input_refs"]
            page = refs[offset:offset + limit]
            return {"resources": [{k: r[k] for k in ["resource_id", "name", "kind", "classification"]}
                                  for r in resources.all() if r["resource_id"] in page],
                    'next': offset + len(page), 'has_more': offset + len(page) < len(refs), 'total': len(refs)}

        return execute(ctx, "fs.inputs", {'offset': offset, 'limit': limit}, operation)

    def select_input_files(ctx: RunContext, resource_id: str, relative_paths: list[str]) -> dict:
        """选择已发送文件夹内要用于代码/科学作业的文件，返回只读ID。继承原文件夹权限，无需用户再登记。"""
        return execute(ctx, 'fs.inputs', {'resource_id': resource_id, 'relative_paths': relative_paths},
                       lambda _: {'resources': resources.select_files(ctx.deps.task_id, resource_id, relative_paths)})

    def list_files(ctx: RunContext, resource_id: str, relative_path: str = "", offset: int = 0) -> dict:
        """分页列出授权输入目录。relative_path 必须是根目录下的相对路径。"""
        return execute(ctx, "fs.list", {"resource_id": resource_id, "relative_path": relative_path,
                                        "offset": offset},
                       lambda _: resources.list(resource_id, relative_path, max(0, offset), task_id=ctx.deps.task_id))

    def read_file(ctx: RunContext, resource_id: str, relative_path: str = "", offset: int = 0) -> dict:
        """读取授权 UTF-8 文本/CSV 的有界字节窗口，结果包含版本和分页游标。"""
        return execute(ctx, "fs.read", {"resource_id": resource_id, "relative_path": relative_path,
                                        "offset": offset},
                       lambda _: resources.read(resource_id, relative_path, max(0, offset), task_id=ctx.deps.task_id))

    def inspect_artifact(ctx: RunContext, artifact_id: str) -> dict:
        """检查本任务已生成产物的校验和、可读性和真实版本；不证明业务语义正确。"""

        def operation(_):
            artifact = resources.artifact(artifact_id, ctx.deps.task_id)
            return {k: v for k, v in artifact.items() if k != "locator"}

        return execute(ctx, "artifact.inspect", {"artifact_id": artifact_id}, operation)

    def read_artifact(ctx: RunContext, artifact_id: str, offset: int = 0) -> dict:
        """按最多4000字节窗口读回本任务封存的UTF-8产物，含SHA256。仅has_more=true时以offset=next继续；正文足以完成任务时可停止读取。"""
        return execute(ctx, "artifact.read", {"artifact_id": artifact_id, "offset": offset},
                       lambda _: resources.read_artifact(ctx.deps.task_id, artifact_id, offset))

    for func, kind, mode in ((list_inputs, "fs.inputs", PARALLEL), (select_input_files, "fs.inputs", EXCLUSIVE),
                             (list_files, "fs.list", PARALLEL), (read_file, "fs.read", PARALLEL),
                             (inspect_artifact, "artifact.inspect", PARALLEL),
                             (read_artifact, "artifact.read", PARALLEL)):
        register(func.__name__, kind, func, mode)
    return cleanup
