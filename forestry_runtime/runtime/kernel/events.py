from typing import Protocol


class EventSink(Protocol):
    def __call__(self, event: dict) -> object: ...
