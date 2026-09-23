"""Dependency installation capability declaration."""

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class DependencyInstallArgs(Args):
    packages: list[str] = Field(min_length=1, max_length=20)
    timeout_seconds: int = Field(default=1800, ge=30, le=3600)


SPECS = (
    ToolSpec("dependency_install", "Install task Python dependencies in a separate networked Docker job. Only the workspace dependency directory is mounted; source data is not mounted.", DependencyInstallArgs, "EvidenceArtifact", Scope(reads=["workspace"], writes=["workspace"], network="registry_only"), SideEffect.DURABLE_JOB, "install_dependency", "sandbox"),
)

__all__ = ["DependencyInstallArgs", "SPECS"]
