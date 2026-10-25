"""本轮实际发送内容的度量。

`PreparedInput` 回答三个问题：这一轮到底发送了什么（messages/tools/settings）、
这些内容按什么分项计费、每个分项是实测、估计还是未知。模型窗口判定只读这份
结果，不再把「保守上界」直接当成「模型实耗」，也不再让一个笼统的
"信息超过窗口" 掩盖真正的组成。

分项只描述事实，不做策略：是否压缩、是否重读由驱动和 `ContextBuilder` 决定。
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai_harness.compaction import estimate_context_tokens, estimate_token_count

from ..store import encode as _encode
from ..vision import visual_measure

ESTIMATOR = "native-usage-anchor-v10-dimension-bounded-vision"

# 分项名称。顺序即解释文本中的展示顺序。
TEXT = "text"
VISION = "vision"
TOOLS = "tool_schema"
FRAMING = "framing"
INSTRUCTION_DELTA = "instruction_delta"
USAGE_BASELINE = "usage_baseline"

LABELS = {
    TEXT: "对话文本",
    VISION: "图像",
    TOOLS: "工具定义",
    FRAMING: "消息框架",
    INSTRUCTION_DELTA: "指令增量",
    USAGE_BASELINE: "历史已付用量",
}

# 解释文本里的展示顺序：先内容，后定义，最后框架留位。
COMPONENT_ORDER = (TEXT, USAGE_BASELINE, INSTRUCTION_DELTA, VISION, TOOLS, FRAMING)

# 计量性质：actual 来自提供方实测，estimated 是上界估计，unknown 表示无法按
# 当前模型模板计量（只能按最坏情况留位）。
ACTUAL, ESTIMATED, UNKNOWN = "actual", "estimated", "unknown"


def byte_bound(text: str) -> int:
    """Conservative text estimate; measure actual provider usage separately."""
    return len(text.encode("utf-8"))


@dataclass(frozen=True)
class Component:
    """一个计费分项。`tokens` 始终是上界；`status` 说明这个上界的来源。"""

    name: str
    tokens: int
    status: str = ESTIMATED
    detail: str = ""

    def public(self) -> dict:
        return {"name": self.name, "label": LABELS.get(self.name, self.name),
                "tokens": self.tokens, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class PreparedInput:
    """本轮真正发出去的输入，以及它的分项计量。

    `messages`/`tools`/`settings` 是发送内容本身，不是事后重建的近似；调用方
    可以直接把它们交给模型通道。`components` 之和等于 `upper_bound`。
    """

    messages: tuple = ()
    tools: tuple = ()
    settings: Mapping[str, Any] = field(default_factory=dict)
    instructions: tuple = ()
    components: tuple = ()
    available: int = 0
    anchored: bool = False
    estimator: str = ESTIMATOR

    @property
    def upper_bound(self) -> int:
        return sum(component.tokens for component in self.components)

    def component(self, name: str) -> Component:
        return next((c for c in self.components if c.name == name), Component(name, 0, ACTUAL))

    @property
    def visual_tokens(self) -> int:
        return self.component(VISION).tokens

    @property
    def fits(self) -> bool:
        return self.available <= 0 or self.upper_bound <= self.available

    @property
    def overflow(self) -> int:
        return max(0, self.upper_bound - self.available)

    def breakdown(self) -> list[dict]:
        return [component.public() for component in self.components]

    def as_manifest(self) -> dict:
        """写进 `contexts` 记录的稳定形状；用于后续解释和重算。"""
        return {"estimator_id": self.estimator, "estimated_tokens_upper_bound": self.upper_bound,
                "available_input_tokens": self.available, "uses_provider_usage_anchor": self.anchored,
                "token_components": self.breakdown()}

    def explain(self, *, limit: str = "可用输入窗口") -> str:
        """一句话说清超出的是哪一项、它是实测还是估计。"""
        parts = []
        for component in sorted(self.components, key=lambda c: -c.tokens):
            if component.tokens <= 0:
                continue
            state = {ACTUAL: "实测", ESTIMATED: "估计", UNKNOWN: "未知"}[component.status]
            parts.append(f"{LABELS.get(component.name, component.name)}{component.tokens:,}（{state}）")
        detail = "、".join(parts) or "无可计费内容"
        return (f"编译输入保守估算 {self.upper_bound:,} tokens 超过{limit} {self.available:,} tokens；"
                f"分项：{detail}。")

    def dominant(self) -> Component:
        return max(self.components, key=lambda c: c.tokens, default=Component(TEXT, 0, ACTUAL))


def unpaid_components(current, measured):
    """Match exact paid occurrences; changed components retain their full cost."""
    paid = Counter(_encode(value) for value in measured or [])
    unpaid = []
    for value in current:
        key = _encode(value)
        if paid[key]:
            paid[key] -= 1
        else:
            unpaid.append(value)
    return unpaid


def instruction_components(instructions):
    """Accounting only: retain exact prompt bytes, split canonical app facts.

    Only the application's canonical facts envelope has stable field boundaries.
    Arbitrary instructions, malformed/noncanonical JSON and source text remain
    whole components. Matching occurrences never refunds old provider usage.
    """
    import json
    from ..context import TASK_FACT_PREFIX
    components = []
    for text in instructions:
        if text.startswith(TASK_FACT_PREFIX):
            payload = text[len(TASK_FACT_PREFIX):]
            try:
                facts, end = json.JSONDecoder().raw_decode(payload)
                if isinstance(facts, dict) and _encode(facts) == payload[:end]:
                    components.append({'kind': 'facts_frame', 'text': TASK_FACT_PREFIX + '{}'})
                    components.extend({'kind': 'facts_field', 'text': _encode({key: value})} for key, value in facts.items())
                    components.extend({'kind': 'facts_guidance', 'text': line} for line in payload[end:].splitlines(keepends=True))
                    continue
            except ValueError:
                pass
        components.append({'kind': 'instruction', 'text': text})
    return components


def measure(messages: Sequence, params, tools: Sequence[dict], *, available: int = 0,
            measured_tools_sha256: str | None = None, measured_tools: Sequence[dict] | None = None,
            measured_instructions: Sequence[str] | None = None,
            measured_usage_baseline: tuple[int, int] | None = None,
            settings: Mapping[str, Any] | None = None) -> PreparedInput:
    """Count a prepared request using native measured usage plus conservative deltas.

    `tools` 必须就是本轮发送的定义（从同一份能力定义派生），不是重新描述的副本。
    """
    from dataclasses import replace

    counted = list(messages)
    instructions = "\n".join(part.content for part in params.instruction_parts)
    # Count only: leave native messages/checkpoints untouched. The next request's
    # dynamic instructions may differ from the historical ModelRequest instructions.
    if counted and isinstance(counted[-1], ModelRequest):
        counted[-1] = replace(counted[-1], instructions=instructions)
    else:
        counted.append(ModelRequest(parts=[], instructions=instructions))
    anchored = any(isinstance(message, ModelResponse) and message.usage.input_tokens > 0 for message in counted)
    measured = anchored or measured_usage_baseline is not None
    components: list[Component] = []
    instruction_delta = 0
    if measured and measured_instructions is not None:
        # Provider usage already includes the previous request's instructions.
        # Keep that entire cost (including removed/changed parts), then pay the
        # full UTF-8 bound for every new or changed occurrence. Do not subtract
        # heuristic compaction reclaim from measured provider usage.
        instruction_delta = sum(byte_bound(value['text']) for value in unpaid_components(
            instruction_components([part.content for part in params.instruction_parts]),
            instruction_components(measured_instructions)))
        counted = [replace(message, instructions=None) if isinstance(message, ModelRequest) else message for message in counted]
    text_bound = estimate_context_tokens(counted, byte_bound, model_request_parameters=params) if anchored else estimate_token_count(counted, byte_bound)
    baseline_tokens = 0
    if not anchored and measured_usage_baseline is not None:
        # Native compaction may remove every response. A verified earlier request
        # still paid its fixed components. Retain ALL its input/output cost and
        # additionally pay for EVERY current message, not an estimated reclaim.
        # The two costs stay separate components so the sum is not counted twice.
        baseline_tokens = sum(measured_usage_baseline)
    # Unknown/changed schemas pay the full conservative cost. Exact schemas bound
    # to this response's measured request have already been paid by its native
    # input usage; do not charge them twice. Framing remains conservatively added.
    schema = _encode(tools)
    known_schema = measured and measured_tools_sha256 == hashlib.sha256(schema.encode()).hexdigest()
    unpaid_tools = unpaid_components(tools, measured_tools) if measured and measured_tools is not None else list(tools)
    schema_cost = 0 if known_schema or not unpaid_tools else byte_bound(_encode(unpaid_tools))
    framing = 256 + 16 * len(messages) + 32 * len(tools)
    vision = visual_measure(messages)
    components.append(Component(TEXT, text_bound,
        ESTIMATED, "按原生历史逐条估算" if anchored else "无实测锚点，按全部历史估算"))
    if baseline_tokens:
        components.append(Component(USAGE_BASELINE, baseline_tokens, ACTUAL, "已验证的上一请求实测用量"))
    components.append(Component(VISION, vision["tokens"], vision["status"], vision["detail"]))
    components.append(Component(TOOLS, schema_cost,
        ACTUAL if known_schema else ESTIMATED,
        "已由同身份实测请求付过" if known_schema else f"{len(unpaid_tools)} 个未付工具定义"))
    components.append(Component(FRAMING, framing, ESTIMATED, "消息与工具边界的保守留位"))
    if instruction_delta:
        components.append(Component(INSTRUCTION_DELTA, instruction_delta, ESTIMATED, "本轮新增或变更的指令"))
    return PreparedInput(messages=tuple(messages), tools=tuple(tools),
        settings=dict(settings or {}), instructions=tuple(p.content for p in params.instruction_parts),
        components=tuple(components), available=available, anchored=anchored)
