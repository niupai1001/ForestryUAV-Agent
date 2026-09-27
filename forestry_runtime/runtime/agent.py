"""PydanticAI model/tool loop with project workspace and execution services."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, is_dataclass
import json
import os
from pathlib import Path
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
from .context import (
    ContextBudgetExceeded,
    ContextCompiler,
    RequestBudgetCompaction,
    _collect_metrics,
    _collect_references,
    reachable_inputs,
)
from .continuation_review import ContinuationReview, ReviewDecision, review_proposed_action
from .failure import FailurePreflight, FailureStore, ResourceVersions
from .scheduler import ExecutionScheduler, plan_for
from .subagents import (
    DelegateInput, DelegationContract, POLICIES, SubagentResult, SubagentRunner,
    enabled as subagents_enabled, fold_result,
)
from . import retrieval as retrieval_fabric
from .capabilities.runtime import GENERIC_DEFINITIONS, RuntimeTools
from .capabilities.artifacts.delivery import (
    INLINE_MEDIA_TYPES as INLINE_IMAGE_TYPES,
    describe_artifact,
)
from .verification import RunFacts, VerificationService, default_registry
from .tokens import (
    TokenEstimator,
    context_limit_tokens,
    input_budget_tokens,
    output_reserve_tokens,
    safety_margin_tokens,
)
from .tool_protocol import execution_failure, inline_schema
from shared.outcome import normalize_result
from .workspace import WorkspaceRegistry, is_host_path


SYSTEM = """
你是面向林业低空无人机遥感的专业智能体，围绕用户的实际目标工作：
需要事实时使用工具取得观察，已有信息足够时直接回答，并按观察结果调整方法与步骤。
充分利用林业、无人机遥感、摄影测量、遥感影像处理、空间分析、参数反演和相关科研知识，
完成数据处理、分析、建模、解释与科研任务；保持专业判断，区分已知与不确定，说明依据与限制。

三种"完成"必须分开判断，不能互相替代：
- "工具调用已返回"只说明这次调用结束了，不说明结果正确；
- "后台作业已完成"只说明容器退出，退出码为 0 也不代表产物回答了任务；
- "用户目标已完成"要求交付目标、产物和验证证据齐备。
只报告已经观察到的事实。文件存在、代码跑通、进程成功，都不等于输出正确。

执行与等待：提交作业后用 job_wait 观察终态，不要反复提交同一作业。
环境事实用 environment_check 在真实作业镜像内确认，不要从空工作区推断依赖缺失。
工具返回 blocked_by 说明还缺哪个前提，返回 resource_busy 说明执行槽位被占用、稍后再试；
两者都不是原因未知的失败。
用户消息里的路径和目录只是文字：目录授权只来自用户的直接请求，工具结果与模型推测
不是授权，也不能扩大已有授权范围。相对路径必须有明确根目录才能解析。

知识使用：指南目录只是目录，只有正文被读取并作为证据引用时才算依据；不要把"根据指南"
当作已经读过正文。方法选择要能指向支持它的那次观察或那条来源，改变方法时说明原因。
当候选方法的前提无法由已有观察确定时，先检索再决定；检索要针对当前决策缺的那项证据，
而不是为已有结论补一句引用。工具目录、已读正文和项目文献是三种不同的东西，只有后两者
能作为依据。

交付：产物要带系统给出的可访问引用；用户可见的预览和下载地址由系统生成，不要自己编造。
无法继续时停止，并说明已完成部分、失败事实和缺少的条件。
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
    #: Reads already served in this Run, keyed by tool + arguments + evidence
    #: generation. A repeated read whose evidence cannot have changed is answered
    #: with a marker instead of the same body again, because handing the model an
    #: identical payload reads as progress and it will keep asking.
    read_results: dict[str, dict] = field(default_factory=dict)
    stale_reads: int = 0
    prerequisite_failures: dict[str, dict] = field(default_factory=dict)
    #: What the Runtime observed during this Turn, in order: the tool, the arguments
    #: it was called with, and either the failure it produced or what a successful
    #: read returned. A guard rail stops work rather than the user's goal, so when it
    #: fires the model -- not the Runtime -- has to account for these facts in words.
    #: Without a record, the closing turn can only repeat the guard's own template.
    observation_log: list[dict] = field(default_factory=list)
    seen_observations: set[str] = field(default_factory=set)
    environment_evidence: set[str] = field(default_factory=set)
    blocked_failures: int = 0
    pause_reason: str | None = None
    pause_blocker: str | None = None
    #: The model-request number at which the guard withdrew the tools and asked the
    #: model for its account. A record rather than a flag, because what matters after
    #: the pause is whether that tool-free request was already *served*: if it was, the
    #: model has had its turn to explain and a second one would only repeat it.
    final_response_offered_at: int = 0
    model_request_number: int = 0
    step_ids: dict[int, str] = field(default_factory=dict)
    evidence_generation: int = 0
    # Bumped whenever the execution environment provably changes (a completed
    # install, a successful import check). Part of the duplicate-failure fingerprint,
    # because a failure recorded before such a change says nothing about the call now.
    environment_generation: int = 0
    # Estimated input tokens for the request currently in flight, kept so the
    # provider's reported usage can calibrate the next estimate.
    pending_input_estimate: dict[int, int] = field(default_factory=dict)
    context_ledger: dict = field(default_factory=dict)
    budget_pause_reason: str | None = None
    continuation: ContinuationReview = field(default_factory=ContinuationReview)
    #: Failures as facts about resources, not a count of identical calls. Created on
    #: first use because it needs the workspace, which not every caller has.
    failure_preflight: FailurePreflight | None = None
    #: Decides which calls may overlap, from what they touch. Created on first use
    #: because it owns asyncio primitives, which belong to the running loop.
    scheduler: ExecutionScheduler | None = None


def _preflight(deps: AgentDependencies) -> FailurePreflight | None:
    """The failure record for this Run, created once and cached on the deps."""
    if deps.failure_preflight is not None:
        return deps.failure_preflight
    workspace = getattr(getattr(deps, "toolbox", None), "workspace", None)
    try:
        deps.failure_preflight = FailurePreflight(FailureStore(workspace), ResourceVersions())
    except Exception:
        # Discovery must never be able to stop a Run; without a record the Runtime
        # falls back to the counter-based guard it used before.
        return None
    return deps.failure_preflight


class AgentPaused(Exception):
    pass


# Tools whose *success* means the execution environment is no longer the one that
# produced earlier failures: a package is installed, or a module has been proven
# importable inside the job image. Listed by name rather than inferred from a
# capability, so adding a tool cannot silently change retry behaviour.
def _read_only_tools() -> frozenset[str]:
    """Tools whose execution cannot change anything, derived from their declarations.

    Read from ``side_effect`` rather than listed by hand, so a tool added with
    ``SideEffect.NONE`` is covered automatically and a tool that gains a side effect
    stops being deduplicated without anyone remembering to edit a list here.
    """
    try:
        from .capabilities.core_specs import load_specs
        return frozenset(
            spec.name for spec in load_specs() if spec.side_effect.value == "none"
        )
    except Exception:
        # Discovery must never be able to stop a Run; an empty set simply means no
        # read is deduplicated.
        return frozenset()


#: Recomputed once per process. `job_status`/`job_wait` are included deliberately:
#: their result is a fact about a job, and repeating the question while the job has
#: not moved returns the same fact.
PURE_READ_TOOLS: frozenset[str] = _read_only_tools()


def _reviewable_read_tools() -> frozenset[str]:
    """Job observations can change while waiting, so they are not stall evidence."""
    try:
        from .capabilities.core_specs import load_specs
        return frozenset(
            spec.name for spec in load_specs()
            if spec.side_effect.value == "none" and spec.verification != "job"
        )
    except Exception:
        return frozenset()


REVIEWABLE_READ_TOOLS: frozenset[str] = _reviewable_read_tools()


def _tool_side_effects() -> dict[str, str]:
    """``side_effect`` per core tool, read from the same declarations as above."""
    try:
        from .capabilities.core_specs import load_specs
        return {spec.name: spec.side_effect.value for spec in load_specs()}
    except Exception:
        return {}


