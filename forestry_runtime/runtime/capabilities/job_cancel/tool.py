"""Durable-job cancellation capability declaration."""

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class JobCancelArgs(Args):
    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    user_initiated: bool = Field(
        default=False,
        description=(
            "Set true only when the user explicitly asked to cancel this job. "
            "Cancelling a healthy installation to free an execution slot is refused."
        ),
    )


SPECS = (
    ToolSpec("job_cancel", "Cancel a managed Runtime code or dependency job. Cancelling a running dependency installation is refused unless the user asked for it, or the installation has already failed or timed out.", JobCancelArgs, "EvidenceArtifact", Scope(reads=["workspace"]), SideEffect.DURABLE_JOB, "cancel_job", "job"),
)

__all__ = ["JobCancelArgs", "SPECS"]
