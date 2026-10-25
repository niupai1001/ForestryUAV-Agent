"""作业提交与观察：代码执行、影像/激光雷达专业能力。

所有提交都经同一个受守卫通道登记，再交给既有 Jobs；返回持久 job_id，观察由
``wait_job`` 完成。这里不实现第二套队列，也不在提交时推断计算结果。

提交会创建新的执行体，声明为独占；观察只读作业状态，可与读取类工具并行。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal

from pydantic_ai import RunContext

from ..contracts import Failure
from ..jobs import TERMINAL
from .scheduling import EXCLUSIVE
from .tools import ToolRegistry, registrar


def register_job_tools(registry: ToolRegistry, *, engine, jobs, roles, store,
                       journal: Callable[[Any, str, dict, Callable], Any]) -> Callable[[], None]:
    """``engine`` 只用于交给专业能力模块的提交函数，不暴露运行循环。"""
    register, cleanup = registrar(registry, "job_tools")

    def execute(ctx, kind, args, operation):
        return journal(ctx, kind, args, operation)

    async def code_execute(ctx: RunContext, code: str, input_refs: list[str], seconds: int = 300,
                           environment_artifact_id: str | None = None) -> dict:
        """断网Python作业，输入只读到/inputs/<resource_id>，输出/output。
        默认标准库；environment_artifact_id复用确认environment.zip，绑定镜像/SHA。返回持久job_id，用wait_job观察，不重复提交。
        """
        arguments = {"code": code, "input_refs": input_refs, "seconds": seconds}
        if environment_artifact_id is not None:
            arguments["environment_artifact_id"] = environment_artifact_id
        receipt = await asyncio.to_thread(execute, ctx, "code.execute", arguments,
            lambda action_id: jobs.submit(ctx.deps.task_id, ctx.deps.revision, action_id,
                code, input_refs, seconds, environment_artifact_id=environment_artifact_id))
        return jobs.model_receipt(receipt)

    async def process_imagery(ctx: RunContext, operation: Literal["inspect", "build_lut", "invert", "orthomosaic"],
                              input_refs: dict[str, str] | None = None, declaration_ref: str | None = None,
                              seconds: int = 900, resume_job_id: str | None = None) -> dict:
        """科学影像按文件引用处理，不把整批像素注入模型。
        inspect:raster；build_lut:空输入+参数声明JSON；invert:raster/lut+定标/波段声明JSON；
        orthomosaic:flight→已发送文件夹ID，或逐航片角色→资源ID；另需ODM声明JSON。
        文件夹由后端展开（当前≤200张顶层航片），不将影像清单/像素塞进上下文。
        input_refs省略时使用唯一已授权航次目录/已生成正射。先调用本工具，不要逐张文本读取TIFF或抽取单个拍摄组。
        若已登记输入一致、方法一致的正确成果，本工具校验并封存至当前会话，返回reused及真实产物；直接交付，不再wait_job。
        沿用Jobs/只读输入/同会话产物/取消和恢复；返回job_id后wait_job，正确LUT/结果直接复用。
        不猜反射率、波段或模型参数，不把光谱残差当现场LAI精度。
        缺少声明时返回所需JSON Schema，不启动作业。
        """
        from ..imagery import ImageryRequest, submit_imagery, automatic_imagery_inputs
        if input_refs is None:
            input_refs = automatic_imagery_inputs(engine, ctx.deps.task_id, operation)
        if operation != "inspect" and not declaration_ref:
            if engine.config.imagery_result_catalog:
                from ..imagery_catalog import reuse_verified
                candidate = ImageryRequest(request_id='native-imagery', expected_spec_revision=ctx.deps.revision,
                    operation=operation, input_refs=input_refs, seconds=seconds, resume_job_id=resume_job_id)
                reused = await asyncio.to_thread(execute, ctx, 'code.execute',
                    {**candidate.model_dump(), 'input_refs':list(input_refs.values())},
                    lambda action_id: reuse_verified(engine, ctx.deps, action_id, candidate) or {
                        'kind':'not_found', 'message':'没有匹配的已验证成果；本次未启动计算，需要有来源的参数声明。'})
                if reused.get('kind') != 'not_found':
                    return reused
            from ..imagery_schema import InversionDeclaration, LutDeclaration, OdmDeclaration
            schema = {"build_lut": LutDeclaration, "invert": InversionDeclaration, "orthomosaic": OdmDeclaration}[operation]
            recipe = {}
            if operation == "build_lut":
                from ..imagery_schema import LEGACY_RANGES
                recipe = {"legacy_ranges": LEGACY_RANGES, "seed": 20260914, "lut_size": 500,
                    "parameter_source": "迁移自旧run_prosail.py的探索性先验，不是本航次现场标定",
                    "required_evidence": "当前航次太阳/观测几何、波段及响应来源仍须读取；不得套用旧太阳角度。"}
            return {"status": "declaration_required", "schema": schema.model_json_schema(), "legacy_recipe": recipe,
                    "next": "依据元数据/用户资料构建声明JSON，write_file保存为本会话产物后传入declaration_ref；缺少依据先说明，禁止猜测定标/波段。"}
        payload = ImageryRequest(request_id="native-imagery", expected_spec_revision=ctx.deps.revision,
            operation=operation, input_refs=input_refs, declaration_ref=declaration_ref, seconds=seconds,
            resume_job_id=resume_job_id)
        arguments = {**payload.model_dump(), "input_refs": [*input_refs.values(), *([declaration_ref] if declaration_ref else [])]}
        receipt = await asyncio.to_thread(execute, ctx, "code.execute", arguments,
            lambda action_id: submit_imagery(engine, ctx.deps, action_id, payload))
        return jobs.model_receipt(receipt)

    async def process_lidar_terrain(ctx: RunContext, cloud_ref: str, declaration_ref: str,
                                   seconds: int = 900, resume_job_id: str | None = None,
                                   resume_refs: dict[str, str] | None = None) -> dict:
        """原始米制高程LAS+授权声明JSON，经固定SMRF/IDW生成地形、支持和HAG。
        只用任务冻结科学镜像/已有code.execute授权和Jobs；返回job_id后wait_job。
        部分科学交付，不含单木/独立精度。已归一化、未知垂直/单位或不可行指定方法拒绝，不替换。
        """
        from ..lidar_terrain import TerrainRequest, submit_terrain

        payload = TerrainRequest(request_id="native-terrain", expected_spec_revision=ctx.deps.revision,
                                 cloud_ref=cloud_ref, declaration_ref=declaration_ref, seconds=seconds,
                                 resume_job_id=resume_job_id, resume_refs=resume_refs or {})
        arguments = {**payload.model_dump(), "input_refs": [cloud_ref, declaration_ref, *payload.resume_refs.values()]}
        receipt = await asyncio.to_thread(execute, ctx, "code.execute", arguments,
            lambda action_id: submit_terrain(engine, ctx.deps, action_id, payload))
        return jobs.model_receipt(receipt)

    async def process_lidar_canopy(ctx: RunContext, stage: str, input_refs: dict[str, str] | None = None,
                                  seconds: int = 900) -> dict:
        """固定lidR配方的chm/seeds/instances独立阶段；文件角色及授权须完整。
        chm:working/terrain_quality/terrain_support；seeds:chm/chm_record；
        instances:working/original/hag/chm/chm_record/seeds/seed_record。
        返回job_id后wait_job；复用已完成阶段，不重新归一化或做现场准确性推断。
        input_refs可使用同会话产物ID，无须重新登记；省略时按完整产品批次自动衔接已有阶段。
        """
        from ..lidar_canopy import CanopyStageRequest, submit_canopy
        from ..lidar_products import automatic_inputs

        if input_refs is None:
            input_refs = automatic_inputs(engine, ctx.deps.task_id, stage)
        payload = CanopyStageRequest(request_id="native-canopy", expected_spec_revision=ctx.deps.revision,
                                     stage=stage, input_refs=input_refs, seconds=seconds)
        arguments = {**payload.model_dump(), "input_refs": list(input_refs.values())}
        receipt = await asyncio.to_thread(execute, ctx, "code.execute", arguments,
            lambda action_id: submit_canopy(engine, ctx.deps, action_id, payload))
        return jobs.model_receipt(receipt)

    async def apply_lidar_roi(ctx: RunContext, input_refs: dict[str, str], seconds: int = 600) -> dict:
        """明确投影ROI的面积/指标/标签后处理，不重算地形或实例。
        输入角色segmented/chm/trees/vectors/quality/instance_record/roi均须是授权单文件。
        ROI JSON明确米制投影CRS/多边形/来源说明；输出只过滤treeID，保留其它点属性和原ID。
        """
        from ..lidar_roi import RoiRequest, submit_roi

        payload = RoiRequest(request_id="native-roi", expected_spec_revision=ctx.deps.revision,
                             input_refs=input_refs, seconds=seconds)
        arguments = {**payload.model_dump(), "input_refs": list(input_refs.values())}
        receipt = await asyncio.to_thread(execute, ctx, "code.execute", arguments,
            lambda action_id: submit_roi(engine, ctx.deps, action_id, payload))
        return jobs.model_receipt(receipt)

    async def wait_job(ctx: RunContext, job_id: str, seconds: int = 45) -> dict:
        """等待当前任务作业最多seconds秒（1–120），到期如实返回仍在运行，可用同一job_id再查或继续其他交流；应用轮询不调用模型。终态含有界日志末尾/截断标记。失败产物可按artifact_ids读回，先检查并复用正确结果，不能把文件存在当通过或盲目重算。"""
        with store.transaction() as db:
            roles.authorize(db, ctx.deps, "job.observe", {"job_id": job_id})
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + max(1, min(int(seconds or 45), 120))
        while True:
            state = store.task(ctx.deps.task_id)
            if state["pause_requested"] or state["cancellation_requested"] or state["spec_revision"] != ctx.deps.revision:
                raise Failure("EXECUTION_STOPPED", "作业等待被用户控制中断", 409)
            try:
                job = jobs.read(job_id, ctx.deps.task_id)
            except Failure as error:
                if error.code != "NOT_FOUND":
                    raise
                return await asyncio.to_thread(execute, ctx, "job.observe", {"job_id": job_id},
                    lambda _: jobs.observation(job_id, ctx.deps.task_id))
            if job["status"] in TERMINAL or job["blocker"]:
                return await asyncio.to_thread(execute, ctx, "job.observe", {"job_id": job_id},
                    lambda _: jobs.observation(job_id, ctx.deps.task_id))
            if loop.time() >= deadline:
                # 单次等待只回答「现在的真实状态」；计算继续，作业有自己的执行预算。
                return {"kind": "running", "still_running": True,
                        "job": {**jobs.ref_summary(job), "blocker": job.get("blocker")},
                        "waited_seconds": round(loop.time() - started),
                        "next": "作业仍在运行，计算没有中断。可继续其他交流，或再次wait_job查看；不要重复提交同一作业。"}
            await asyncio.sleep(1)

    register("code_execute", "code.execute", code_execute, EXCLUSIVE)
    register("process_imagery", "code.execute", process_imagery, EXCLUSIVE)
    register("process_lidar_terrain", "code.execute", process_lidar_terrain, EXCLUSIVE)
    register("process_lidar_canopy", "code.execute", process_lidar_canopy, EXCLUSIVE)
    register("apply_lidar_roi", "code.execute", apply_lidar_roi, EXCLUSIVE)
    # 观察本身会写一条动作记录，不是纯读；保持默认串行，避免与提交交错。
    register("wait_job", "job.observe", wait_job)
    return cleanup
