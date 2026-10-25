"""工具定义与安装的唯一入口。

一份定义同时派生模型可见的 schema、动作日志类型、调度声明和调用函数；能力组件
只注册，不接触运行循环。注册时检查重名、可调用性与调度声明，避免同一能力存在
两份定义或未声明的并发。
"""

from __future__ import annotations

from collections.abc import Callable

from .interfaces import ToolSpec
from .scheduling import MODES, PARALLEL, SERIAL


class ToolRegistry:
    """当前应用的工具目录。

    目录实例属于一次运行装配：能力函数会闭包本次运行的身份与授权范围，因此不
    跨运行共享。定义仍然来自同一个组件工厂，不需要手工维护第二份清单。
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if not spec.name.isidentifier():
            raise ValueError(f"工具名必须是合法标识符：{spec.name!r}")
        if not callable(spec.func):
            raise TypeError(f"工具 {spec.name} 没有可调用实现")
        if spec.mode not in MODES:
            raise ValueError(f"工具 {spec.name} 的调度声明未知：{spec.mode!r}")
        if spec.name in self._specs:
            raise ValueError(f"工具 {spec.name} 已由 {self._specs[spec.name].component} 注册")
        self._specs[spec.name] = spec
        return spec

    def unregister(self, *names: str) -> None:
        for name in names:
            self._specs.pop(name, None)

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def modes(self) -> dict[str, str]:
        """工具名 → 调度声明；执行通道按它决定能否并行。"""
        return {spec.name: spec.mode for spec in self._specs.values()}

    def parallel(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._specs.values() if spec.mode == PARALLEL)

    def describe(self) -> list[dict]:
        return [{"name": spec.name, "kind": spec.kind, "component": spec.component, "mode": spec.mode}
                for spec in self._specs.values()]

    def install(self, agent) -> None:
        """把定义装到当前驱动上；只在装配点调用一次。"""
        for spec in self._specs.values():
            agent.tool(spec.func)


def registrar(registry: ToolRegistry, component: str,
              mode: str = SERIAL) -> tuple[Callable[..., None], Callable[[], None]]:
    """返回注册函数与清理句柄；安装失败或关闭时可撤回自己的注册。"""

    def register(name: str, kind: str, func: Callable, declared: str | None = None) -> None:
        registry.register(ToolSpec(name=name, kind=kind, func=func, component=component,
                                   mode=declared or mode))

    def cleanup() -> None:
        registry.unregister(*[spec.name for spec in registry.specs() if spec.component == component])

    return register, cleanup