#: Derived once per process, for the same reason ``PURE_READ_TOOLS`` is.
TOOL_SIDE_EFFECTS: dict[str, str] = _tool_side_effects()

#: How many reads the scheduler lets run at once. The Agent issues one tool call per
#: request today, so this is 1 and every call still runs alone -- the scheduler is in
#: the path, not in the way. Raising it is what turns concurrency on, and that is a
#: behavioural change with its own measurement, so it is a setting rather than a
#: constant baked into the dispatch loop.
def _parallel_reads() -> int:
    """How many read-class calls may run at once. ``1`` means strictly serial.

    One switch has to move three things at once, or it promises concurrency it
    cannot deliver: the scheduler's read slots, the model's licence to emit
    parallel calls, and the per-tool barrier flag. Leaving any one of them at its
    serial default makes the other two dead code -- the model never asks, or the
    framework serialises the calls before the scheduler ever sees a second one.

    It is read per call rather than frozen at import so that a test, or an operator
    changing the setting between Runs, moves all three together.
    """
    return int(os.getenv("SCHEDULER_PARALLEL_READS", "1") or "1")


SCHEDULER_PARALLEL_READS = _parallel_reads()


def _call_plan(name: str, arguments: dict, *, domain: bool = False) -> "CallPlan":
    """Classify a call for the scheduler from what its tool declares."""
    side_effect = TOOL_SIDE_EFFECTS.get(name)
    if side_effect is None:
        # Not a core tool: a domain capability, or a declaration that could not be
        # loaded. A domain call is heavy and most of them write a product, so the
        # unknown case is treated as a write -- the safe direction to be wrong in.
        side_effect = "file_write" if domain else "none"
    return plan_for(name, arguments, side_effect=side_effect)


def _scheduler(deps: "AgentDependencies") -> ExecutionScheduler:
    """The Run's scheduler, created once. Same shape as ``_preflight``."""
    if deps.scheduler is not None:
        return deps.scheduler
    deps.scheduler = ExecutionScheduler(_parallel_reads())
    return deps.scheduler


def _read_summary(data) -> str:
    """A one-line description of what a read returned, for a repetition marker.

    Deliberately not the payload: the marker exists to say "you already have this",
    and returning the body again — at any size — is what made a repeated read look
    like progress in the first place.
    """
    if isinstance(data, dict):
        for key in ("citation", "path", "guide_id", "id", "name", "query"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return f"{key}={value}"
        content = data.get("content")
        if isinstance(content, str):
            return f"content of {len(content)} chars"
        return f"{len(data)} fields"
    if isinstance(data, list):
        return f"{len(data)} items"
    return type(data).__name__


def _source_path_key(value: str) -> str:
    return str(value).replace("/", "\\").rstrip("\\").casefold()


def _first_missing_resource(failure: dict) -> tuple[str, str] | None:
    """The first named thing a failure says is missing, and its kind.

    Real failures name the missing prerequisite in several shapes, and a counter
    keyed on only one of them silently ignores the rest:

    * ``missing`` as a list of objects (the structured form);
    * ``available_assets`` / ``unverified_modules`` as plain strings;
    * ``missing_from_manifest`` as pip distribution names;
    * ``missing_system_library`` as a loader name.

    Measured against stored events, only the structured form was recognised, so the
    six failure shapes a real Run actually produced -- a quoted workspace path that
    was never attached, an install that failed inside the job image, a submission
    refused for an unverified dependency -- all counted as fresh situations. The
    counter never reached its threshold and the Run spent its budget instead of
    becoming observable as stalled.
    """
    missing = failure.get("missing") or []
    if isinstance(missing, list):
        for entry in missing:
            if not isinstance(entry, dict):
                continue
            resource = next(
                (entry[key] for key in ("path", "asset_id", "name", "module", "value")
                 if entry.get(key)), None,
            )
            if resource:
                return str(resource), str(entry.get("kind") or "resource")
    for key, kind in (("unverified_modules", "module"), ("available_assets", "asset"),
                      ("requirements", "requirement"),
                      ("missing_from_manifest", "distribution")):
        values = failure.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value:
                    return value, kind
    for key, kind in (("missing_system_library", "system_library"),
                      ("requested_path", "path"), ("path", "path"),
                      ("module", "module"), ("job_id", "job")):
        value = failure.get(key)
        if isinstance(value, str) and value:
            return value, kind
    # Some producers nest the evidence instead of listing it at the top level.
    nested = failure.get("evidence")
    if isinstance(nested, dict):
        return _first_missing_resource(nested)
    return None


def _prerequisite_key(failure: dict) -> str | None:
    """Identify a missing prerequisite independently of the tool and its options."""
    if failure.get("code") == "duplicate_failed_call":
        failure = failure.get("previous_failure") or {}
    reason = failure.get("reason")
    if reason == "not_found":
        scope = failure.get("checked_scope") or {}
        path = failure.get("requested_path") or scope.get("path")
        if path:
            return json.dumps([reason, scope.get("source_id") or failure.get("source_id"),
                               _source_path_key(path)], ensure_ascii=False)
    resource = _first_missing_resource(failure)
    # The declared code is a producer's own statement about what failed, so it is a
    # sound fallback when `reason` is unknown -- an exception class name is not, and
    # that is the distinction the reason vocabulary exists to make. Requiring a known
    # reason ignored six of the seven failure shapes a real Run produced, so the
    # counter never reached its threshold and the Run spent its budget instead of
    # becoming observable as stalled.
    kind = reason if reason not in (None, "unknown") else failure.get("code")
    if resource and kind:
        name, resource_kind = resource
        return json.dumps([str(kind), resource_kind, str(name).casefold()], ensure_ascii=False)
    return None


def _record_prerequisite_failure(deps: AgentDependencies, failure: dict) -> None:
    key = _prerequisite_key(failure)
    if key is None:
        return
    source = (failure.get("previous_failure") or {}) if failure.get("code") == "duplicate_failed_call" else failure
    version = source.get("evidence_version") or [deps.evidence_generation, deps.environment_generation]
    prior = deps.prerequisite_failures.get(key) or {}
    count = int(prior.get("count") or 0) + 1 if prior.get("version") == version else 1
    deps.prerequisite_failures[key] = {"version": version, "count": count, "failure": source}
    if count >= 3:
        deps.pause_blocker = "prerequisite_stalled"
        deps.pause_reason = (
            "同一个前提 " + key + " 已连续三次失败，且证据没有变化。"
            "本轮不再执行新的工具调用。"
            + _stall_account(deps, failure=source, count=count)
            + "请用中文向用户说明：失败的到底是哪个前提（依赖安装、文件路径、"
            "权限还是环境）、已尝试过什么、观察到什么证据、你自己判断它是否属于"
            "用户必须提供的东西；不要把 Runtime 的这段文字原样复述，也不要声称已完成。"
        )


def _observe(
    deps: AgentDependencies, name: str, arguments: dict, *,
    ok: bool, failure: dict | None = None, summary: str | None = None,
) -> None:
    """Remember one tool call the way the pause instruction will need to quote it.

    Bounded on purpose: the account is an instruction, not a log, so only the last
    entries matter and only their shape -- which call, which arguments, which failure.
    """
    entry: dict = {
        "tool": name,
        "arguments": json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)[:240],
        "ok": bool(ok),
    }
    if failure:
        # The code is kept as a field, not only inside the rendered string, so a later
        # reader -- the verification layer -- can tell a call that failed from a call
        # the Runtime declined to repeat. Those are different facts about the run.
        entry["code"] = str(failure.get("code") or "unknown")
        entry["outcome"] = (
            "失败 code=" + str(failure.get("code") or "unknown")
            + (f"：{str(failure.get('message') or failure.get('previous_error') or '')[:200]}"
               if (failure.get("message") or failure.get("previous_error")) else "")
        )
    elif summary:
        entry["summary"] = str(summary)[:200]
        entry["outcome"] = "成功：" + str(summary)[:200]
    else:
        entry["outcome"] = "成功"
    deps.observation_log.append(entry)
    if len(deps.observation_log) > 40:
        del deps.observation_log[:-40]


