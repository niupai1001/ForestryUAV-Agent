"""环境诊断、依赖解析/获取与运行环境准备。

诊断只读；依赖获取与准备会产生真实下载和构建，因此声明为独占，并沿用既有
审批与作业通道。
"""

from __future__ import annotations

import asyncio
import inspect as pyinspect
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

from pydantic_ai import RunContext

from ..acquisition import PackageRequest
from ..contracts import Failure
from .scheduling import EXCLUSIVE, PARALLEL
from .tools import ToolRegistry, registrar


def register_environment_tools(registry: ToolRegistry, *, config, store, resolution, acquisition,
                               environments, jobs, doctor,
                               journal: Callable[[Any, str, dict, Callable], Any]) -> Callable[[], None]:
    register, cleanup = registrar(registry, "environment_tools")

    def execute(ctx, kind, args, operation):
        return journal(ctx, kind, args, operation)

    async def inspect_environment(ctx: RunContext) -> dict:
        """实际检查本机软件/硬件/执行器就绪状态，返回有时间戳的清单。只读诊断，不安装依赖。"""
        if store.task(ctx.deps.task_id)["config_snapshot"].get("context_classification") == "public":
            raise Failure("PERMISSION_DENIED", "公开任务不能读取本机私密环境信息", 403)
        value = execute(ctx, "environment.inspect", {}, lambda _: doctor(config, probe_search=False))
        return await value if pyinspect.isawaitable(value) else value

    async def resolve_dependencies(ctx: RunContext, requirements: list[str],
            target_artifact_id: UUID | None = None, approval_id: str | None = None,
            built_wheel_artifact_ids: list[str] | None = None, refresh: bool = False) -> dict:
        """PEP508解析wheel闭包或源码候选；无target先断网无输入探测(需code.execute)，wait_job后传目标ID。
        built_wheel_artifact_ids复用源码元数据；同请求复用核验快照，refresh=true重新联网须审批。下载IDs合并安装。
        """
        target = str(target_artifact_id) if target_artifact_id is not None else None
        args = {"requirements": requirements, "target_artifact_id": target, "approval_id": approval_id}
        if built_wheel_artifact_ids is not None:
            args['built_wheel_artifact_ids'] = built_wheel_artifact_ids
        if refresh:
            args['refresh'] = True
        receipt = await asyncio.to_thread(execute, ctx, "environment.acquire", args,
            lambda action: resolution.resolve(ctx.deps, action, requirements, target, approval_id,
                built_wheel_artifact_ids=built_wheel_artifact_ids, refresh=refresh))
        return jobs.model_receipt(receipt)

    async def acquire_dependencies(ctx: RunContext, wheels: list[PackageRequest] | None = None,
            source: Literal["pypi", "tuna"] = "pypi", approval_id: str | None = None,
            resolution_lock_artifact_id: str | None = None, source_packages: list[PackageRequest] | None = None,
            refresh: bool = False) -> dict:
        """精确name/version/filename下载1–32文件/128MiB，核对PyPI大小/SHA；不执行包。
        wheels只收.whl；source_packages收.tar.gz/.zip。私密任务审批后带approval_id重调。
        解析锁按用途绑定wheels或源码候选；下载的*_artifact_ids交prepare_environment。
        默认核验复用相同请求的已下载文件；refresh=true才重新获取并走原审批。
        """
        payload = [w.model_dump() for w in (wheels or [])]
        args = {"wheels": payload, "source": source, "approval_id": approval_id}
        sources = [s.model_dump() for s in (source_packages or [])]
        if sources:
            args['source_packages'] = sources
        if resolution_lock_artifact_id is not None:
            args["resolution_lock_artifact_id"] = resolution_lock_artifact_id
        if refresh:
            args['refresh'] = True
        return await asyncio.to_thread(execute, ctx, "environment.acquire", args,
            lambda action: acquisition.acquire(ctx.deps, action, payload, source, approval_id, resolution_lock_artifact_id,
                source_packages=sources, refresh=refresh))

    async def prepare_environment(ctx: RunContext, wheel_refs: list[str] | None = None, seconds: int = 300,
            wheel_artifact_ids: list[str] | None = None, source_refs: list[str] | None = None,
            source_artifact_ids: list[str] | None = None, build_only: bool = False,
            inspect_build: Literal['static', 'dynamic'] | None = None) -> dict:
        """*_refs用本地resource_id；下载用wheel_artifact_ids/source_artifact_ids。
        源码需code.execute；static取需求补wheel再dynamic。inspect_build与build_only互斥，后者产wheel；默认装环境，提交后wait_job。
        """
        args = {"input_refs": [*(wheel_refs or []), *(source_refs or [])], "seconds": seconds}
        if build_only:
            args['build_only'] = True
        if inspect_build:
            args['inspect_build'] = inspect_build
        if source_refs is not None:
            args["source_refs"] = source_refs
        if source_artifact_ids is not None:
            args['source_artifact_ids'] = source_artifact_ids
        if wheel_artifact_ids is not None:
            args["wheel_artifact_ids"] = wheel_artifact_ids
        receipt = await asyncio.to_thread(execute, ctx, "environment.prepare", args,
            lambda action_id: environments.prepare(ctx.deps.task_id, ctx.deps.revision, action_id, wheel_refs or [], seconds,
                wheel_artifact_ids=wheel_artifact_ids, source_refs=source_refs, source_artifact_ids=source_artifact_ids,
                build_only=build_only, inspect_build=inspect_build))
        return jobs.model_receipt(receipt)

    register("inspect_environment", "environment.inspect", inspect_environment, PARALLEL)
    register("resolve_dependencies", "environment.acquire", resolve_dependencies, EXCLUSIVE)
    register("acquire_dependencies", "environment.acquire", acquire_dependencies, EXCLUSIVE)
    register("prepare_environment", "environment.prepare", prepare_environment, EXCLUSIVE)
    return cleanup
