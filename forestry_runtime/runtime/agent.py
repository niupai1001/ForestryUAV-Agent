"""PydanticAI model/tool loop with project workspace and execution services."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, is_dataclass
import json
import os
import time
from typing import Any, Callable

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    CancellationToken,
    RunContext,
    Tool,
    ToolReturn,
)
from pydantic_ai.capabilities import Hooks, ToolSearch
from pydantic_ai.exceptions import (
    ModelAPIError,
    RunCancelled,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    SystemPromptPart,
    InstructionPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness import (
    ClearToolResults,
    DeduplicateFileReads,
    StepPersistence,
    SummarizingCompaction,
    TieredCompaction,
)
from pydantic_ai_harness.step_persistence import SqliteStepStore, continue_run

from .domain_registry import DOMAIN_TOOL_GROUPS, matching_domain_tools, plugin_enabled
from .context import ContextCompiler
from .capabilities.runtime import GENERIC_DEFINITIONS, RuntimeTools
from .tool_protocol import execution_failure, inline_schema
from .workspace import WorkspaceRegistry, is_host_path


SYSTEM = """你是面向文件、代码和低空林草遥感任务的执行 Agent。
围绕用户目标选择行动；需要事实时使用工具，已有信息足够时直接回答。
工具结果是观察。失败后应改变参数、代码或方法，或取得新证据；不要重复相同的失败调用。
调用领域模型时，必填模型参数必须来自用户、已读取文件或可引用的知识来源；
缺少依据时先询问或检索，不得自行补默认值或猜测。
项目知识正文不会自动注入。需要项目方法、规范、历史事实、参数依据或用户要求来源时，
先调用 knowledge_search，必要时再用 knowledge_read；文件和栅格的真实状态使用对应检查工具。
knowledge_search 已返回与问题直接相关的正文时，应使用该证据作答；只有结果为空或明显无关时
才改写查询。只有现有正文确实缺少相邻语境时才调用 knowledge_read，不要为寻找不同措辞重复检索。
使用项目知识回答时，必须逐字复制检索结果中对应的 citation 字段作为来源。
影像中存在RTK元数据或未知状态码，不等于RTK成功、固定解或达到某种精度；
只有取得厂商状态码定义或独立质量证据时才能解释其含义。
盘点林业UAV目录时，主航线、起飞前/起飞后参考板和已有地理成果必须分开报告；
已有GeoTIFF通过自动元数据检查也不等于接缝、冠层形变或绝对几何精度已经验证。
地理坐标系的像元大小单位是度；没有执行有依据的投影换算时，不得标成米。
inspect_uav_dataset或inspect_uav_products成功时，结果已是该目录的完整有界观察，
其中relative_path是source_id下的源文件路径，不是asset_id；不得把它传给inspect_file、
inspect_raster、preview_image或artifacts_inspect。用户只要求盘点或质量报告时应直接作答。
只有检查真实状态、文件或数值后才能报告完成。大文件只使用资产引用和必要元数据。
无法继续时停止，并准确说明已完成部分、失败事实和缺少的条件。
"""


@dataclass
class AgentDependencies:
    toolbox: RuntimeTools
    cancelled: Callable[[], bool]
    pause_requested: Callable[[], bool]
    begin_action: Callable[[str, str, dict], dict] | None = None
    mark_attempt_started: Callable[[str], None] | None = None
    finish_action: Callable[[str, str, dict, float, str | None], None] | None = None
    publish: Callable[[dict], None] | None = None
    begin_step: Callable[[int, dict, int | None], str] | None = None
    finish_step: Callable[[str, dict], None] | None = None
    failed_calls: dict[str, dict] = field(default_factory=dict)
    invalid_source_paths: dict[str, dict] = field(default_factory=dict)
    blocked_failures: int = 0
    pause_reason: str | None = None
    model_request_number: int = 0
    step_ids: dict[int, str] = field(default_factory=dict)
    evidence_generation: int = 0
    lock_tools_after_complete_report: bool = False
    tools_locked: bool = False


class AgentPaused(Exception):
    pass


def _source_path_key(value: str) -> str:
    return str(value).replace("/", "\\").rstrip("\\").casefold()


def _latest_user(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _chat_messages(messages: list[dict]) -> list[ModelRequest | ModelResponse]:
    """Convert application roles without flattening them into one prompt."""
    converted: list[ModelRequest | ModelResponse] = []
    request_parts = []

    def flush_request() -> None:
        if request_parts:
            converted.append(ModelRequest(parts=list(request_parts)))
            request_parts.clear()

    for message in messages:
        content = str(message.get("content") or "")
        role = message.get("role")
        if role == "assistant":
            flush_request()
            if content:
                converted.append(ModelResponse(parts=[TextPart(content)]))
        elif role == "system":
            request_parts.append(SystemPromptPart(content))
        elif role == "user":
            request_parts.append(UserPromptPart(content))
    flush_request()
    return converted


def _model() -> OpenAIChatModel:
    base_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/") + "/v1"
    profile = OpenAIModelProfile(
        supports_tools=True,
        supports_thinking=True,
        thinking_always_enabled=os.getenv("OLLAMA_THINK", "true").lower() == "true",
        openai_chat_thinking_field="reasoning",
        openai_chat_send_back_thinking_parts="field",
    )
    return OpenAIChatModel(
        os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
        provider=OpenAIProvider(base_url=base_url, api_key="ollama"),
        profile=profile,
    )


def _settings() -> dict:
    return {
        "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0.2")),
        "max_tokens": int(os.getenv("OLLAMA_OUTPUT_TOKENS", "8192")),
        "parallel_tool_calls": False,
        "timeout": 180,
        "extra_body": {
            "think": os.getenv("OLLAMA_THINK", "true").lower() == "true",
            "options": {"num_ctx": int(os.getenv("OLLAMA_CONTEXT", "32768"))},
        },
    }


def _bounded_tool_result(output: dict, persist=None, limit: int = 14000) -> dict:
    encoded = json.dumps(output, ensure_ascii=False, allow_nan=False)
    if len(encoded) <= limit:
        return output
    result_id = persist(output) if persist is not None else None
    return {
        "ok": output.get("ok", False),
        "outcome_ok": output.get("outcome_ok", output.get("ok", False)),
        "truncated": True,
        "excerpt": encoded[:12000],
        "result_id": result_id,
        "total_chars": len(encoded),
        "note": "Use tool_result_read with result_id and an offset to read the complete result.",
    }


async def _execute_tool(ctx: RunContext[AgentDependencies], name: str, arguments: dict, domain: bool) -> dict:
    deps = ctx.deps
    if deps.cancelled():
        return {"ok": False, "error": "Run canceled before tool execution"}

    for field_name in ("folder_path", "path"):
        value = arguments.get(field_name)
        if not isinstance(value, str):
            continue
        known = deps.invalid_source_paths.get(_source_path_key(value))
        if known is not None and known.get("evidence_generation") == deps.evidence_generation:
            deps.blocked_failures += 1
            if deps.blocked_failures >= 3:
                deps.pause_reason = (
                    "模型连续三次使用已知无效路径，Runtime 已暂停本轮；"
                    "请采用工具给出的 suggested_arguments，或补充新的路径证据。"
                )
            return {
                "ok": False,
                "outcome_ok": False,
                "error": "This source path is already known to be invalid; the filesystem was not queried again.",
                "failure": {
                    "stage": "agent_control",
                    "code": "known_invalid_source_path",
                    "operation_started": False,
                    "side_effects": "none",
                    "requested_path": value,
                    "source_id": known.get("source_id"),
                    "suggested_path": known.get("suggested_path"),
                    "suggested_arguments": known.get("suggested_arguments"),
                },
            }

    fingerprint = (
        str(deps.evidence_generation) + ":" + name + ":"
        + json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    )
    source_scoped = bool(arguments.get("source_id")) or any(
        isinstance(arguments.get(field), str)
        and is_host_path(str(arguments.get(field)))
        for field in ("path", "folder_path")
    )
    previous = deps.failed_calls.get(fingerprint)
    if previous is not None:
        deps.blocked_failures += 1
        if deps.blocked_failures >= 3:
            deps.pause_reason = (
                "模型连续三次重复同一个失败调用，Runtime 已暂停本轮；"
                "请修改参数、换方法或补充新的证据后继续。"
            )
        return {
            "ok": False,
            "outcome_ok": False,
            "error": "The identical failed call was not executed again.",
            "failure": {
                "stage": "agent_control",
                "code": "duplicate_failed_call",
                "previous_error": previous.get("error"),
                "previous_failure": previous.get("failure"),
            },
        }

    deps.blocked_failures = 0

    loop = asyncio.get_running_loop()

    def progress(value: dict) -> None:
        if deps.publish:
            try:
                loop.call_soon_threadsafe(
                    deps.publish,
                    {"type": "job_status", **{
                    key: value.get(key) for key in (
                        "job_id", "job_type", "state", "progress_percent", "terminal"
                    )
                    }},
                )
            except RuntimeError:
                pass

    if domain:
        try:
            output = await asyncio.to_thread(
                deps.toolbox._execute_domain, name, arguments, progress
            )
        except Exception as exc:
            output = execution_failure(exc)
    elif name == "job_status":
        output = await asyncio.to_thread(deps.toolbox.execute, name, arguments)
        data = output.get("data") or {}
        if output.get("ok") and deps.publish:
            deps.publish({"type": "job_status", **{
                key: data.get(key) for key in (
                    "job_id", "job_type", "state", "terminal", "exit_code",
                    "offset", "progress_percent", "needs_finalization",
                )
            }})
    else:
        output = await asyncio.to_thread(deps.toolbox.execute, name, arguments, progress)

    if not output.get("outcome_ok", output.get("ok", False)):
        failure = output.get("failure") or {}
        if not failure.get("operation_started", False):
            deps.failed_calls[fingerprint] = output
        if (
            failure.get("code") == "source_path_not_found"
            and failure.get("requested_path")
        ):
            deps.invalid_source_paths[_source_path_key(failure["requested_path"])] = {
                **failure, "evidence_generation": deps.evidence_generation,
            }
    elif source_scoped:
        # A successful source observation is new evidence. Deterministic failure
        # suppression from the preceding evidence scope must no longer apply.
        deps.evidence_generation += 1
        deps.blocked_failures = 0
    data = output.get("data") or {}
    if (
        output.get("ok")
        and name in {"inspect_uav_dataset", "inspect_uav_products"}
        and data.get("observation_complete") is True
        and deps.lock_tools_after_complete_report
    ):
        deps.tools_locked = True
    return output


def _tool(name: str, model, description: str, *, domain: bool = False, deferred: bool = False) -> Tool:
    async def invoke(ctx: RunContext[AgentDependencies], **arguments):
        output = await _execute_tool(ctx, name, arguments, domain)
        return ToolReturn(return_value=_bounded_tool_result(
            output, ctx.deps.toolbox.store_tool_result
        ))

    tool = Tool.from_schema(
        invoke,
        name=name,
        description=description,
        json_schema=inline_schema(model.model_json_schema()),
        takes_ctx=True,
        sequential=True,
    )
    async def prepare(ctx: RunContext[AgentDependencies], tool_def):
        return None if ctx.deps.tools_locked else tool_def

    tool.prepare = prepare
    tool.defer_loading = deferred
    return tool


def _report_only_requested(user_text: str) -> bool:
    """Keep follow-up actions available only when the user actually requested one."""
    normalized = str(user_text or "").casefold()
    action_phrases = (
        "请生成", "并生成", "然后生成", "直接生成", "开始生成",
        "构建chm", "计算ndvi", "执行反演", "写入文件", "修改文件",
        "start processing", "generate chm", "calculate ndvi",
        "write file", "modify file",
    )
    return not any(phrase in normalized for phrase in action_phrases)


def _tools(use_tools: bool, user_text: str = "") -> tuple[list[Tool], int, int]:
    if not use_tools:
        return [], 0, 0
    tools = [
        _tool(name, model, description)
        for name, (model, description) in GENERIC_DEFINITIONS.items()
    ]
    visible_schema_chars = sum(
        len(json.dumps(inline_schema(model.model_json_schema()), ensure_ascii=False))
        for name, (model, _) in GENERIC_DEFINITIONS.items()
    )
    visible_count = len(tools)

    if plugin_enabled("remote-sensing"):
        from .capabilities.domain_runtime import DEFINITIONS
        initially_selected = matching_domain_tools(user_text)

        group_by_tool = {
            tool_name: group
            for group in DOMAIN_TOOL_GROUPS
            for tool_name in group.tools
        }
        for name, (model, description) in DEFINITIONS.items():
            group = group_by_tool.get(name)
            if group is None:
                continue
            enriched = f"{description} Domain group: {group.name}. {group.summary}"
            tools.append(_tool(
                name, model, enriched, domain=True,
                deferred=name not in initially_selected,
            ))
    return tools, visible_count, visible_schema_chars


async def stream_agent(
    store,
    owner: str,
    asset_ids: list[str],
    messages: list[dict],
    *,
    model=None,
    max_requests: int | None = None,
    use_tools: bool = True,
    workspace_registry: WorkspaceRegistry | None = None,
    cancelled: Callable[[], bool] | None = None,
    agent_run_id: str | None = None,
    chat_id: str | None = None,
    persistence_database: str | os.PathLike | None = None,
    runtime_run_id: str | None = None,
    turn_id: str | None = None,
    resume_agent_run_id: str | None = None,
    pause_requested: Callable[[], bool] | None = None,
    begin_action: Callable[[str, str, dict], dict] | None = None,
    mark_attempt_started: Callable[[str], None] | None = None,
    finish_action: Callable[[str, str, dict, float, str | None], None] | None = None,
    publish: Callable[[dict], None] | None = None,
    begin_step: Callable[[int, dict, int | None], str] | None = None,
    finish_step: Callable[[str, dict], None] | None = None,
    run_facts: Callable[[], dict] | None = None,
    project_context: Callable[[], dict] | None = None,
    memory_manager=None,
):
    """Stream one PydanticAI run using the existing project services."""
    registry = workspace_registry or WorkspaceRegistry(os.getenv("DATA_ROOT", "/data"))
    box = RuntimeTools(
        store, owner, asset_ids, registry, _latest_user(messages), memory_manager
    )
    cancelled = cancelled or (lambda: False)
    pause_requested = pause_requested or (lambda: False)
    latest_user = _latest_user(messages)
    deps = AgentDependencies(
        toolbox=box,
        cancelled=cancelled,
        pause_requested=pause_requested,
        begin_action=begin_action,
        mark_attempt_started=mark_attempt_started,
        finish_action=finish_action,
        publish=publish,
        begin_step=begin_step,
        finish_step=finish_step,
        lock_tools_after_complete_report=_report_only_requested(latest_user),
    )
    tools, _, _ = _tools(use_tools, latest_user)
    compiler = ContextCompiler(
        box, run_facts=run_facts, project_context=project_context
    )
    standalone_lifecycle_events: list[dict] = []
    capabilities = [ToolSearch(strategy="keywords", max_results=6)] if any(t.defer_loading for t in tools) else []
    step_store = None
    if agent_run_id and persistence_database:
        step_store = SqliteStepStore(database=persistence_database)
        metadata = {
            key: value for key, value in {
                "runtime_run_id": runtime_run_id,
                "turn_id": turn_id,
            }.items() if value is not None
        }
        capabilities.append(StepPersistence(
            store=step_store, run_id=agent_run_id, metadata=metadata,
        ))

    async def before_model_request(ctx, request_context):
        if ctx.deps.pause_requested():
            raise AgentPaused("Run paused before the next model request.")
        ctx.deps.model_request_number += 1
        number = ctx.deps.model_request_number
        params = request_context.model_request_parameters
        tool_defs = list(getattr(params, "function_tools", ()) or ())
        runtime_facts, manifest, estimated = compiler.compile(
            _latest_user(messages), tool_defs, len(request_context.messages)
        )
        params.instruction_parts = [
            *params.instruction_parts, InstructionPart(runtime_facts)
        ]
        manifest.update({
            "kernel": "pydantic-ai",
            "agent_run_id": agent_run_id,
        })
        history_chars = sum(
            len(json.dumps(message, default=str, ensure_ascii=False))
            for message in request_context.messages
        )
        system_prompt_chars = len(SYSTEM)
        estimated = max(1, (
            len(runtime_facts) + history_chars + system_prompt_chars
            + int(manifest["tool_schema_chars"])
        ) // 4)
        context_limit = int(os.getenv("OLLAMA_CONTEXT", "32768"))
        output_reserve = int(manifest["output_reserve_tokens"])
        manifest.update({
            "system_prompt_chars": system_prompt_chars,
            "history_chars": history_chars,
            "estimated_input_tokens": estimated,
            "estimated_total_tokens": estimated + output_reserve,
            "context_limit_tokens": context_limit,
            "budget_ok": estimated + output_reserve <= context_limit,
        })
        if ctx.deps.begin_step:
            ctx.deps.step_ids[number] = ctx.deps.begin_step(
                number, manifest, estimated
            )
        else:
            standalone_lifecycle_events.append({
                "type": "model_call", "number": number, **manifest,
                "estimated_tokens": estimated,
            })
        if not manifest["budget_ok"]:
            raise RuntimeError(
                "Compiled model context exceeds OLLAMA_CONTEXT after history processing"
            )
        return request_context

    async def after_model_request(ctx, *, request_context, response):
        step_id = ctx.deps.step_ids.get(ctx.deps.model_request_number)
        if step_id and ctx.deps.finish_step:
            usage = response.usage
            usage_data = asdict(usage) if is_dataclass(usage) else {}
            ctx.deps.finish_step(step_id, usage_data)
        return response

    async def wrap_tool_execute(ctx, *, call, tool_def, args, handler):
        if ctx.deps.pause_requested():
            raise AgentPaused("Run paused before the next tool dispatch.")
        action_id = call.tool_call_id or f"pydantic_{call.tool_name}_{time.time_ns()}"
        started = time.monotonic()
        decision = ctx.deps.begin_action(action_id, call.tool_name, args) if ctx.deps.begin_action else {"execute": True}
        if not decision.get("execute", True):
            prior = decision.get("result")
            if prior is None:
                prior = {
                    "ok": False, "outcome_ok": False,
                    "error": "The action was already registered but its outcome is not settled; it was not replayed.",
                    "failure": {"stage": "recovery", "code": "action_outcome_unsettled", "operation_started": True},
                }
            return ToolReturn(return_value=prior)
        attempt_id = decision.get("attempt_id")
        if attempt_id and ctx.deps.mark_attempt_started:
            ctx.deps.mark_attempt_started(attempt_id)
        try:
            result = await handler(args)
        except Exception as exc:
            failure = execution_failure(exc)
            if ctx.deps.finish_action:
                ctx.deps.finish_action(
                    action_id, call.tool_name, failure,
                    round(time.monotonic() - started, 3), attempt_id,
                )
            raise
        payload: Any = result.return_value if isinstance(result, ToolReturn) else result
        normalized = payload if isinstance(payload, dict) else {"ok": True, "data": payload}
        if ctx.deps.finish_action:
            ctx.deps.finish_action(
                action_id, call.tool_name, normalized,
                round(time.monotonic() - started, 3), attempt_id,
            )
        if ctx.deps.pause_reason:
            raise AgentPaused(ctx.deps.pause_reason)
        return result

    capabilities.append(Hooks(
        before_model_request=before_model_request,
        after_model_request=after_model_request,
        tool_execute=wrap_tool_execute,
        id="runtime-lifecycle",
    ))

    def file_key(call: ToolCallPart) -> str | None:
        return box.observation_key(call.tool_name, call.args_as_dict())

    context_limit = int(os.getenv("OLLAMA_CONTEXT", "32768"))
    output_reserve = int(os.getenv("OLLAMA_OUTPUT_TOKENS", "8192"))
    compaction_target = max(
        8000, min(16000, context_limit - output_reserve - 8192)
    )
    capabilities.append(TieredCompaction(
        tiers=[
            DeduplicateFileReads(file_key=file_key),
            ClearToolResults(max_tokens=compaction_target, keep_pairs=3),
            SummarizingCompaction(
                max_tokens=compaction_target + 2000,
                keep_tokens=min(6000, compaction_target // 2),
                keep_user_messages=True,
                model_settings={"max_tokens": 2048}, receipts=True,
            ),
        ],
        target_tokens=compaction_target,
    ))
    agent = Agent(
        model=model or _model(),
        instructions=SYSTEM,
        deps_type=AgentDependencies,
        tools=tools,
        capabilities=capabilities,
        retries=1,
        max_concurrency=1,
    )

    history = []
    if step_store and resume_agent_run_id:
        history = await continue_run(step_store, run_id=resume_agent_run_id)
    history.extend(_chat_messages(messages))
    if not history:
        yield {"type": "error", "content": "Run has no model input.", "state": "failed"}
        yield {"type": "done", "artifacts": box.created, "state": "failed"}
        return

    token = CancellationToken()

    async def watch_cancel() -> None:
        while not token.cancelled:
            if cancelled():
                token.cancel()
                return
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch_cancel())
    final_text = False
    buffered_text = ""
    emitted_text = ""
    standalone_started_calls: dict[str, float] = {}
    result_event = None
    max_requests = max_requests or int(os.getenv("AGENT_MAX_ROUNDS", "32"))

    try:
        async with agent.run_stream_events(
            user_prompt=None,
            message_history=history,
            conversation_id=chat_id,
            run_id=agent_run_id,
            deps=deps,
            model_settings=_settings() if model is None else None,
            usage_limits=UsageLimits(request_limit=max_requests),
            cancellation_token=token,
        ) as framework_events:
            async for event in framework_events:
                while standalone_lifecycle_events:
                    yield standalone_lifecycle_events.pop(0)
                if isinstance(event, PartStartEvent):
                    if isinstance(event.part, ThinkingPart) and event.part.content:
                        yield {"type": "thinking", "content": event.part.content}
                    elif isinstance(event.part, TextPart) and event.part.content:
                        buffered_text += event.part.content
                elif isinstance(event, PartDeltaEvent):
                    if isinstance(event.delta, ThinkingPartDelta) and event.delta.content_delta:
                        yield {"type": "thinking", "content": event.delta.content_delta}
                    elif isinstance(event.delta, TextPartDelta) and event.delta.content_delta:
                        if final_text:
                            emitted_text += event.delta.content_delta
                            yield {"type": "message", "content": event.delta.content_delta}
                        else:
                            buffered_text += event.delta.content_delta
                elif isinstance(event, FinalResultEvent):
                    final_text = True
                    if buffered_text:
                        emitted_text += buffered_text
                        yield {"type": "message", "content": buffered_text}
                        buffered_text = ""
                elif isinstance(event, FunctionToolCallEvent) and begin_action is None:
                    part = event.part
                    standalone_started_calls[part.tool_call_id] = time.monotonic()
                    yield {
                        "type": "tool_start", "action_id": part.tool_call_id,
                        "name": part.tool_name, "arguments": part.args_as_dict(),
                    }
                elif isinstance(event, FunctionToolResultEvent) and begin_action is None:
                    part = event.part
                    content = getattr(part, "content", None)
                    if isinstance(content, ToolReturn):
                        content = content.return_value
                    output = content if isinstance(content, dict) else {
                        "ok": not hasattr(part, "error_message"), "data": content
                    }
                    yield {
                        "type": "tool_end", "action_id": part.tool_call_id,
                        "name": part.tool_name or "unknown",
                        "ok": output.get("ok", True),
                        "outcome_ok": output.get("outcome_ok", output.get("ok", True)),
                        "duration_seconds": round(
                            time.monotonic() - standalone_started_calls.get(
                                part.tool_call_id, time.monotonic()
                            ), 3
                        ),
                        "result": output,
                    }
                elif isinstance(event, AgentRunResultEvent):
                    result_event = event

        if result_event is None:
            raise UnexpectedModelBehavior("PydanticAI ended without an AgentRunResult")
        result = result_event.result
        if not emitted_text and isinstance(result.output, str) and result.output:
            yield {"type": "message", "content": result.output}
        usage = result.usage
        usage_data = asdict(usage) if is_dataclass(usage) else {}
        yield {"type": "done", "artifacts": box.created, "usage": usage_data}
    except UsageLimitExceeded:
        yield {
            "type": "error",
            "content": f"已用完本 Run 的 {max_requests} 次模型调用预算；现场已保存，可补充要求后继续。",
            "state": "paused",
        }
        yield {"type": "done", "artifacts": box.created, "state": "paused"}
    except AgentPaused as exc:
        yield {"type": "error", "content": str(exc), "state": "paused"}
        yield {"type": "done", "artifacts": box.created, "state": "paused"}
    except RunCancelled:
        yield {"type": "error", "content": "任务已取消；不会启动后续行动。", "state": "canceled"}
        yield {"type": "done", "artifacts": box.created, "state": "canceled"}
    except ModelAPIError as exc:
        yield {"type": "error", "content": f"模型服务失败：{exc}", "state": "failed"}
        yield {"type": "done", "artifacts": box.created, "state": "failed"}
    except UnexpectedModelBehavior as exc:
        yield {"type": "error", "content": f"模型响应无法执行：{exc}", "state": "failed"}
        yield {"type": "done", "artifacts": box.created, "state": "failed"}
    finally:
        watcher.cancel()