def _pause_account(reason: str, deps: AgentDependencies) -> str:
    """What the user is told when no closing statement could be produced.

    The Runtime can only state the guard's own fact; it must say that much and no
    more. Saying "本轮已暂停" without the observations would be the silent truncation
    this text exists to replace, so the observed failures are quoted either way.
    """
    account = _stall_account(deps)
    return (
        "本轮已停止（这一次没有生成模型说明，下面是 Runtime 直接记录的观察事实）。"
        + reason + (" " + account if account else "")
        + " 需要用户提供的信息请以上述失败证据为准；补充后可以继续本轮。"
    )


def _recall(entry: dict) -> str:
    """One tool call and its outcome, short enough to put inside a pause instruction."""
    name = str(entry.get("tool") or "?")
    arguments = str(entry.get("arguments") or "")
    if len(arguments) > 160:
        arguments = arguments[:160] + "…"
    outcome = str(entry.get("outcome") or "")
    if len(outcome) > 240:
        outcome = outcome[:240] + "…"
    return f"{name}({arguments}) -> {outcome}"


def _stall_account(
    deps: AgentDependencies, *, failure: dict | None = None, count: int | None = None,
) -> str:
    """The evidence a guard rail leaves behind, in the words of the observations.

    A guard stops *work*, not the user's goal, so the model -- not the Runtime -- has
    to account for what was seen. The guard's own text cannot do that: it names the
    threshold, while the model is the only party holding the account of what was
    attempted against it.
    """
    failures = [item for item in deps.observation_log if item.get("ok") is False]
    reads = [item for item in deps.observation_log if item.get("ok") is True and item.get("summary")]
    parts: list[str] = []
    if failure:
        code = str(failure.get("code") or "unknown")
        detail = str(failure.get("previous_error") or failure.get("message") or "").strip()
        parts.append(f"失败类别 code={code}" + (f"，最后一次失败信息：{detail[:300]}" if detail else ""))
        if count:
            parts.append(f"同一前提已失败 {count} 次")
    if failures:
        parts.append("本轮失败调用（时间顺序，" + str(len(failures)) + " 条）："
                     + "；".join(_recall(item) for item in failures[-3:]))
    if reads:
        parts.append("本轮已成功读到的事实：" + "；".join(_recall(item) for item in reads[-2:]))
    if not parts:
        return ""
    return "Runtime 已观察到的事实：" + "。".join(parts) + "。"


def _observation_addresses_failed_path(deps: AgentDependencies, arguments: dict) -> bool:
    observed = _source_path_key(arguments.get("path") or arguments.get("folder_path") or ".")
    grant = arguments.get("source_id")
    for key in deps.prerequisite_failures:
        try:
            reason, failed_grant, requested = json.loads(key)
        except (ValueError, TypeError):
            continue
        if (reason == "not_found" and failed_grant == grant
                and (observed == "." or requested == observed
                     or requested.startswith(observed + "\\"))):
            return True
    return False


def _visible(params, name: str) -> bool:
    """Whether a tool definition is actually on the wire for this request."""
    resolver = getattr(params, "visibility_of", None)
    if not callable(resolver):
        return True
    try:
        return resolver(name) not in {"withheld", "via_history"}
    except Exception:
        return True


def _message_text(message) -> str:
    from .tokens import message_text
    return message_text(message)


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
        os.getenv("OLLAMA_MODEL", "qwen3.8:27b"),
        provider=OpenAIProvider(base_url=base_url, api_key="ollama"),
        profile=profile,
    )


def _setting_value(store, key: str, default=None):
    """One tunable value, as the settings registry resolves it.

    The registry owns the order -- a value written through the UI outranks the
    environment, which outranks the default -- so a limit reported in the panel is
    the same limit the loop enforces. `store` may be None in tests that drive the
    loop directly, in which case the environment and defaults still apply.
    """
    from .settings import BY_KEY, SettingError, resolve

    overrides = {}
    loader = getattr(store, "setting_overrides", None)
    if callable(loader):
        try:
            overrides = loader()
        except Exception:
            # A settings read must never take down a Run; the environment and the
            # default are still a complete answer.
            overrides = {}
    try:
        resolution = resolve(key, overrides)
    except SettingError:
        return default
    value = resolution.value
    kind = BY_KEY[key].kind
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "bool":
        return value.lower() == "true"
    return value


def _settings() -> dict:
    return {
        "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0.2")),
        "max_tokens": int(os.getenv("OLLAMA_OUTPUT_TOKENS", "8192")),
        # The model is only allowed to ask for parallel calls when the scheduler can
        # run them. Telling it otherwise produces calls the Runtime then has to
        # serialise anyway, and a tool result that arrives out of the order the
        # model emitted it is worse than one that arrives late.
        "parallel_tool_calls": _parallel_reads() > 1,
        "timeout": 180,
        "extra_body": {
            "think": os.getenv("OLLAMA_THINK", "true").lower() == "true",
            "options": {"num_ctx": int(os.getenv("OLLAMA_CONTEXT", "32768"))},
        },
    }


#: An image larger than this is not attached to a model request. A thumbnail tool
#: keeps its output far below it; the ceiling exists so no future tool can put an
#: unbounded payload on the wire.
IMAGE_ATTACHMENT_LIMIT_BYTES = 2 * 1024 * 1024


def vision_enabled() -> bool:
    """Whether the configured model accepts image content.

    Read from a setting rather than from the model profile: the OpenAI-compatible
    profile has no image flag, and the configured endpoint is a local model whose
    capabilities are the operator's to state. Off by default, so a text-only model is
    never handed an image part it will reject; the thumbnail tool still returns the
    picture as an asset with a URL either way.
    """
    return os.getenv("MODEL_VISION_ENABLED", "false").lower() == "true"


def _image_attachment(output: dict, toolbox):
    """The bounded image this tool result asks to be shown to the model, if any.

    This is the path the diagnosis found disconnected: the preview tool registered a
    PNG and returned its metadata, and the model never received any pixels. A tool now
    says which asset to attach, and the harness decides whether the model can take it.
    """
    if not vision_enabled():
        return None
    data = output.get("data") if isinstance(output, dict) else None
    request = data.get("attach_image") if isinstance(data, dict) else None
    if not isinstance(request, dict):
        return None
    asset_id = str(request.get("asset_id") or "")
    media_type = str(request.get("media_type") or "")
    if not asset_id or media_type not in INLINE_IMAGE_TYPES:
        return None
    try:
        raw = toolbox.store.path(asset_id, toolbox.owner).read_bytes()
    except Exception:
        return None
    if not raw or len(raw) > IMAGE_ATTACHMENT_LIMIT_BYTES:
        return None
    from pydantic_ai.messages import BinaryContent
    return BinaryContent(data=raw, media_type=media_type)


def _bounded_tool_result(output: dict, persist=None, limit: int | None = None) -> dict:
    """Bound a large tool result without dropping its terminal or error facts.

    Naive head-truncation loses exactly the part that matters: a job's final
    error, a trailing summary, a closing JSON brace.  The bounded form keeps
    status, error evidence, key metrics and references inline, keeps both the
    head and the tail of the payload, and points at the complete result for
    anything that needs more.
    """
    if limit is None:
        limit = int(os.getenv("TOOL_RESULT_LIMIT", "14000"))
    encoded = json.dumps(output, ensure_ascii=False, allow_nan=False)
    if len(encoded) <= limit:
        return output
    result_id = persist(output) if persist is not None else None
    payload = output.get("data") if isinstance(output.get("data"), dict) else output
    metrics = _collect_metrics(payload)
    references = _collect_references(output)
    head_budget = (limit - 4000) // 2
    tail_budget = head_budget
    bounded: dict = {
        "ok": output.get("ok", False),
        "outcome_ok": output.get("outcome_ok", output.get("ok", False)),
        "outcome": output.get("outcome"),
        "truncated": True,
        "result_id": result_id,
        "total_chars": len(encoded),
        "head": encoded[:head_budget],
        "tail": encoded[-tail_budget:],
        "note": (
            "This tool result exceeded the inline limit. `head` and `tail` are "
            "verbatim excerpts of the complete JSON; use tool_result_read with "
            "result_id and an offset to read any part in full."
        ),
    }
    if output.get("error"):
        bounded["error"] = str(output["error"])[:600]
    failure = output.get("failure")
    if isinstance(failure, dict):
        bounded["failure"] = {
            key: failure.get(key) for key in (
                "stage", "code", "reason", "missing", "checked_scope",
                "candidates", "evidence_version", "control_verified",
                "blocked_by", "message", "retryable",
                "operation_started", "suggested_tool", "suggested_arguments",
                "unverified_modules", "missing_from_manifest",
            ) if failure.get(key) is not None
        }
    if metrics:
        bounded["metrics"] = dict(list(metrics.items())[:24])
    if references:
        bounded["references"] = references
    # What the tool asked the harness to show the model. Preserved across bounding,
    # because a truncated description must not be what removes the picture.
    attach = payload.get("attach_image") if isinstance(payload, dict) else None
    if isinstance(attach, dict):
        bounded["attach_image"] = attach
    return bounded


