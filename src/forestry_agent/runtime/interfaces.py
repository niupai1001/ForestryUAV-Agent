"""运行核心的接口与值对象。

这些是本项目的约定，不是第三方库提供的 API。每个接口只在一处定义，由启动
入口装配具体实现；能力组件（文件、影像、作业）只向 ``ToolRegistry`` 注册，
不反向依赖运行循环。第三方驱动（Pydantic AI / Harness）作为 ``ModelAdapter``
后面的实现使用，不把它的内部对象扩散到业务代码。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class RunScope:
    """一次回合的明确身份。

    只携带身份、冻结设置和取消句柄所需的最小信息；不是可以访问全部内核状态
    的万能对象。组件需要什么就显式接收什么，避免重新形成共享可变状态。
    """

    task_id: str
    turn_id: str
    revision: int
    assignment_id: str | None = None
    settings: Mapping[str, Any] = field(default_factory=dict)

    @property
    def visible(self) -> bool:
        """正文草稿是否属于用户的对话。委派角色返回结构化结果，不向对话写草稿。"""
        return self.assignment_id is None


@dataclass
class RequestTrace:
    """一次模型请求的传输阶段计时。

    每次请求单独创建：后一次请求不会复用前一次的阶段字典，迟到的响应头或片段
    不会被记到当前请求上。只用于证据，不参与判定。
    """

    request_id: str
    started_at: float
    budget_seconds: float
    headers_at: float | None = None
    first_fragment_at: float | None = None
    fragments: int = 0
    timeout_reported: bool = False

    @property
    def deadline(self) -> float:
        return self.started_at + self.budget_seconds

    def _elapsed(self, value: float | None) -> float | None:
        return None if value is None else round(value - self.started_at, 3)

    @property
    def layer(self) -> str:
        """未观测到响应头时只能说明传输层未返回，不能断言模型正在加载。"""
        if self.headers_at is None:
            return "transport"
        if self.first_fragment_at is None:
            return "prefill_or_server"
        return "generation"

    def note_fragment(self, at: float) -> bool:
        """记录一个原生片段；首个片段返回 True。"""
        self.fragments += 1
        if self.first_fragment_at is None:
            self.first_fragment_at = at
            return True
        return False

    def evidence(self, **extra) -> dict:
        payload = {
            "request_id": self.request_id,
            "headers_seconds": self._elapsed(self.headers_at),
            "first_fragment_seconds": self._elapsed(self.first_fragment_at),
            "fragments": self.fragments,
            "budget_seconds": self.budget_seconds,
            "layer": self.layer,
        }
        payload.update(extra)
        return payload


@dataclass(frozen=True)
class ToolSpec:
    """一份能力定义：稳定名称、日志动作类型、调用函数和调度声明。

    schema 由这份定义派生，不再单独维护模型可见清单；``kind`` 是唯一的准入与
    预算通道使用的动作类型；``mode`` 声明它能不能和其他调用同时执行——只有明确
    只读且并发安全的工具才写 ``parallel``，其余保持默认串行。
    """

    name: str
    kind: str
    func: Callable[..., Any]
    component: str
    mode: str = "serial"


@dataclass(frozen=True)
class ModelChannel:
    """一次运行动用的模型通道：模型对象、它自己的 HTTP 客户端和请求阶段槽。

    ``trace_slot`` 属于这条通道（一次运行一个客户端，请求按顺序发出），响应头
    回调通过它绑定当前请求；不同运行、不同委派各有自己的通道，不会互相覆盖。
    """

    model: Any
    client: Any
    trace_slot: dict = field(default_factory=dict)

    async def aclose(self) -> None:
        await self.client.aclose()


@runtime_checkable
class ModelAdapter(Protocol):
    """模型边界。

    负责传输（客户端、地址、请求头）和单次请求的墙钟预算与原生流映射；不实现
    第二套工具循环——已安装的驱动提供循环，额外再写一份会重复恢复与配对话义。
    """

    def open(self, profile, run: RunScope | None = None) -> ModelChannel: ...

    def request_guard(self, channel: ModelChannel | None = None, *, budget_seconds: float) -> Callable[..., Awaitable[Any]]: ...


@runtime_checkable
class ToolRegistry(Protocol):
    """工具定义、安装与执行的唯一入口。"""

    def register(self, spec: ToolSpec) -> None: ...

    def unregister(self, *names: str) -> None: ...

    def names(self) -> tuple[str, ...]: ...

    def install(self, agent) -> None: ...


@runtime_checkable
class SessionRepository(Protocol):
    """消息与事件提交边界；事务、表名留在存储实现里。"""

    def read(self, task_id: str) -> dict: ...

    def commit(self, task_id: str, kind: str, mutate, payload: dict | None = None) -> dict: ...


StreamEvent = tuple[str, dict]
ThinkingSink = MutableMapping[tuple[str, int], str]
