from __future__ import annotations

from collections.abc import Iterable
import threading

from .spec import ToolSpec


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec]):
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError(f"Duplicate tool specification: {spec.name}")
            self._specs[spec.name] = spec

    def __len__(self) -> int:
        return len(self._specs)

    def __iter__(self):
        return iter(self._specs.values())

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def require(self, name: str) -> ToolSpec:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"Unknown tool specification: {name}")
        return spec

    def as_legacy_definitions(self) -> dict[str, tuple[type, str]]:
        return {
            spec.name: (spec.params, spec.description)
            for spec in self._specs.values()
        }

    def model_schemas(self) -> dict[str, dict]:
        return {spec.name: spec.model_schema() for spec in self._specs.values()}


def build_registry(specs: Iterable[ToolSpec]) -> ToolRegistry:
    return ToolRegistry(specs)


_RUNTIME_REGISTRY = ToolRegistry(())
_REGISTRY_LOCK = threading.RLock()


def runtime_registry() -> ToolRegistry:
    """Return the core registry without depending on runtime.tools import order."""
    global _RUNTIME_REGISTRY
    with _REGISTRY_LOCK:
        if len(_RUNTIME_REGISTRY) == 0:
            from ..capabilities import load_specs

            _RUNTIME_REGISTRY = build_registry(load_specs())
        return _RUNTIME_REGISTRY


def install_runtime_registry(
    registry: ToolRegistry, *, if_empty: bool = False
) -> ToolRegistry:
    global _RUNTIME_REGISTRY
    with _REGISTRY_LOCK:
        if not if_empty or len(_RUNTIME_REGISTRY) == 0:
            _RUNTIME_REGISTRY = registry
        return _RUNTIME_REGISTRY


def tool_spec_summary(name: str) -> dict | None:
    spec = runtime_registry().get(name)
    return spec.trace_summary() if spec is not None else None
