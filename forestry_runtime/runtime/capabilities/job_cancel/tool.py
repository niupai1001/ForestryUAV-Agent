"""Durable-job cancellation capability declaration."""

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class JobCancelArgs(Args):
    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")


SPECS = (
    ToolSpec("job_cancel", "Cancel a managed Runtime code or dependency job.", JobCancelArgs, "EvidenceArtifact", Scope(reads=["workspace"]), SideEffect.DURABLE_JOB, "cancel_job", "job"),
)

__all__ = ["JobCancelArgs", "SPECS"]
