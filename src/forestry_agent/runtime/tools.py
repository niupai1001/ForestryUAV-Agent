"""工具定义与安装的唯一入口。

一份定义同时派生模型可见的 schema、动作日志类型、调度声明和调用函数；能力组件
只注册，不接触运行循环。注册时检查重名、可调用性与调度声明，避免同一能力存在
两份定义或未声明的并发。
"""

from __future__ import annotations

from collections.abc import Callable

from .interfaces import ToolSpec, ToolDescriptor
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

    def install(self, agent, *, deferred=False) -> None:
        """把定义装到当前驱动上；只在装配点调用一次。"""
        for spec in self._specs.values():
            if deferred:
                from .discovery import ESSENTIAL
                agent.tool(spec.func, defer_loading=spec.name not in ESSENTIAL)
            else:
                agent.tool(spec.func)

    def descriptors(self, definitions) -> list[ToolDescriptor]:
        """Project current authorized schemas, including driver-native tools."""
        result = []
        for definition in definitions:
            spec = self._specs.get(definition.name)
            result.append(ToolDescriptor(name=definition.name,
                summary_zh=(definition.description or definition.name)[:240],
                summary_en=spec.summary_en if spec and spec.summary_en else definition.name.replace('_', ' '),
                capability=spec.kind if spec else 'native',
                domain=spec.component if spec else 'native', schema_ref=definition.name,
                accepts=spec.accepts if spec and spec.accepts else tuple(sorted(definition.parameters_json_schema.get('properties', {}))),
                produces=spec.produces if spec else (), modalities=spec.modalities if spec else (),
                permission_tier=spec.permission_tier if spec else 'runtime_guarded',
                version=spec.version if spec else '1'))
        return result


def registrar(registry: ToolRegistry, component: str,
              mode: str = SERIAL) -> tuple[Callable[..., None], Callable[[], None]]:
    """返回注册函数与清理句柄；安装失败或关闭时可撤回自己的注册。"""

    def register(name: str, kind: str, func: Callable, declared: str | None = None, **metadata) -> None:
        registry.register(ToolSpec(name=name, kind=kind, func=func, component=component,
                                   mode=declared or mode, **metadata))

    def cleanup() -> None:
        registry.unregister(*[spec.name for spec in registry.specs() if spec.component == component])

    return register, cleanup


def application_tools(engine, journal, *, assignment=None, spill_store=None):
    """Register the same application capabilities for either driver."""
    from ..doctor import doctor
    from .document_tools import register_document_tools
    from .environment_tools import register_environment_tools
    from .file_tools import register_file_tools
    from .job_tools import register_job_tools
    from .memory_tools import register_memory_tools
    from .native_tools import register_native_tools
    from .network_tools import register_web_tools
    from .workspace_tools import register_workspace_tools

    registry = ToolRegistry()
    register_file_tools(registry, resources=engine.resources, store=engine.store, journal=journal, assignment=assignment)
    register_document_tools(registry, documents=engine.documents, knowledge=engine.knowledge,
        vision=engine.vision, journal=journal, assignment=assignment)
    register_workspace_tools(registry, resources=engine.resources, store=engine.store, roles=engine.roles,
        cite=engine.cited_content, journal=journal)
    register_memory_tools(registry, memory=engine.memory, store=engine.store, journal=journal)
    if spill_store is not None:
        register_native_tools(registry, spill_store=spill_store, store=engine.store, publish=engine.publish)
    register_environment_tools(registry, config=engine.config, store=engine.store, resolution=engine.resolution,
        acquisition=engine.acquisition, environments=engine.environments, jobs=engine.jobs, doctor=doctor, journal=journal)
    register_job_tools(registry, engine=engine, jobs=engine.jobs, roles=engine.roles, store=engine.store, journal=journal)
    if engine.config.search_enabled:
        register_web_tools(registry, network=engine.network, journal=journal)
    return registry