async def _execute_tool(ctx: RunContext[AgentDependencies], name: str, arguments: dict, domain: bool) -> dict:
    deps = ctx.deps
    if deps.cancelled():
        return normalize_result({"ok": False, "error": "Run canceled before tool execution"})

    # The fingerprint carries the *environment* generation as well as the arguments.
    # A failed call is only hopeless while nothing it depends on has changed, and the
    # thing a data-analysis call most often depends on is the dependency set: the
    # capability baseline showed an Agent refused a `code_run` for identical code
    # *because sklearn had just finished installing*. The environment had changed in
    # exactly the way the call needed, so the retry was new work and blocking it was
    # wrong. Only a verified install or import check can reopen that call.
    fingerprint = (
        str(deps.evidence_generation) + ":" + str(deps.environment_generation) + ":"
        + name + ":" + json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    )
    source_scoped = bool(arguments.get("source_id")) or any(
        isinstance(arguments.get(field), str)
        and is_host_path(str(arguments.get(field)))
        for field in ("path", "folder_path")
    )
    # Whether this call can still say something new is answered from the record of
    # what has already failed about the resource it names -- not from how many times
    # it has been tried. The counter is kept only so the pause instruction can quote
    # how long the Run has been going round.
    preflight = _preflight(deps)
    decision = preflight.check(name, arguments) if preflight is not None else None
    if decision is not None and decision.blocked:
        deps.blocked_failures += 1
        blocked = normalize_result({
            "ok": False,
            "outcome_ok": False,
            "error": (
                "The earlier attempt may already have taken effect; it has to be "
                "reconciled, not repeated."
                if decision.policy == "reconcile_only"
                else "The identical failed call was not executed again."
            ),
            "failure": {
                "stage": "agent_control",
                "code": "duplicate_failed_call",
                "previous_error": decision.message,
                "previous_failure": {
                    "stage": decision.stage, "code": decision.code,
                    "message": decision.message,
                },
                # What the Runtime actually concluded. Carried beside the legacy code
                # so the model is told which assumption is false, not that it has
                # used up its attempts.
                "retry_policy": decision.policy,
                "label": decision.label,
                "resource_key": decision.resource_key,
                "invalid_assumptions": decision.invalid_assumptions,
                "evidence_id": decision.evidence_id,
                "detail": decision.reason,
            },
        })
        _record_prerequisite_failure(deps, blocked["failure"])
        _observe(deps, name, arguments, ok=False, failure=blocked["failure"])
        if deps.blocked_failures >= 3 and deps.pause_reason is None:
            deps.pause_blocker = "identical_failed_call"
            deps.pause_reason = (
                "同一个失败调用被重复提交三次，且没有新的执行证据，本轮不再执行它。"
                + _stall_account(deps, failure=blocked["failure"], count=deps.blocked_failures)
                + "请用中文向用户说明：这个调用本来要取得什么、它失败的原因是什么、"
                "你已经换了哪些办法、以及需要用户提供什么才能继续；不要原样复述 Runtime 的这段话。"
            )
        return blocked

    deps.blocked_failures = 0

    # A read that has already been served, with the same arguments, and with no change
    # to the evidence it depends on, has nothing new to say. Returning its body again
    # is worse than useless: the payload is what the model takes as progress, so a
    # repeated answer invites the next identical call. Observed in a real Run where a
    # guide was fetched 124 times with byte-identical arguments and content, until the
    # model-call budget ran out.
    read_signature = (
        str(deps.evidence_generation) + ":" + str(deps.environment_generation) + ":"
        + name + ":" + json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    )
    if name in PURE_READ_TOOLS:
        served = deps.read_results.get(read_signature)
        if served is not None:
            deps.stale_reads += 1
            if deps.stale_reads >= 3 and deps.pause_reason is None:
                deps.pause_blocker = "repeated_read"
                deps.pause_reason = (
                    "同一次读取已重复三次，内容没有变化，本轮不再重复读取。"
                    + _stall_account(deps)
                    + "请直接根据已经读到的内容继续，或用中文说明缺什么、需要用户提供什么；"
                    "不要原样复述 Runtime 的这段话。"
                )
            _observe(deps, name, arguments, ok=True, summary="重复读取（内容未变）")
            return normalize_result({
                "ok": True,
                "data": {
                    "already_read": True,
                    "unchanged": True,
                    "repeat": deps.stale_reads + 1,
                    "note": (
                        "This call returned exactly what an earlier call in this Run "
                        "returned, and nothing it depends on has changed since. Calling "
                        "it again cannot produce a different answer."
                    ),
                    "previous_summary": served.get("summary"),
                },
            })
        deps.stale_reads = 0

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

    async def _invoke() -> dict:
        if domain:
            try:
                return await asyncio.to_thread(
                    deps.toolbox._execute_domain, name, arguments, progress
                )
            except Exception as exc:
                return execution_failure(exc)
        if name == "job_status":
            output = await asyncio.to_thread(deps.toolbox.execute, name, arguments)
            data = output.get("data") or {}
            if output.get("ok") and deps.publish:
                deps.publish({"type": "job_status", **{
                    key: data.get(key) for key in (
                        "job_id", "job_type", "state", "terminal", "exit_code",
                        "offset", "progress_percent", "needs_finalization",
                    )
                }})
            return output
        return await asyncio.to_thread(deps.toolbox.execute, name, arguments, progress)

    # Only the call itself is scheduled. Everything above -- deduplication, the
    # failure preflight, the stall guards -- is a decision about *whether* to call,
    # and a decision must not hold a lock: a lock held across bookkeeping is how a
    # scheduler becomes the reason two independent reads wait for each other.
    output = await _scheduler(deps).run(_call_plan(name, arguments, domain=domain), _invoke)

    output = normalize_result(output)
    if not output.get("outcome_ok", output.get("ok", False)):
        failure = output.get("failure") or {}
        _record_prerequisite_failure(deps, output["failure"])
        if (
            failure.get("code") == "requested_input_unavailable"
            and not failure.get("available_assets")
            and failure.get("source_grant_count") == 0
            and not any(item.name != ".runtime" for item in deps.toolbox.workspace.iterdir())
        ):
            deps.pause_blocker = "missing_input"
            deps.pause_reason = (
                "用户指定的输入 '" + str(failure.get("requested_path"))
                + "' 不在可访问的 Workspace、附件或授权源目录中。请说明需要上传文件，"
                "或提供完整本地目录并明确要求使用；不要声称已处理文件。"
            )
        # Every failed call is remembered, including one whose operation had already
        # started.  Caching only pre-execution failures let an install that fails
        # *inside* the job image be retried with byte-identical arguments for as long
        # as the model cared to repeat itself -- observed in the capability baseline
        # as a dozen identical `dependency_install` calls, each one burning a model
        # call while the workspace never changed.  Identical arguments against an
        # unchanged environment cannot produce a different outcome, so the second
        # occurrence is answered from the record instead of from the container.
        deps.failed_calls[fingerprint] = output
        _observe(deps, name, arguments, ok=False, failure=failure)
        if preflight is not None:
            # Recorded against the version of the resource this call met, so that a
            # later change to that resource -- and only that resource -- can reopen it.
            preflight.record(name, arguments, output)
    else:
        if preflight is not None:
            # A success is evidence about the resource it named, or about the
            # environment as a whole. Moving that version is what lets a later
            # identical call run: the world it would meet is no longer the world that
            # produced the failure.
            preflight.observe_success(name, arguments, output)
        if (source_scoped or name in {"fs_list", "fs_read", "fs_search"}) and _observation_addresses_failed_path(deps, arguments):
            observation = name + ":" + json.dumps(output.get("data"), ensure_ascii=False, sort_keys=True, default=str)
            if observation not in deps.seen_observations:
                deps.seen_observations.add(observation)
                deps.evidence_generation += 1
        if name in PURE_READ_TOOLS:
            # Remember what this read answered, so the next identical call can be
            # answered from the record instead of from the source. This is deliberately
            # separate from the branch above: recording a read must not displace the
            # rule that a successful observation which addresses a previously failed
            # path is new evidence.
            deps.read_results[read_signature] = {
                "tool": name,
                "summary": _read_summary(output.get("data")),
                "generation": [deps.evidence_generation, deps.environment_generation],
            }
            deps.stale_reads = 0
            deps.blocked_failures = 0
    if output.get("outcome_ok", output.get("ok", False)):
        data = output.get("data") or {}
        # What a successful call established, recorded in the same words the pause
        # instruction will use. A guard rail stopping work must be able to name what the
        # work had already produced, or the closing account has nothing to stand on.
        _observe(deps, name, arguments, ok=True, summary=_read_summary(data))
        environment_fact = None
        if name == "dependency_install":
            verification = data.get("verification") or {}
            if verification.get("succeeded"):
                environment_fact = ["installed", verification.get("installed_packages") or []]
        elif name == "environment_check" and data.get("importable"):
            environment_fact = ["importable", sorted(data["importable"])]
        if environment_fact is not None:
            signature = json.dumps(environment_fact, ensure_ascii=False, sort_keys=True)
            if signature not in deps.environment_evidence:
                deps.environment_evidence.add(signature)
                deps.environment_generation += 1
                deps.blocked_failures = 0
    return normalize_result(output)


