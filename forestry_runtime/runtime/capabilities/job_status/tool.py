"""Durable-job observation capability declaration."""

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class JobStatusArgs(Args):
    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    offset: int = Field(default=0, ge=0)
    wait_seconds: int = Field(default=20, ge=0, le=30)


SPECS = (
    ToolSpec("job_status", "Read incremental output and current state for a Runtime code or dependency job. It may long-poll without another model call. Terminal jobs register available artifacts.", JobStatusArgs, "EvidenceArtifact", Scope(reads=["workspace"]), SideEffect.NONE, "observe_job", "job"),
)

__all__ = ["JobStatusArgs", "SPECS"]
