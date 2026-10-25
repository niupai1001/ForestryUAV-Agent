"""把模型接口返回的原生分片归一化为应用事件。

一个请求一个累积器：实时展示和最终消息使用同一份累积结果，不再分别计算。
思考、正文和工具参数各自分流，不能混成一段文本；工具参数草稿只为旧检查点的
``final_result`` 兼容保留，不代表正文。
"""

from __future__ import annotations

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)

from ..public_stream import AnswerStream
from .interfaces import StreamEvent, ThinkingSink


class AssistantStreamAccumulator:
    """一次模型请求的累积器：原生思考、正文分片和工具参数草稿。"""

    def __init__(self, *, thinking: ThinkingSink, request_key: str, visible: bool) -> None:
        self._thinking = thinking
        self._request_key = request_key
        self._visible = visible
        self._public: dict[int, str] = {}
        self._draft = AnswerStream()

    @property
    def public_text(self) -> str:
        """已收到的可见正文前缀，用于中断时保存实际内容。"""
        return "\n\n".join(self._public.values())

    def consume(self, event) -> list[StreamEvent]:
        """把一个原生事件映射为零到多个应用事件。"""
        if not self._visible:
            return []
        events: list[StreamEvent] = []
        thought = None
        if isinstance(event, PartStartEvent) and isinstance(event.part, ThinkingPart):
            thought = event.part.content
        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, ThinkingPartDelta):
            thought = event.delta.content_delta
        if thought:
            key = (self._request_key, event.index)
            self._thinking[key] = self._thinking.get(key, "") + thought
            events.append(("assistant.thinking.delta", {
                "delta": thought, "request_id": self._request_key, "part_index": event.index}))
        text = None
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            self._public[event.index] = event.part.content
            text = self.public_text
        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            self._public[event.index] = self._public.get(event.index, "") + event.delta.content_delta
            text = self.public_text
        elif not self._public and isinstance(event, PartStartEvent) and isinstance(event.part, ToolCallPart):
            text = self._draft.update(event.index, part=event.part)
        elif not self._public and isinstance(event, PartDeltaEvent) and isinstance(event.delta, ToolCallPartDelta):
            text = self._draft.update(event.index, delta=event.delta)
        if text is not None:
            events.append(("assistant.draft", {"text": text, "request_id": self._request_key}))
        return events
