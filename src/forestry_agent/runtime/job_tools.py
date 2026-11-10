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
        """执行断网Python脚本，处理表格/CSV、缺失值清理、单位换算和通用计算。
        输入只读到/inputs/<resource_id>，输出/output。
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
                              seconds: int = 900, resume_job_id: str | None = None,
                              reuse_candidate_id: str | None = None, adoption_note: str | None = None) -> dict:
        """科学影像按文件引用处理，不把整批像素注入模型。
        inspect:raster；build_lut:空输入+参数声明JSON；invert:raster/lut+定标/波段声明JSON；
        orthomosaic:flight→已发送文件夹ID，或逐航片角色→资源ID；另需ODM声明JSON。
        文件夹由后端展开（当前≤200张顶层航片），不将影像清单/像素塞进上下文。
        input_refs省略时使用唯一已授权航次目录/已生成正射；不能把单个多波段拍摄组当整航次，不用文本工具读取TIFF。
        无声明时仅返回匹配数据的旧成果候选及原参数，不导入、不宣称完成；核对本次要求并提供声明后才校验复用。
        输入/声明/算法/预处理/镜像身份完整匹配才封存；反演复用须提供原LUT。旧成果缺血缘仅给候选，不猜测补录。
        旧候选无完整身份但适合当前目标，可传reuse_candidate_id和adoption_note明确采用其旧参数/限制，仍须声明；
        保存采用依据与未知项，不宣称当前算法等价。已知身份不符不能绕过，失效候选不会启动计算。
        返回reused及真实产物后直接交付，不再wait_job。
        沿用Jobs/只读输入/同会话产物/取消和恢复；返回job_id后wait_job，正确LUT/结果直接复用。
        不猜反射率、波段或模型参数，不把光谱残差当现场LAI精度。
        LAI是叶面积指数(m²/m²)，Cab为叶绿素含量；先验范围来自本次LUT声明，不能套用当前默认值解释历史LUT。
        先验上界不是森林普适生理阈值；接近边界只提示敏感性/辨识问题的候选，不凭统计证明饱和或定标错误。
        ODM camera辐射归一化不等于独立反射率定标，适用性须核对本航次元数据与来源。
        缺少声明时返回所需JSON Schema，不启动作业。
        """
        from ..imagery import ImageryRequest, submit_imagery, automatic_imagery_inputs
        if input_refs is None:
            input_refs = automatic_imagery_inputs(engine, ctx.deps.task_id, operation)
        if operation != "inspect" and not declaration_ref:
            if engine.config.imagery_result_catalog:
                from ..imagery_catalog import reuse_verified
                candidate = ImageryRequest(request_id='native-imagery', expected_spec_revision=ctx.deps.revision,
                    operation=operation, input_refs=input_refs, seconds=seconds, resume_job_id=resume_job_id,
                    reuse_candidate_id=reuse_candidate_id, adoption_note=adoption_note)
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
            resume_job_id=resume_job_id, reuse_candidate_id=reuse_candidate_id, adoption_note=adoption_note)
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
        """用已有点云产品生成冠层高度图(CHM)、树顶种子和单木实例分割。
        固定lidR配方的chm/seeds/instances独立阶段；文件角色及授权须完整。
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

    async def set_job_follow_up(ctx: RunContext, job_id: str, instruction: str | None = None) -> dict:
        """登记后台作业完成后尚欠的分析/报告，自动接回原任务；只需要文件时传null仅通知。
        提交长作业后，若用户目标仍欠分析或报告，登记具体剩余工作，不靠反复wait_job轮询。
        不改变权限、科学参数、执行预算或原任务目标；已经观察/消费的终态不能重复唤醒。
        """
        return await asyncio.to_thread(execute, ctx, 'job.observe',
            {'job_id': job_id, 'instruction': instruction},
            lambda _: jobs.configure_completion(job_id, ctx.deps.task_id, instruction))

    async def wait_job(ctx: RunContext, job_id: str, seconds: int = 0) -> dict:
        """查询当前任务作业，默认立即返回；显式等待最多seconds秒（0–120），到期如实返回仍在运行，可用同一job_id再查或继续其他交流；应用轮询不调用模型。终态含有界日志末尾/截断标记。失败产物可按artifact_ids读回，先检查并复用正确结果，不能把文件存在当通过或盲目重算。"""
        with store.transaction() as db:
            roles.authorize(db, ctx.deps, "job.observe", {"job_id": job_id})
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + max(0, min(int(seconds), 120))
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
                result = await asyncio.to_thread(execute, ctx, "job.observe", {"job_id": job_id},
                    lambda _: jobs.observation(job_id, ctx.deps.task_id))
                if result.get('job', {}).get('status') in TERMINAL:
                    jobs.acknowledge_terminal(job_id, ctx.deps.task_id, ctx.deps.turn_id)
                return result
            if loop.time() >= deadline:
                # 单次等待只回答「现在的真实状态」；计算继续，作业有自己的执行预算。
                return {"kind": "running", "still_running": True,
                        "job": {**jobs.ref_summary(job), "blocker": job.get("blocker")},
                        "waited_seconds": round(loop.time() - started),
                        "next": "作业仍在运行，计算没有中断。可继续其他交流，或再次wait_job查看；不要重复提交同一作业。"}
            with store.snapshot() as db:
                pending_input = db.execute("SELECT 1 FROM turns t JOIN turn_requests r ON r.turn_id=t.id "
                    "WHERE t.task_id=? AND t.revision=? AND t.status='queued' AND t.id!=? LIMIT 1",
                    (ctx.deps.task_id, ctx.deps.revision, ctx.deps.turn_id)).fetchone()
            if pending_input:
                return {'kind': 'running', 'still_running': True, 'yielded_to_user': True,
                    'job': jobs.ref_summary(job), 'next': '新消息已到达，先处理用户指令；后台计算继续。'}
            await asyncio.sleep(1)

    register("code_execute", "code.execute", code_execute, EXCLUSIVE)
    register("process_imagery", "code.execute", process_imagery, EXCLUSIVE,
        summary_en='Inspect multispectral raster metadata, reconstruct orthomosaics, build PROSAIL LUTs or invert LAI and Cab with declared science inputs.',
        accepts=('authorized flight images or GeoTIFF', 'PROSAIL LUT for inversion', 'sourced JSON method declaration'),
        produces=('metadata observation', 'verified reuse candidates', 'durable JobRef or immutable science artifacts'),
        modalities=('raster', 'multispectral', 'json'), permission_tier='task_capability_and_input_grants')
    register("process_lidar_terrain", "code.execute", process_lidar_terrain, EXCLUSIVE)
    register("process_lidar_canopy", "code.execute", process_lidar_canopy, EXCLUSIVE)
    register("apply_lidar_roi", "code.execute", apply_lidar_roi, EXCLUSIVE)
    # 观察本身会写一条动作记录，不是纯读；保持默认串行，避免与提交交错。
    register("wait_job", "job.observe", wait_job)
    register("set_job_follow_up", "job.observe", set_job_follow_up)
    return cleanup
