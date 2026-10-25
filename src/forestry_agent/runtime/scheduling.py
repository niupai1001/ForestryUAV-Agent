"""工具执行调度：默认串行、只读有界并行、修改与提交走独占边界。

调度只决定「什么时候可以开始执行」，不改变执行通道、准入、权限或动作记录。
取消后不再开始新调用；已经开始的调用继续跑完并把真实结果写回，保持消息里的
调用/结果配对——用取消协程宣称底层作业已停不算停止。

声明分两种：只有明确只读且并发安全的工具才进入并行侧，其余（含未声明者）走
独占侧。独占侧是写侧，会等到在途读结束再开始，并在它等待期间拒绝新的读者插
队，因此修改和计算提交不会和读取交错。

闸门按活动（任务）分开：同一任务内默认串行，不同任务之间互不阻塞——一个任务
的长作业观察不能让另一个任务的普通读取排队。
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass

from ..contracts import Failure

SERIAL, PARALLEL, EXCLUSIVE = "serial", "parallel", "exclusive"
MODES = (SERIAL, PARALLEL, EXCLUSIVE)

#: 同一个执行栈已经持有闸门时，嵌套调用直接放行：委派角色在父工具内部发起的
#: 调用不能和父工具争同一把闸门，否则会自锁。ContextVar 会随任务创建复制，因此
#: 子代理继承「已持有」标记。
_HOLDING: ContextVar[bool] = ContextVar("forestry_tool_slot", default=False)


@dataclass
class View:
    """一个闸门当前持有者的快照，用于证据与测试，不参与判定。"""

    readers: int = 0
    writer: bool = False
    waiting: int = 0


class _Gate:
    """写者优先的读写闸门。"""

    def __init__(self, parallel_reads: int):
        self.parallel_reads = max(1, parallel_reads)
        self.cond = asyncio.Condition()
        self.readers = 0
        self.writer = False
        self.writers_waiting = 0
        self.waiting = 0

    def view(self) -> View:
        return View(readers=self.readers, writer=self.writer, waiting=self.waiting)

    async def start(self, parallel: bool) -> None:
        async with self.cond:
            self.waiting += 1
            try:
                if parallel:
                    while self.writer or self.writers_waiting or self.readers >= self.parallel_reads:
                        await self.cond.wait()
                    self.readers += 1
                else:
                    self.writers_waiting += 1
                    try:
                        while self.writer or self.readers:
                            await self.cond.wait()
                    finally:
                        self.writers_waiting -= 1
                    self.writer = True
            finally:
                self.waiting -= 1

    async def finish(self, parallel: bool) -> None:
        async with self.cond:
            if parallel:
                self.readers -= 1
            else:
                self.writer = False
            self.cond.notify_all()


class ToolScheduler:
    """按声明发放执行许可；``serial`` 是未声明工具的默认行为。"""

    def __init__(self, parallel_reads: int = 2):
        self.parallel_reads = max(1, parallel_reads)
        self._gates: dict[str, _Gate] = {}
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def view(self, key: str = "") -> View:
        return self._gate(key).view()

    def close(self) -> None:
        """关闭时调用：拒绝新的开始，不打断已经开始的调用。"""
        self._closed = True

    def reopen(self) -> None:
        self._closed = False

    @staticmethod
    def mode(declared: str | None = None) -> str:
        return declared if declared in MODES else SERIAL

    def _gate(self, key: str) -> _Gate:
        gate = self._gates.get(key)
        if gate is None:
            gate = self._gates[key] = _Gate(self.parallel_reads)
        return gate

    def enter(self, mode: str | None = None, *, key: str = ""):
        """获取执行许可；返回的异步上下文在退出时释放（``async with`` 使用）。

        本身不是协程：发放许可要在 ``__aenter__`` 里等待，调用点因此能直接写在
        ``async with`` 里，不必先 await 一个对象。
        """
        if _HOLDING.get():
            return _Nested()
        if self._closed:
            raise Failure("EXECUTION_STOPPED", "应用正在关闭，本轮不再开始新的工具调用", 409)
        return _Slot(self._gate(key), self.mode(mode))


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Slot:
    def __init__(self, gate: _Gate, mode: str):
        self.gate, self.mode, self.token = gate, mode, None

    async def __aenter__(self):
        await self.gate.start(self.mode == PARALLEL)
        self.token = _HOLDING.set(True)
        return self

    async def __aexit__(self, *exc):
        if self.token is not None:
            _HOLDING.reset(self.token)
        await self.gate.finish(self.mode == PARALLEL)
        return False