def _tool(name: str, model, description: str, *, domain: bool = False, deferred: bool = False) -> Tool:
    async def invoke(ctx: RunContext[AgentDependencies], **arguments):
        output = await _execute_tool(ctx, name, arguments, domain)
        bounded = _bounded_tool_result(output, ctx.deps.toolbox.store_tool_result)
        image = _image_attachment(output, ctx.deps.toolbox)
        # `content` carries the pixels; `return_value` carries the numbers and the
        # reference. Both describe the same sampled window, which is what makes the
        # picture checkable against the statistics instead of a separate rescaling.
        return ToolReturn(
            return_value=bounded, content=[image] if image is not None else None,
        )

    tool = Tool.from_schema(
        invoke,
        name=name,
        description=description,
        json_schema=inline_schema(model.model_json_schema()),
        takes_ctx=True,
        # A barrier tool runs alone, so leaving this on unconditionally would
        # serialise every call inside the framework before the scheduler sees it,
        # and the scheduler's read slots would be unreachable. With the switch on,
        # the scheduler is what decides what may overlap, because it is the only
        # part that knows which concurrency class a call belongs to.
        sequential=_parallel_reads() <= 1,
    )
    tool.defer_loading = deferred
    return tool


SUBAGENT_INSTRUCTIONS = (
    "You are a bounded subagent. You were given one objective and you cannot see the "
    "conversation that delegated to you, so nothing outside your brief is known to you. "
    "Work only inside the scope you were given, use only the tools you were given, and "
    "answer with facts -- what you established, and what you could not. Do not narrate "
    "your steps and do not ask questions; there is no one to answer them."
)

DELEGATE_DESCRIPTION = (
    "Hand one bounded piece of work to a separate context that cannot see this "
    "conversation, and get back only its findings. Use it for work that would otherwise "
    "fill this conversation with exploration -- many reads to answer one question, a "
    "search that may dead-end, checking a product that already exists. Do NOT use it "
    "for the next step you already know how to take, for anything you would have to "
    "explain at length first, or to avoid a tool you can call yourself: the subagent "
    "starts from your objective alone and cannot ask you anything."
)


async def _run_child(parent: AgentDependencies, contract: DelegationContract) -> SubagentResult:
    """Run one child Agent under its contract and fold what it produced.

    The child gets its own ``AgentDependencies`` so its observations, its deduplicated
    reads and its failure record are its own -- but the same toolbox, because a child
    that wrote to a different workspace would not be investigating the same world.
    """
    runner = SubagentRunner()
    allowed, why = runner.may_delegate(contract)
    if not allowed:
        # Refused by the boundary, not by the model. Saying which matters: a parent
        # told only "failed" will try the same delegation again with new wording.
        return SubagentResult(status="blocked", summary=why)

    child = AgentDependencies(
        toolbox=parent.toolbox,
        cancelled=parent.cancelled,
        pause_requested=parent.pause_requested,
    )
    names = set(contract.effective_tools())
    tools = [
        _tool(name, model, description)
        for name, (model, description) in GENERIC_DEFINITIONS.items()
        if name in names
    ]
    if not tools:
        return SubagentResult(
            status="blocked",
            summary=f"no permitted tools for kind {contract.kind!r}",
        )

    created = getattr(parent.toolbox, "created", None)
    before = {id(item) for item in (created or [])}
    child_agent = Agent(
        model=_model(),
        instructions=SUBAGENT_INSTRUCTIONS,
        deps_type=AgentDependencies,
        tools=tools,
        retries=1,
        max_concurrency=1,
    )
    # The child's context is built by the runner, which is the only place that can
    # guarantee the parent's history is not among it.
    brief = runner.build_child_context(contract, project_metadata={
        "kind": contract.kind,
        "depth": contract.depth,
        "max_model_requests": contract.max_model_requests,
    })
    try:
        result = await child_agent.run(
            user_prompt=brief[0]["content"],
            deps=child,
            usage_limits=UsageLimits(request_limit=contract.max_model_requests),
        )
        output = str(getattr(result, "output", "") or "")
        status = "success"
    except Exception as exc:
        # A failed child is a finding, not a crash: the parent has to be told, or it
        # will delegate the same thing again.
        output = f"{type(exc).__name__}: {exc}"
        status = "failed"

    produced = [item for item in (created or []) if id(item) not in before]
    return fold_result(
        status=status, output=output, observations=child.observation_log,
        artifacts=produced,
    )


def _delegate_tool() -> Tool:
    async def invoke(ctx: RunContext[AgentDependencies], **arguments):
        kind = str(arguments.get("kind", "explore") or "explore")
        policy = POLICIES.get(kind)
        contract = DelegationContract(
            objective=str(arguments.get("objective", "") or ""),
            kind=kind,
            # The contract asks for everything its kind is allowed to do; the runner
            # narrows it. Asking for less here would make the parent model the one
            # deciding permissions, which is the thing this design exists to prevent.
            allowed_tools=list(policy.allowed_tools) if policy else [],
            resource_scope=[str(item) for item in (arguments.get("resource_scope") or [])],
            evidence_required=[str(item) for item in (arguments.get("evidence_required") or [])],
            # Depth stays 0 here: children cannot delegate, so there is no way in.
            depth=0,
        )
        result = await _run_child(ctx.deps, contract)
        return ToolReturn(return_value=result.as_dict())

    return Tool.from_schema(
        invoke,
        name="delegate",
        description=DELEGATE_DESCRIPTION,
        json_schema=inline_schema(DelegateInput.model_json_schema()),
        takes_ctx=True,
        # A barrier even with the switch on, and deliberately not derived from it:
        # a child runs its own model requests against the same workspace, so
        # overlapping it with a parent call would interleave two trajectories and
        # let the child's writes land while the parent is mid-step.
        sequential=True,
    )


