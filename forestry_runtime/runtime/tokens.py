"""Token estimation for request budgeting.

The Runtime must decide *before* a request whether the request will fit the
model window.  Two failure modes matter:

* under-counting lets a request exceed ``num_ctx`` and the server silently drops
  the oldest tokens;
* over-counting makes the Runtime compact a request that would have fitted, and
  raise a budget error for a request that was fine.

The previous implementation measured ``len(json.dumps(message))`` for the whole
PydanticAI history and divided by four.  That is wrong twice over: the JSON
rendering of SDK objects adds field names, class tags and usage metadata that
are never sent to the model, and CJK text is roughly one token per character
rather than one per four characters.  A request locally estimated at 22,810
tokens was reported by the server as 13,568.

This module counts only model-visible text and weights CJK by script, then
calibrates against provider-reported usage once a response comes back.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Iterable, Sequence

# Roughly one token per character for CJK scripts and one per four characters
# for everything else.  The real tokenizer is not available offline; the
# calibration factor corrects whatever bias remains.
_CJK_RANGES = (
    (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x3040, 0x30FF),    # Hiragana / Katakana
    (0xAC00, 0xD7AF),    # Hangul syllables
    (0x20000, 0x2FA1F),  # CJK Extensions B-F
)


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def estimate_text(text: str) -> int:
    """Approximate token count for one string without calibration."""
    if not text:
        return 0
    cjk = sum(1 for character in text if _is_cjk(character))
    return cjk + max(0, len(text) - cjk) // 4


def _part_text(part: Any) -> str:
    """Return only the text a model would actually read for one message part."""
    if isinstance(part, str):
        return part
    kind = getattr(part, "part_kind", None)
    if kind in {"thinking", "reasoning", "snapshot"}:
        # Reasoning is normally sent back as a field, not as prompt text. Count it
        # at a discount rather than ignoring it entirely, so a long chain of
        # thinking is still visible to the budget without dominating it.
        content = getattr(part, "content", None)
        return content if isinstance(content, str) else ""
    chunks: list[str] = []
    content = getattr(part, "content", None)
    if isinstance(content, str):
        chunks.append(content)
    elif isinstance(content, (list, tuple)):
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            else:
                inner = getattr(item, "content", None)
                if isinstance(inner, str):
                    chunks.append(inner)
    for attribute in ("args", "tool_name", "tool_call_id"):
        value = getattr(part, attribute, None)
        if isinstance(value, str):
            chunks.append(value)
    return "\n".join(chunks)


def message_text(message: Any) -> str:
    """Concatenate the model-visible text of one message."""
    parts = getattr(message, "parts", None)
    if parts is None:
        return str(message)
    return "\n".join(filter(None, (_part_text(part) for part in parts)))


def _tool_schema_text(tool: Any) -> str:
    name = str(getattr(tool, "name", "") or "")
    description = str(getattr(tool, "description", "") or "")
    schema = getattr(tool, "parameters_json_schema", None)
    try:
        encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        encoded = ""
    return f"{name}\n{description}\n{encoded}"


def visible_tool_text(params: Any) -> tuple[list[str], int]:
    """Return (visible tool names, schema characters) for the outgoing request.

    Deferred tools that have not been revealed are not on the wire, so they must
    not be charged to the budget.
    """
    if params is None:
        return [], 0
    tools: Iterable[Any] = getattr(params, "function_tools", ()) or ()
    names: list[str] = []
    total = 0
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        visibility = "visible"
        resolver = getattr(params, "visibility_of", None)
        if callable(resolver):
            try:
                visibility = resolver(name)
            except Exception:
                visibility = "visible"
        if visibility in {"withheld", "via_history"}:
            continue
        names.append(name)
        total += len(_tool_schema_text(tool))
    return names, total


def instruction_text(params: Any) -> str:
    parts = getattr(params, "instruction_parts", None) or ()
    chunks: list[str] = []
    for part in parts:
        content = getattr(part, "content", None)
        chunks.append(content if isinstance(content, str) else str(content))
    return "\n".join(chunks)


class TokenEstimator:
    """Count model-visible tokens and calibrate against provider usage."""

    def __init__(self, *, floor: float = 0.5, ceiling: float = 2.0):
        self.floor = float(floor)
        self.ceiling = float(ceiling)
        self._ratio = 1.0
        self._samples = 0
        self._lock = threading.Lock()

    @property
    def ratio(self) -> float:
        with self._lock:
            return self._ratio

    @property
    def samples(self) -> int:
        with self._lock:
            return self._samples

    def estimate_text(self, text: str) -> int:
        return max(0, round(estimate_text(text) * self.ratio))

    def estimate_message(self, message: Any) -> int:
        return max(0, round(estimate_text(message_text(message)) * self.ratio))

    def estimate_history(self, messages: Sequence[Any]) -> int:
        return sum(self.estimate_message(message) for message in messages)

    def estimate_request(
        self,
        messages: Sequence[Any],
        params: Any = None,
        *,
        extra_text: str = "",
    ) -> int:
        """Estimate the full input for a request as it would be sent."""
        names, schema_chars = visible_tool_text(params)
        instructions = instruction_text(params)
        raw = (
            estimate_text(instructions)
            + schema_chars // 4
            + estimate_text(extra_text)
            + sum(estimate_text(message_text(message)) for message in messages)
        )
        return max(1, round(raw * self.ratio))

    def breakdown(
        self,
        messages: Sequence[Any],
        params: Any = None,
        *,
        extra_text: str = "",
    ) -> dict:
        """Per-component accounting, recorded for every model request."""
        names, schema_chars = visible_tool_text(params)
        instructions = instruction_text(params)
        history = 0
        tool_result_chars = 0
        for message in messages:
            history += estimate_text(message_text(message))
            for part in getattr(message, "parts", ()) or ():
                if getattr(part, "part_kind", "") in {"tool-return", "retry-prompt"}:
                    tool_result_chars += len(_part_text(part))
        ratio = self.ratio
        return {
            "instruction_tokens": round(estimate_text(instructions) * ratio),
            "tool_schema_tokens": round(schema_chars / 4 * ratio),
            "history_tokens": round(history * ratio),
            "runtime_facts_tokens": round(estimate_text(extra_text) * ratio),
            "tool_result_chars": tool_result_chars,
            "visible_tool_count": len(names),
            "visible_tool_names": names,
            "calibration_ratio": round(ratio, 4),
            "calibration_samples": self.samples,
        }

    def calibrate(self, *, estimated: int, actual: int) -> None:
        """Fold provider-reported input usage into the correction factor."""
        if estimated <= 0 or actual <= 0:
            return
        observed = actual / estimated
        if not (0.1 <= observed <= 10.0):
            return
        with self._lock:
            # Exponential moving average, biased to the newest measurement so a
            # change in content mix converges within a couple of requests.
            weight = 0.5 if self._samples == 0 else 0.35
            blended = (1 - weight) * self._ratio + weight * observed
            self._ratio = min(self.ceiling, max(self.floor, blended))
            self._samples += 1


def output_reserve_tokens() -> int:
    return max(0, int(os.getenv("OLLAMA_OUTPUT_TOKENS", "4096")))


def context_limit_tokens() -> int:
    return max(1024, int(os.getenv("OLLAMA_CONTEXT", "32768")))


def safety_margin_tokens(limit: int | None = None) -> int:
    """Headroom kept for tokenizer drift and tool-search reveals."""
    configured = os.getenv("CONTEXT_SAFETY_MARGIN_TOKENS")
    if configured:
        return max(0, int(configured))
    window = limit if limit is not None else context_limit_tokens()
    return max(512, min(2048, window // 16))


def input_budget_tokens(
    *,
    limit: int | None = None,
    reserve: int | None = None,
    margin: int | None = None,
) -> int:
    """Tokens available for instructions, tool schemas, and history."""
    window = limit if limit is not None else context_limit_tokens()
    out = reserve if reserve is not None else output_reserve_tokens()
    guard = margin if margin is not None else safety_margin_tokens(window)
    return max(1024, window - out - guard)


def context_utilisation() -> float:
    """Fraction of the window the input budget may use.

    Guards against a mis-set ``OLLAMA_CONTEXT`` that is far larger than the
    server's real ``num_ctx``: exceeding the window is silent truncation, while
    a deliberate fraction degrades predictably.
    """
    raw = os.getenv("CONTEXT_UTILISATION", "0.9")
    try:
        value = float(raw)
    except ValueError:
        value = 0.9
    return min(1.0, max(0.3, value))


__all__ = [
    "TokenEstimator",
    "context_limit_tokens",
    "context_utilisation",
    "estimate_text",
    "input_budget_tokens",
    "instruction_text",
    "message_text",
    "output_reserve_tokens",
    "safety_margin_tokens",
    "visible_tool_text",
]
