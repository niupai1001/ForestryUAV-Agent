"""Dependency installation capability declaration."""

from pydantic import Field, model_validator

from ...kernel.spec import Declaration, Scope, SideEffect, ToolSpec
from ..base import Args


class DependencyInstallArgs(Args):
    packages: list[str] = Field(default_factory=list, max_length=20)
    system_packages: list[str] = Field(default_factory=list, max_length=20)
    timeout_seconds: int = Field(default=1800, ge=30, le=3600)

    @model_validator(mode="after")
    def at_least_one_target(self):
        if not self.packages and not self.system_packages:
            raise ValueError(
                "name at least one Python package or one system package to install"
            )
        return self


SPECS = (
    ToolSpec(
        "dependency_install",
        (
            "Install dependencies in a separate networked Docker job. Two kinds are "
            "accepted: Python requirements in `packages`, and operating-system "
            "packages in `system_packages`. A wheel that needs a shared library the "
            "wheel cannot supply needs the system package: pip will report such an "
            "install as successful while the import still fails. Only the workspace "
            "dependency directory is mounted; source data is not mounted. Whatever "
            "the job adds to the image is committed, so later code jobs run from an "
            "image that contains it."
        ),
        DependencyInstallArgs, "EvidenceArtifact",
        Scope(reads=["workspace"], writes=["workspace"], network="registry_only"),
        SideEffect.DURABLE_JOB, "install_dependency", "sandbox",
        declaration=Declaration(
            id="env.installable",
            requires=(
                "Both the Python requirements and any operating-system package a "
                "wheel depends on must be installable. A system package is not "
                "reachable through `packages`, and a wheel's shared library cannot be "
                "supplied by pip."
            ),
            verify_with=(
                "environment_check(modules=[...])",
                "job_status(job_id=...)",
            ),
            if_unmet=(
                "Read which requirement failed before choosing another one: pip "
                "resolves a set as a whole, so one unbuildable name also leaves the "
                "others uninstalled.",
                "If a module is installed but fails to import, name the shared library "
                "in `system_packages` rather than requesting the module again.",
            ),
        ),
    ),
)

__all__ = ["DependencyInstallArgs", "SPECS"]