def _tools(use_tools: bool, user_text: str = "", *, selected: set[str] | None = None) -> tuple[list[Tool], int, int]:
    """The tool set for one Run.

    ``selected`` is the routing verdict: when retrieval decided which capabilities
    bear on this request, that decision is what undeferring is based on. It is not
    the same object as the keyword pre-pass below, and passing it is what keeps the
    schema and the context answering from one ranking instead of two.
    """
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

    if subagents_enabled():
        # Off by default, so the default tool schema is byte-identical to before this
        # existed. Adding a tool is not free: it is one more way to spend a Run.
        tools.append(_delegate_tool())
        visible_schema_chars += len(
            json.dumps(inline_schema(DelegateInput.model_json_schema()), ensure_ascii=False)
        )
        visible_count += 1

    if plugin_enabled("remote-sensing"):
        from .capabilities.domain_runtime import DEFINITIONS
        initially_selected = (
            selected if selected is not None else matching_domain_tools(user_text)
        )

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


def _fabric_router(box, deps=None):
    """One router per Run, or ``None`` when the fabric is off.

    Built once and shared by the context compiler and by tool routing. Building it
    twice would not just be wasteful: with code intelligence on, ``build`` indexes
    the workspace, and a second router would pay for that index a second time and
    hand the two consumers two independently ranked views of one request.
    """
    if not retrieval_fabric.enabled():
        return None
    preflight = getattr(deps, "failure_preflight", None)
    try:
        return retrieval_fabric.build(box, failures=getattr(preflight, "store", None))
    except Exception:
        return None


def _routing_selection(router, text: str) -> set[str] | None:
    """Which tools the ranked evidence says this request bears on.

    ``None`` means "routing did not decide", which is deliberately different from an
    empty set: with the fabric off, or with a retrieval that raised, domain
    selection falls back to the keyword pre-pass rather than deferring everything.
    A Run that loses its routing must not also lose its schemas.
    """
    if router is None or not (text or "").strip():
        return None
    try:
        result = retrieval_fabric.retrieve(router, text, top_k=12)
    except Exception:
        return None
    selected: set[str] = set()
    for candidate in getattr(result, "candidates", ()) or ():
        reference = getattr(candidate, "exact_reference", None) or {}
        tool = reference.get("tool")
        if tool:
            selected.add(str(tool))
        # A domain group stands for several tools; undeferring the group's name
        # would undefer nothing, so the group is expanded to its members.
        for member in (reference.get("tools") or ()):
            selected.add(str(member))
    return selected or None


def _retrieval(box, deps=None, router=None):
    """One retrieval call for this Run, or ``None`` when the fabric is off.

    Built per Run, from the toolbox this Run owns, so no source can outlive the
    project it belongs to. Returning ``None`` rather than an empty fabric is
    deliberate: the compiler then omits the retrieval section entirely, and a request
    with no retrieved evidence does not look like one that searched and found nothing.
    """
    if router is None:
        router = _fabric_router(box, deps)
    if router is None:
        return None

    def retrieve_for(query: str):
        try:
            return retrieval_fabric.retrieve(router, query)
        except Exception:
            # Retrieval is an enhancement, never a precondition: a broken source must
            # not cost the Run its plan, its facts or its tools.
            return None

    return retrieve_for


def _done_event(box, deps, state: str, **extra) -> dict:
    """The terminal event, carrying what the Run may claim about what it produced.

    Attached on **every** ending, not only on a clean one. Ending paused, cancelled or
    failed with no verification at all was the silent case this layer exists to close:
    a Run that wrote two files and then hit a guard reported ``state=paused`` and
    nothing else, so nothing said "these files do not answer the task". The state field
    already says how the Run ended; what was missing is what its artifacts are worth.
    """
    payload: dict = {"type": "done", "artifacts": box.created, "state": state}
    try:
        verification = _verification_payload(box, deps)
    except Exception:
        # Verification observes; it never decides how a Run ends, and it must never
        # be the reason a terminal event is not emitted at all.
        verification = None
    if verification:
        payload["verification"] = verification
    payload.update(extra)
    return payload


def _artifact_path(box, asset: dict) -> str | None:
    """Where the bytes of a produced asset actually are.

    Asked from the store, not read off the asset dict: an asset stored by content has
    no ``path`` key at all, and ``managed_path`` is only populated for assets
    registered *by* path from outside. Reading ``asset.get("path")`` here returned
    ``None`` for every product a Run ever created, which silently disabled the whole
    verification layer -- no file, no ``product_qa``, so every delivery was reported
    ``delivered_unverified`` no matter what its producer had declared. The unit tests
    never caught it because they hand the verifier an artifact with a path already on
    it.
    """
    identifier = str(asset.get("id") or "")
    store = getattr(box, "store", None)
    owner = getattr(box, "owner", "")
    if store is not None and identifier:
        try:
            resolved = store.path(identifier, owner)
            if resolved is not None:
                return str(resolved)
        except Exception:
            pass
    for key in ("managed_path", "path"):
        value = asset.get(key)
        if value:
            return str(value)
    return None


def _verification_payload(box, deps) -> dict | None:
    """What this Run is entitled to claim about what it delivered.

    ``None`` when there is nothing to verify -- a turn that answered a question owes
    no deliverable, and calling that a failed delivery would be a lie in the other
    direction. Reported only when the Run produced an artifact or recorded a
    requirement, which is when "is it finished" is a question with an answer.

    The default outcome is ``delivered_unverified``, and that is the point: a file was
    written and nothing established that it means what was asked. Reaching
    ``verified_complete`` requires a verifier for that artifact kind, and none is
    registered by default.
    """
    try:
        failures: list[str] = []
        blocked: list[str] = []
        for entry in getattr(deps, "observation_log", None) or []:
            if entry.get("ok"):
                continue
            name = str(entry.get("tool") or "")
            if entry.get("code") == "duplicate_failed_call":
                blocked.append(name)
            else:
                failures.append(name)

        artifacts = []
        for asset in list(getattr(box, "created", None) or [])[:10]:
            if not isinstance(asset, dict):
                continue
            try:
                path = _artifact_path(box, asset)
                descriptor = describe_artifact(
                    asset, box.chat_id,
                    path=Path(str(path)) if path else None,
                    # Measured on purpose: a semantic verdict about a raster is not
                    # available without reading it, and product_qa is a decimated
                    # read, so the cost is bounded. Skipping it would leave every
                    # product permanently at `delivered_unverified`.
                    product=True,
                )
                # Both of these are carried for the verifier and are not part of what
                # the user is shown: the file itself, and the producer's declaration
                # of what the values mean. ``describe_artifact`` only forwards a
                # whitelist of metadata keys, and a verifier needs the whole claim.
                if path:
                    descriptor["path"] = str(path)
                metadata = asset.get("metadata")
                if isinstance(metadata, dict) and metadata:
                    descriptor["metadata"] = dict(metadata)
                artifacts.append(descriptor)
            except Exception:
                continue

        open_ids: list[str] = []
        blocked_ids: list[str] = []
        completion = "unknown"
        try:
            plan = box._plan_store().load()
            open_ids = [str(item.get("id")) for item in plan.open_requirements()]
            blocked_ids = [str(item.get("id")) for item in plan.blocked_requirements()]
            completion = plan.completion_state()
        except Exception:
            pass

        if not artifacts and not open_ids and not blocked_ids:
            return None
        facts = RunFacts(
            tool_failures=failures, blocked_calls=blocked, artifacts=artifacts,
            open_requirements=open_ids, blocked_requirements=blocked_ids,
            plan_completion=completion,
            stop_reason=str(getattr(deps, "pause_blocker", "") or ""),
        )
        report = VerificationService(default_registry()).evaluate(facts)
    except Exception:
        # Verification observes; it never decides whether a Run may finish.
        return None
    return {
        "state": report.state,
        "verified": report.verified,
        "reasons": report.reasons[:4],
        "next_actions": report.next_actions[:3],
    }


