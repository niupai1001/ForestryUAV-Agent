"""Bounded, trajectory-aware review of proposed read-only actions.

The reviewer sees observations as tool results, not as a derived fact database.
It is advisory until the model has had one opportunity to reconsider.  The
ordinary PydanticAI loop remains responsible for choosing the next action.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, is_dataclass
import json
from typing import Any

from pydantic_ai import Agent


REVIEW_INSTRUCTIONS = """你是 Agent Harness 的一次性继续行动审查器，不执行任务，也不调用工具。
输入包含用户目标、近期原始工具调用及结果、当前可用输入和一项拟执行的只读调用。
只判断该调用是否有合理机会得到之前没有、且可能改变任务处理的新信息。
允许有根据的替代搜索、错误恢复和必要验证；不要因为单次失败或暂时没有产物就要求停止。
若拟调用只是重复已检查的范围，或依赖当前不存在且只有用户能提供的输入，选 reconsider；
若此前已要求重新选择而模型仍提出无新依据的行动，选 stop。其他情况选 allow。
只输出 JSON：{"decision":"allow|reconsider|stop","reason":"简短具体的理由"}。"""


@dataclass(frozen=True)
class ReviewDecision:
    decision: str = "allow"
    reason: str = ""
    usage: dict | None = None


def _observation_text(value: Any, limit: int = 2400) -> str:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= limit:
        return encoded
    half = (limit - 80) // 2
    return encoded[:half] + " ... [truncated middle] ... " + encoded[-half:]


def _weak_observation(result: dict) -> bool:
    if not result.get("outcome_ok", result.get("ok", False)):
        return True
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    if data.get("already_read") or data.get("unchanged"):
        return True
    for key in ("items", "matches", "results", "candidates"):
        if key in data and isinstance(data[key], list) and not data[key]:
            return True
    return False


@dataclass
class ContinuationReview:
    """Only schedule semantic review when the observable trajectory warrants it."""

    recent: list[dict] = field(default_factory=list)
    weak_reads: int = 0
    reads_since_change: int = 0
    reviews: int = 0
    reconsidered: bool = False
    max_reviews: int = 2

    def should_review(self, name: str, read_only: bool) -> bool:
        return (
            read_only and self.reviews < self.max_reviews
            and (self.weak_reads >= 2 or self.reads_since_change >= 6)
        )

    def observe(self, name: str, arguments: dict, result: dict, read_only: bool) -> None:
        self.recent.append({
            "tool": name,
            "arguments": arguments,
            "observation": _observation_text(result),
        })
        self.recent = self.recent[-6:]
        if read_only:
            self.reads_since_change += 1
            self.weak_reads = self.weak_reads + 1 if _weak_observation(result) else 0
        elif result.get("outcome_ok", result.get("ok", False)):
            # A successful write/job submission may make previously failed reads useful.
            self.weak_reads = 0
            self.reads_since_change = 0
            self.reconsidered = False

    def record_review(self, decision: ReviewDecision) -> ReviewDecision:
        self.reviews += 1
        if decision.decision == "stop" and not self.reconsidered:
            self.reconsidered = True
            return ReviewDecision("reconsider", decision.reason, decision.usage)
        if decision.decision == "reconsider":
            if self.reconsidered:
                return ReviewDecision("stop", decision.reason, decision.usage)
            self.reconsidered = True
        elif decision.decision == "allow":
            self.weak_reads = 0
            self.reads_since_change = 0
            self.reconsidered = False
        return decision


def _parse_review(text: str) -> ReviewDecision:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return ReviewDecision()
    try:
        parsed = json.loads(text[start:end + 1])
    except (TypeError, ValueError):
        return ReviewDecision()
    if not isinstance(parsed, dict) or parsed.get("decision") not in {"allow", "reconsider", "stop"}:
        return ReviewDecision()
    return ReviewDecision(parsed["decision"], str(parsed.get("reason") or "")[:500])


async def review_proposed_action(
    *, model, user_goal: str, recent: list[dict], proposed: dict,
    available_inputs: dict, model_settings: dict | None,
    cancellation_token=None,
) -> ReviewDecision:
    """One tool-less model request; uncertainty or reviewer failure allows the action."""
    reviewer = Agent(model=model, instructions=REVIEW_INSTRUCTIONS, tools=[], retries=0)
    payload = {
        "user_goal": user_goal,
        "recent_actions_and_observations": recent,
        "available_inputs": available_inputs,
        "proposed_action": proposed,
    }
    settings = dict(model_settings or {})
    settings["max_tokens"] = min(int(settings.get("max_tokens") or 512), 512)
    try:
        async with asyncio.timeout(60):
            async with reviewer.run_stream(
                user_prompt=json.dumps(payload, ensure_ascii=False), model_settings=settings,
                cancellation_token=cancellation_token,
            ) as run:
                output = await run.get_output()
                usage = run.usage
    except Exception:
        return ReviewDecision()
    parsed = _parse_review(output if isinstance(output, str) else "")
    return ReviewDecision(
        parsed.decision, parsed.reason,
        asdict(usage) if is_dataclass(usage) else None,
    )


__all__ = ["ContinuationReview", "ReviewDecision", "review_proposed_action"]
