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
from .context import (
    ContextBudgetExceeded,
    ContextCompiler,
    RequestBudgetCompaction,
    _collect_metrics,
    _collect_references,
    reachable_inputs,
)
from .continuation_review import ContinuationReview, ReviewDecision, review_proposed_action
from .capabilities.runtime import GENERIC_DEFINITIONS, RuntimeTools
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
你是一个面向林业低空无人机遥感的专业智能体。
以用户实际目标为核心，自主规划、使用工具并根据任务结果动态调整执行过程，不拘泥于固定流程。
充分利用林业、无人机遥感、摄影测量、遥感影像处理、空间分析、参数反演和相关科研知识，完成数据处理、分析、建模、解释与科研任务。
保持专业判断，区分已知信息与不确定内容，必要时说明依据和限制。
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
    seen_observations: set[str] = field(default_factory=set)
    environment_evidence: set[str] = field(default_factory=set)
    blocked_failures: int = 0
    pause_reason: str | None = None
    pause_blocker: str | None = None
    final_response_offered: bool = False
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
            "同一前提已连续三次失败，且证据没有变化。本轮停止尝试；"
            "请根据已观察到的失败说明缺少什么、已检查什么，以及需要用户提供什么。"
        )


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
        "parallel_tool_calls": False,
        "timeout": 180,
        "extra_body": {
            "think": os.getenv("OLLAMA_THINK", "true").lower() == "true",
            "options": {"num_ctx": int(os.getenv("OLLAMA_CONTEXT", "32768"))},
        },
    }


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
    previous = deps.failed_calls.get(fingerprint)
    if previous is not None:
        deps.blocked_failures += 1
        blocked = normalize_result({
            "ok": False,
            "outcome_ok": False,
            "error": "The identical failed call was not executed again.",
            "failure": {
                "stage": "agent_control",
                "code": "duplicate_failed_call",
                "previous_error": previous.get("error"),
                "previous_failure": previous.get("failure"),
            },
        })
        _record_prerequisite_failure(deps, blocked["failure"])
        if deps.blocked_failures >= 3 and deps.pause_reason is None:
            deps.pause_blocker = "identical_failed_call"
            deps.pause_reason = "连续三次重复同一个失败调用，且没有新的执行证据。本轮停止尝试，请说明失败前提。"
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
                    "同一读取已重复三次，内容没有变化。本轮停止重复读取；"
                    "请直接根据已获得的内容继续，或说明缺少什么。"
                )
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
    else:
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
    tool.defer_loading = deferred
    return tool


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
            "本轮到此为止。" + reason + " 请直接用中文向用户交代：你已经完成了什么、"
            "得到或产出了什么、卡在哪里、以及用户需要提供什么才能继续。"
            "不要调用工具，不要重复已经说过的分析过程。"
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
    tools, _, _ = _tools(use_tools, latest_user)
    compiler = ContextCompiler(
        box, run_facts=run_facts, project_context=project_context
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
        if ctx.deps.pause_reason and ctx.deps.final_response_offered:
            raise AgentPaused(ctx.deps.pause_reason)
        ctx.deps.model_request_number += 1
        number = ctx.deps.model_request_number
        params = request_context.model_request_parameters
        if ctx.deps.pause_reason:
            # Give the model one final turn to explain the observed blocker.
            ctx.deps.final_response_offered = True
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
              if deps.pause_reason:
                  yield {
                      "type": "done", "artifacts": box.created, "usage": usage_data,
                      "state": "paused", "blocked_by": deps.pause_blocker,
                  }
              else:
                  yield {"type": "done", "artifacts": box.created, "usage": usage_data}
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
        yield {"type": "done", "artifacts": box.created, "state": "paused"}
    except AgentPaused as exc:
        # A guard rail stopped the turn. The pause path already offered the model one
        # final turn, so it normally produced its own explanation; this covers the case
        # where the pause landed after that offer was used, or before any text went out.
        statement = None
        if not emitted_text:
            statement = await _final_statement(
                model=model or _model(), instructions=SYSTEM, history=history,
                reason=str(exc), model_settings=model_settings_for_run,
            )
        if statement:
            yield {"type": "message", "content": statement}
        else:
            yield {"type": "error", "content": str(exc), "state": "paused"}
        yield {"type": "done", "artifacts": box.created, "state": "paused"}
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
