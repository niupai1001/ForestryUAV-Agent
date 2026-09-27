"""Execution-environment inspection capability declaration."""

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class EnvironmentCheckArgs(Args):
    modules: list[str] = Field(
        default_factory=list, max_length=40,
        description=(
            "Module names to probe in the job image. A module that probes importable is "
            "recorded as import-checked, which is what lets code_run start code that "
            "imports it; a module that is not importable must be installed with "
            "dependency_install first."
        ),
    )
    record_requirements: list[str] = Field(
        default_factory=list, max_length=20,
        description=(
            "Distribution names to bind this verification to, for example "
            "['numpy', 'rasterio']. Recording them is what satisfies code_run's import "
            "preflight for later code, so pass every distribution your code imports when "
            "you want a durable record rather than a one-off probe."
        ),
    )


SPECS = (
    ToolSpec(
        "environment_check",
        (
            "Report the real execution environment of a code job: the image that job "
            "will run (including one an install has committed, which is not the "
            "configured base image), the installed package manifest of the dependency "
            "directory, whether the named modules are importable there, and the capacity "
            "of the writable locations. Runs a short bounded probe in that image and "
            "never occupies the execution slot. Capacity is measured inside the "
            "container, because a cap on a tmpfs or a quota is invisible from the host. "
            "Use this instead of inferring that dependencies are missing from an empty "
            "workspace. A module that reports importable here is marked import-checked, "
            "so code_run will accept code importing it; when code_run refuses with "
            "blocked_by, call this tool with the refused module names (and, for a durable "
            "record, the same names as record_requirements)."
        ),
        EnvironmentCheckArgs, "EvidenceArtifact",
        Scope(reads=["workspace"]), SideEffect.NONE, "check_environment", "sandbox",
    ),
)

__all__ = ["EnvironmentCheckArgs", "SPECS"]