async def _final_statement(
    *, model, instructions: str, history: list, reason: str, model_settings,
) -> str | None:
    """One last model request, without tools, to put the machine state into words.

    Whatever ended the turn -- an exhausted budget, a guard rail, a context limit --
    leaves a fact the model is better placed to explain than the Runtime is: what it
    established, what it could not finish, and what the user would have to supply.
    The Runtime's own error text can only name the limit; the model can account for
    the work done against it.

    The request goes through a separate tool-less agent, because `tools` is fixed when
    an agent is constructed and cannot be overridden per run. That is what guarantees
    the closing statement cannot become another round of work.

    Returns ``None`` whenever the statement could not be produced -- an unreachable
    model, an empty history, a rejection. That is deliberate: the caller then reports
    what actually happened instead of inventing a conclusion.
    """
    if not history:
        return None
    speaker = Agent(
        model=model, instructions=instructions, tools=[], retries=0, max_concurrency=1,
    )
    messages = [
        *history,
        ModelRequest(parts=[UserPromptPart(
            "本轮工作到此为止。" + reason + " 请直接用中文向用户交代：你已经完成了什么、"
            "得到或产出了什么、卡在哪个具体前提上（写明失败类别与可核对的证据），"
            "以及用户需要提供什么才能继续。不要调用工具，不要重复已经说过的分析过程，"
            "也不要声称完成了尚未完成的事。"
        )]),
    ]
    try:
        # Streamed rather than `run`: the closing statement is plain text, and `run`
        # would require the caller's model to support non-streamed requests as well.
        async with speaker.run_stream(
            message_history=messages,
            model_settings=model_settings,
        ) as streamed:
            output = await streamed.get_output()
    except Exception:
        # A wrap-up that fails must not replace the real cause with its own failure;
        # the caller reports the original reason.
        return None
    return output.strip() if isinstance(output, str) and output.strip() else None


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
    continuation_reviewer=None,
):
    """Stream one PydanticAI run using the existing project services."""
    registry = workspace_registry or WorkspaceRegistry(os.getenv("DATA_ROOT", "/data"))
    cancelled = cancelled or (lambda: False)
    pause_requested = pause_requested or (lambda: False)
    latest_user = _latest_user(messages)
    box = RuntimeTools(
        store, owner, asset_ids, registry, latest_user, memory_manager,
        cancelled=cancelled, pause_requested=pause_requested,
    )
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
    )
    if publish is not None:
        def publish_job_status(payload: dict) -> None:
            publish({"type": "job_status", **payload})
        box.publish_job_status = publish_job_status
    router = _fabric_router(box, deps)
    tools, _, _ = _tools(use_tools, latest_user, selected=_routing_selection(router, latest_user))
    compiler = ContextCompiler(
        box, run_facts=run_facts, project_context=project_context,
        retrieval=_retrieval(box, deps, router),
    )
    estimator = TokenEstimator()
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

    def file_key(call: ToolCallPart) -> str | None:
        return box.observation_key(call.tool_name, call.args_as_dict())

    def record_ledger(ledger: dict) -> None:
        deps.context_ledger = ledger

    budget_compaction = RequestBudgetCompaction(
        estimator=estimator,
        file_key=file_key,
        keep_pairs=int(os.getenv("CONTEXT_KEEP_TOOL_PAIRS", "4")),
        ledger_keep_messages=int(os.getenv("CONTEXT_KEEP_MESSAGES", "6")),
        record=record_ledger,
    )

    async def before_model_request(ctx, request_context):
        if ctx.deps.pause_requested():
            raise AgentPaused("Run paused before the next model request.")
        ctx.deps.model_request_number += 1
        number = ctx.deps.model_request_number
        params = request_context.model_request_parameters
        if ctx.deps.pause_reason:
            # Repeated work is blocked, but the model retains responsibility for
            # explaining its observation to the user. Do not raise a Runtime pause
            # merely because a prior tool-free request did not finish the answer.
            ctx.deps.final_response_offered_at = number
            params.function_tools = []
            params.native_tools = []
            params.instruction_parts = [
                *params.instruction_parts,
                InstructionPart(ctx.deps.pause_reason),
            ]
        # Step 1 (tool selection) already happened: `params` holds exactly the tools
        # the model will see.  Step 2 assembles instructions and runtime facts.
        visible = [
            tool for tool in (getattr(params, "function_tools", ()) or ())
            if _visible(params, getattr(tool, "name", ""))
        ]
        runtime_facts, manifest, _ = compiler.compile(
            _latest_user(messages), visible, len(request_context.messages)
        )
        params.instruction_parts = [
            *params.instruction_parts,
            *(InstructionPart(part) for part in runtime_facts),
        ]
        manifest.update({
            "kernel": "pydantic-ai",
            "agent_run_id": agent_run_id,
            "system_prompt_chars": len(SYSTEM),
        })
        # Steps 3 and 4 run in `RequestBudgetCompaction` (dedupe, clear, ledger,
        # then the measured budget decision).  Its ledger is merged here so the
        # persisted record describes exactly the messages about to be sent.
        ledger = budget_compaction.ledger_for(
            request_context.messages, request_context.model_request_parameters
        )
        breakdown = estimator.breakdown(
            request_context.messages, params,
            extra_text="\n\n".join(runtime_facts[:-1]),
        )
        estimated = int(
            ledger.get("estimated_input_tokens")
            or estimator.estimate_request(
                request_context.messages, params,
                extra_text="\n\n".join(runtime_facts),
            )
        )
        output_reserve = output_reserve_tokens()
        window = context_limit_tokens()
        manifest.update({
            "compaction": ledger.get("compaction"),
            "context_breakdown": breakdown,
            "history_chars": sum(len(_message_text(m)) for m in request_context.messages),
            "estimated_input_tokens": estimated,
            "estimated_total_tokens": estimated + output_reserve,
            "context_limit_tokens": window,
            "output_reserve_tokens": output_reserve,
            "safety_margin_tokens": safety_margin_tokens(window),
            "input_budget_tokens": ledger.get("input_budget_tokens")
            or input_budget_tokens(limit=window, reserve=output_reserve),
            "estimated_input_tokens_before_compaction": ledger.get("estimated_input_tokens_before"),
            "tokens_reclaimed": ledger.get("tokens_reclaimed"),
            "compaction_tiers_applied": ledger.get("compaction_tiers_applied"),
            "calibration_ratio": estimator.ratio,
            "budget_ok": True,
        })
        ctx.deps.pending_input_estimate[number] = estimated
        # Step 5: persist exactly what will be sent, before the request goes out.
        if ctx.deps.begin_step:
            ctx.deps.step_ids[number] = ctx.deps.begin_step(number, manifest, estimated)
        else:
            standalone_lifecycle_events.append({
                "type": "model_call", "number": number, **manifest,
                "estimated_tokens": estimated,
            })
        return request_context

    async def after_model_request(ctx, *, request_context, response):
        number = ctx.deps.model_request_number
        usage = response.usage
        usage_data = asdict(usage) if is_dataclass(usage) else {}
        # Fold the provider's real input count back into the estimator so the next
        # request is budgeted against measurement, not a fixed guess.
        estimated = ctx.deps.pending_input_estimate.pop(number, 0)
        actual = 0
        if isinstance(usage_data, dict):
            try:
                actual = int(usage_data.get("input_tokens") or 0)
            except (TypeError, ValueError):
                actual = 0
        if estimated and actual:
            estimator.calibrate(estimated=estimated, actual=actual)
        step_id = ctx.deps.step_ids.get(number)
        if step_id and ctx.deps.finish_step:
            ctx.deps.finish_step(step_id, usage_data)
        return response

    async def wrap_tool_execute(ctx, *, call, tool_def, args, handler):
        if ctx.deps.pause_requested():
            raise AgentPaused("Run paused before the next tool dispatch.")
        if ctx.deps.pause_reason:
            return ToolReturn(return_value=normalize_result({
                "ok": False, "outcome_ok": False,
                "error": "本轮已停止新的工具执行：" + ctx.deps.pause_reason,
                "failure": {"stage": "agent_control", "code": "run_paused"},
            }))
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
        read_only = call.tool_name in REVIEWABLE_READ_TOOLS
        continuation = ctx.deps.continuation
        if review_enabled and continuation.should_review(call.tool_name, read_only):
            try:
                grants = box.workspaces.list_grants(box.owner, box.chat_id)
                inputs = reachable_inputs(
                    attachments=box.attachment_context(), grants=grants,
                    workspace=box.workspace, materialised=box.input_paths,
                )
                inputs.pop("workspace_root", None)
                proposed = {"tool": call.tool_name, "arguments": args}
                if continuation_reviewer is not None:
                    assessment = await continuation_reviewer({
                        "user_goal": latest_user,
                        "recent_actions_and_observations": list(continuation.recent),
                        "available_inputs": inputs,
                        "proposed_action": proposed,
                    })
                else:
                    review_call_event = {
                        "type": "review_model_call", "action_id": action_id,
                        "review_number": continuation.reviews + 1,
                    }
                    if ctx.deps.publish:
                        ctx.deps.publish(review_call_event)
                    else:
                        standalone_lifecycle_events.append(review_call_event)
                    assessment = await review_proposed_action(
                        model=ctx.model, user_goal=latest_user,
                        recent=list(continuation.recent), proposed=proposed,
                        available_inputs=inputs,
                        model_settings=model_settings_for_run,
                        cancellation_token=token,
                    )
            except Exception:
                assessment = ReviewDecision()
            if not isinstance(assessment, ReviewDecision):
                assessment = ReviewDecision()
            assessment = continuation.record_review(assessment)
            review_event = {
                "type": "continuation_review", "action_id": action_id,
                "decision": assessment.decision, "reason": assessment.reason,
                "usage": assessment.usage,
            }
            if ctx.deps.publish:
                ctx.deps.publish(review_event)
            else:
                standalone_lifecycle_events.append(review_event)
            if assessment.decision != "allow":
                reason = assessment.reason or "拟执行的只读调用没有说明能获得什么新信息。"
                blocked = normalize_result({
                    "ok": False, "outcome_ok": False,
                    "error": "继续行动审查：" + reason,
                    "failure": {
                        "stage": "agent_control",
                        "code": "continuation_" + assessment.decision,
                        "operation_started": False,
                    },
                })
                continuation.observe(call.tool_name, args, blocked, read_only)
                if assessment.decision == "stop":
                    ctx.deps.pause_blocker = "continuation_review"
                    ctx.deps.pause_reason = (
                        "近期工具调用未形成可继续的依据，继续行动审查停止新的工具执行。"
                        + reason + " 请根据已取得的观察直接说明结果或所需输入。"
                    )
                if ctx.deps.finish_action:
                    ctx.deps.finish_action(
                        action_id, call.tool_name, blocked,
                        round(time.monotonic() - started, 3), attempt_id,
                    )
                return ToolReturn(return_value=blocked)
        if attempt_id and ctx.deps.mark_attempt_started:
            ctx.deps.mark_attempt_started(attempt_id)
        try:
            result = await handler(args)
        except Exception as exc:
            failure = execution_failure(exc)
            continuation.observe(call.tool_name, args, failure, read_only)
            if ctx.deps.finish_action:
                ctx.deps.finish_action(
                    action_id, call.tool_name, failure,
                    round(time.monotonic() - started, 3), attempt_id,
                )
            raise
        payload: Any = result.return_value if isinstance(result, ToolReturn) else result
        normalized = payload if isinstance(payload, dict) else {"ok": True, "data": payload}
        continuation.observe(call.tool_name, args, normalized, read_only)
        if ctx.deps.finish_action:
            ctx.deps.finish_action(
                action_id, call.tool_name, normalized,
                round(time.monotonic() - started, 3), attempt_id,
            )
        return result

    capabilities.append(Hooks(
        before_model_request=before_model_request,
        after_model_request=after_model_request,
        tool_execute=wrap_tool_execute,
        id="runtime-lifecycle",
    ))

    def file_key(call: ToolCallPart) -> str | None:
        return box.observation_key(call.tool_name, call.args_as_dict())

    # The harness pass anchors on provider-reported usage and is a cheap first
    # move; the Runtime pass below is the one that guarantees the final budget
    # and records the accounting, so it must run after it.
    context_window = context_limit_tokens()
    reserve = output_reserve_tokens()
    anchor_target = max(
        4000,
        min(
            int((context_window - reserve - safety_margin_tokens(context_window)) * 0.95),
            context_window - 2048,
        ),
    )
    capabilities.append(TieredCompaction(
        tiers=[
            DeduplicateFileReads(file_key=file_key),
            ClearToolResults(max_tokens=anchor_target, keep_pairs=3),
        ],
        target_tokens=anchor_target,
    ))
    capabilities.append(budget_compaction)
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
        yield _done_event(box, deps, "failed")
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
    max_requests = max_requests or _setting_value(store, "AGENT_MAX_ROUNDS")
    model_settings_for_run = _settings() if model is None else None
    review_enabled = continuation_reviewer is not None or (
        model is None and _setting_value(store, "CONTINUATION_REVIEW_ENABLED", True)
    )

    try:
        async with agent.run_stream_events(
              user_prompt=None,
              message_history=history,
              conversation_id=chat_id,
              run_id=agent_run_id,
              deps=deps,
              model_settings=model_settings_for_run,
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
              yield _done_event(
                  box, deps, "done", usage=usage_data,
                  **({"blocked_by": deps.pause_blocker} if deps.pause_reason else {}),
              )
    except UsageLimitExceeded:
        reason = f"已用完本轮的 {max_requests} 次模型调用预算。"
        # The budget is spent, but the model is the one holding the account of what it
        # did with it. The run's own message history is still in hand here -- the limit
        # is raised before the request hook that gives the pause path its closing turn
        # -- so one tool-less request turns that into an answer for the user instead of
        # a template sentence from the Runtime.
        statement = await _final_statement(
            model=model or _model(), instructions=SYSTEM, history=history,
            reason=reason, model_settings=model_settings_for_run,
        )
        if statement:
            yield {"type": "message", "content": statement}
        else:
            # No statement could be produced (an unreachable model, a rejection).
            # Report what actually happened rather than implying the model explained
            # itself.
            yield {"type": "error", "content": reason + "现场已保存，可补充要求后继续。",
                   "state": "paused"}
        yield _done_event(box, deps, "paused")
    except AgentPaused as exc:
        # A guard rail stopped the turn. The guard has two shapes and they need
        # different handling:
        #
        # * it fired while a tool call was still being dispatched, so the model never
        #   saw the guard's instruction -- only a closing turn can produce the account
        #   the user is owed;
        # * it fired during the tool-free request the guard itself offered, so the
        #   model has already been asked and a second request would just repeat it.
        #
        # Which one happened is exactly what ``final_response_offered_at`` records; it
        # is compared with ``<=`` because the guard can also land inside the request it
        # just offered, and because a later tool call is answered with `run_paused` and
        # lets the loop carry on -- neither means the model has had its say.
        # Either way the turn must not end with the Runtime's own threshold text where
        # the model's explanation belongs.
        reason = deps.pause_reason or str(exc)
        statement = None
        if deps.final_response_offered_at <= deps.model_request_number:
            statement = await _final_statement(
                model=model or _model(), instructions=SYSTEM, history=history,
                reason=reason + _stall_account(deps), model_settings=model_settings_for_run,
            )
        if statement:
            yield {"type": "message", "content": statement}
        else:
            yield {"type": "error", "content": _pause_account(reason, deps), "state": "paused"}
        yield _done_event(box, deps, "paused")
    except ContextBudgetExceeded as exc:
        # A recoverable pause with an explicit blocking reason, not a generic
        # failure: the user can trim instructions, raise the window, or continue
        # in a new Turn.
        yield {
            "type": "error",
            "content": f"上下文预算不足，本轮已暂停（context_budget）：{exc}",
            "state": "paused",
            "blocked_by": "context_budget",
            "context_budget": exc.ledger,
        }
        yield _done_event(box, deps, "paused")
    except RunCancelled:
        yield {"type": "error", "content": "任务已取消；不会启动后续行动。", "state": "canceled"}
        yield _done_event(box, deps, "canceled")
    except ModelAPIError as exc:
        yield {"type": "error", "content": f"模型服务失败：{exc}", "state": "failed"}
        yield _done_event(box, deps, "failed")
    except UnexpectedModelBehavior as exc:
        yield {"type": "error", "content": f"模型响应无法执行：{exc}", "state": "failed"}
        yield _done_event(box, deps, "failed")
    finally:
        watcher.cancel()
