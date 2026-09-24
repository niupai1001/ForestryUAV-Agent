"""Execution-environment inspection capability declaration."""

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class EnvironmentCheckArgs(Args):
    modules: list[str] = Field(default_factory=list, max_length=40)
    record_requirements: list[str] = Field(default_factory=list, max_length=20)


SPECS = (
    ToolSpec(
        "environment_check",
        "Report the real execution environment: the job image, the installed package "
        "manifest of the dependency directory, and whether the named modules are "
        "importable there. Runs a short bounded probe in the job image and never "
        "occupies the execution slot. Use this instead of inferring that dependencies "
        "are missing from an empty workspace.",
        EnvironmentCheckArgs, "EvidenceArtifact",
        Scope(reads=["workspace"]), SideEffect.NONE, "check_environment", "sandbox",
    ),
)

__all__ = ["EnvironmentCheckArgs", "SPECS"]
