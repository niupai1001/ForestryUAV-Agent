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
        (
            "Report the real execution environment of a code job: the image that job "
            "will run (including one an install has committed, which is not the "
            "configured base image), the installed package manifest of the dependency "
            "directory, whether the named modules are importable there, and the capacity "
            "of the writable locations. Runs a short bounded probe in that image and "
            "never occupies the execution slot. Capacity is measured inside the "
            "container, because a cap on a tmpfs or a quota is invisible from the host. "
            "Use this instead of inferring that dependencies are missing from an empty "
            "workspace."
        ),
        EnvironmentCheckArgs, "EvidenceArtifact",
        Scope(reads=["workspace"]), SideEffect.NONE, "check_environment", "sandbox",
    ),
)

__all__ = ["EnvironmentCheckArgs", "SPECS"]
