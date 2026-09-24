"""Import-order-independent discovery of core capability declarations."""

from __future__ import annotations

from ..kernel.spec import ToolSpec


def load_specs() -> tuple[ToolSpec, ...]:
    # Service implementations and optional execution dependencies are not loaded
    # by discovery; each tool module owns its complete public declaration.
    from .artifacts.tool import SPECS as artifact_specs
    from .code_run.tool import SPECS as code_specs
    from .dependency_install.tool import SPECS as dependency_specs
    from .domain_guides.tool import SPECS as guide_specs
    from .environment.tool import SPECS as environment_specs
    from .fs.tool import SPECS as fs_specs
    from .job_cancel.tool import SPECS as cancel_specs
    from .job_status.tool import SPECS as status_specs
    from .knowledge_search.tool import SPECS as knowledge_specs

    return (
        *fs_specs,
        *code_specs,
        *dependency_specs,
        *environment_specs,
        *status_specs,
        *cancel_specs,
        *artifact_specs,
        *knowledge_specs,
        *guide_specs,
    )


__all__ = ["load_specs"]
