"""本地 OpenAI 兼容模型的适配实现。

这里只负责传输和单次请求的观测：创建 provider 客户端、绑定响应头阶段、执行
冻结的墙钟预算、把原生分片交给累积器。工具循环、恢复和上下文都由驱动和
其他组件负责，避免出现第二套运行语义。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from typing import Any

import httpx2
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import Config, ModelProfile
from ..contracts import Failure
from .interfaces import ModelChannel, RequestTrace, RunScope, ThinkingSink
from .streams import AssistantStreamAccumulator


class LocalModelAdapter:
    """为 Qwen/Ollama 等本地 OpenAI 兼容服务提供请求通道。"""

    def __init__(self, config: Config, publish: Callable[[str, str, dict], None]) -> None:
        self.config, self._publish = config, publish
        # 响应头回调没有请求对象可用，只能通过上下文取当前请求；作用域覆盖整个
        # 异步流消费，因此不同回合/委派并发时不会互相覆盖阶段数据。
        self._trace: ContextVar[RequestTrace | None] = ContextVar("forestry_model_trace", default=None)

    def open(self, profile: ModelProfile, run: RunScope | None = None) -> ModelChannel:
        client = httpx2.AsyncClient(timeout=httpx2.Timeout(300, connect=10), trust_env=False)
        slot: dict = {}

        async def note_headers(response) -> None:
            # 只有服务端接受请求后才会产生响应对象；没有记录就说明未观测到响应头。
            # 优先用本通道的请求槽：驱动的请求可能运行在另一个任务上下文中，
            # 那里看不到调用方设置的 ContextVar。
            trace = slot.get("current") or self._trace.get()
            if trace is not None and trace.headers_at is None:
                trace.headers_at = time.monotonic()

        client.event_hooks["response"].append(note_headers)
        model = OpenAIChatModel(profile.served_name, provider=OpenAIProvider(
            base_url=self.config.model_base_url, api_key="local-only", http_client=client),
            profile=OpenAIModelProfile(openai_chat_send_back_thinking_parts=False))
        return ModelChannel(model=model, client=client, trace_slot=slot)

    def request_guard(self, channel: ModelChannel | None = None, *, budget_seconds: float) -> Callable[..., Any]:
        """一次模型请求的墙钟边界。

        每次调用都创建新的请求状态；实测阶段和失败证据都绑定这一请求，不与下一次
        请求共用可变字典。身份取自当次 ``ctx.deps``，组件不持有整个 Deps。
        """

        async def guard(ctx, *, request_context, handler):
            scope = ctx.deps.scope
            trace = RequestTrace(request_id=ctx.deps.context_manifest_id or "stream",
                                 started_at=time.monotonic(), budget_seconds=budget_seconds)
            ctx.deps.request_trace = trace
            if channel is not None:
                channel.trace_slot["current"] = trace
            token = self._trace.set(trace)
            try:
                async with asyncio.timeout_at(trace.deadline):
                    return await handler(request_context)
            except TimeoutError as error:
                self.report_timeout(scope, trace)
                raise Failure("MODEL_REQUEST_TIMED_OUT",
                    f"单次模型请求超过冻结的墙钟时间预算（{budget_seconds:g} 秒，阶段 {trace.layer}），已停止本回合；"
                    "后台作业有自己的执行预算，不受此限制。", 422) from error
            finally:
                self._trace.reset(token)
                if channel is not None:
                    channel.trace_slot.pop("current", None)

        return guard

    def report_timeout(self, run: RunScope, trace: RequestTrace) -> None:
        """超时证据只记一次，重复路径不能把它写成两条不同归因。"""
        if trace.timeout_reported:
            return
        trace.timeout_reported = True
        self._publish(run.task_id, "model.request_timed_out", {"turn_id": run.turn_id, **trace.evidence()})

    def finished(self, run: RunScope, trace: RequestTrace, *, input_tokens: int, output_tokens: int) -> None:
        self._publish(run.task_id, "model.request_finished", {"turn_id": run.turn_id, **trace.evidence(
            total_seconds=round(time.monotonic() - trace.started_at, 3),
            input_tokens=input_tokens, output_tokens=output_tokens)})

    async def observe(self, run: RunScope, trace: RequestTrace | None, stream: AsyncIterator[Any],
                      *, thinking: ThinkingSink, request_key: str,
                      on_visible: Callable[[str], None] | None = None) -> str:
        """消费一次请求的原生流并发布思考/正文事件。

        流本身仍由驱动提供；这里只把分片映射为应用事件，并在同一累积器上保留完整
        正文。``on_visible`` 在每收到一段可见正文时同步更新调用方持有的前缀：取消会
        在任意片段处中断，返回值来不及交付，前缀必须边收边记。
        """
        accumulator = AssistantStreamAccumulator(thinking=thinking, request_key=request_key, visible=run.visible)
        try:
            async with asyncio.timeout_at(trace.deadline if trace else None):
                async for event in stream:
                    if trace is not None and trace.note_fragment(time.monotonic()):
                        self._publish(run.task_id, "model.stream_opened",
                                      {"turn_id": run.turn_id, "event": type(event).__name__, **trace.evidence()})
                    for kind, payload in accumulator.consume(event):
                        if kind == "assistant.draft" and on_visible is not None:
                            on_visible(payload["text"])
                        self._publish(run.task_id, kind, {"turn_id": run.turn_id, **payload})
        except TimeoutError as error:
            if trace is not None:
                self.report_timeout(run, trace)
            layer = trace.layer if trace is not None else "generation"
            budget = f"{trace.budget_seconds:g} 秒" if trace is not None else "冻结的"
            raise Failure("MODEL_REQUEST_TIMED_OUT",
                f"本次回答超过单次请求的墙钟时间预算（{budget}，停在 {layer} 阶段）；"
                "已保留会话和已收到的正文前缀，可发送新消息继续。后台作业不受影响。", 422) from error
        return accumulator.public_text
