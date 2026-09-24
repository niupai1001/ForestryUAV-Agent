"""Durable-job observation capability declarations.

`job_status` answers "what is true right now" using a Runtime-owned incremental
cursor, `job_wait` hands the waiting itself to the Runtime, and `job_log` turns
the complete log into a readable workspace artifact.
"""

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class JobStatusArgs(Args):
    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    offset: int | None = Field(
        default=None, ge=0,
        description=(
            "Character offset in the job log. Omit it to continue from the Runtime's "
            "saved cursor and receive only new output. Set it explicitly to re-read "
            "earlier output; an explicit offset does not move the saved cursor."
        ),
    )
    wait_seconds: int = Field(default=0, ge=0, le=30)


class JobWaitArgs(Args):
    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    timeout_seconds: int = Field(
        default=300, ge=0, le=3600,
        description="How long the Runtime may wait inside this call before returning.",
    )
    offset: int | None = Field(default=None, ge=0)


class JobLogArgs(Args):
    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=200000, ge=1000, le=1000000)


SPECS = (
    ToolSpec(
        "job_status",
        "Read the current state of a Runtime code or dependency job plus any new output "
        "since the last read. Uses a Runtime-saved cursor, so repeated calls do not "
        "return the same old log. Terminal jobs register their available artifacts.",
        JobStatusArgs, "EvidenceArtifact", Scope(reads=["workspace"]),
        SideEffect.NONE, "observe_job", "job",
    ),
    ToolSpec(
        "job_wait",
        "Wait for a Runtime job to reach a terminal state. The Runtime does the waiting, "
        "not the model: this call returns as soon as the job settles, or after "
        "timeout_seconds with wait_timed_out=true. Prefer this over repeated job_status "
        "polls. A running job is not a finished result.",
        JobWaitArgs, "EvidenceArtifact", Scope(reads=["workspace"]),
        SideEffect.NONE, "observe_job", "job",
    ),
    ToolSpec(
        "job_log",
        "Read the complete log of a Runtime job. The full text is saved as a workspace "
        "file and returned in bounded form with the head and tail preserved, so a "
        "terminal error or a final summary at the end of a long log is never lost.",
        JobLogArgs, "TextArtifact", Scope(reads=["workspace"]),
        SideEffect.FILE_WRITE, "observe_job", "job",
    ),
)

__all__ = ["JobLogArgs", "JobStatusArgs", "JobWaitArgs", "SPECS"]
