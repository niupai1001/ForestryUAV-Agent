"""Sandboxed code execution capability declaration."""

from typing import Literal

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class CodeRunArgs(Args):
    language: Literal["python", "shell"]
    code: str = Field(min_length=1, max_length=131072)
    source_ids: list[str] = Field(default_factory=list, max_length=8)
    timeout_seconds: int = Field(default=14400, ge=1, le=14400)
    required_packages: list[str] = Field(
        default_factory=list, max_length=20,
        description=(
            "Distribution names this code needs in the dependency directory. The Runtime "
            "refuses to start if their installation has not reached a verified terminal "
            "success, and reports blocked_by instead of launching a job that cannot import."
        ),
    )


SPECS = (
    ToolSpec(
        "code_run",
        (
            "Run Python or POSIX shell code written by you in a durable Docker job. The "
            "workspace is writable at /workspace, selected source grants are read-only "
            "under /sources/<source_id>, network is disabled, and the call returns a "
            "job_id immediately. A returned job_id means the job started, not that it "
            "finished: use job_wait to obtain its result. The job runs a fixed image that "
            "already holds the packages listed by environment_check; that tool also "
            "reports the image, the dependency directory and the capacity of the writable "
            "locations, which is where a capacity failure will name itself. Use /scratch "
            "for intermediates larger than the /tmp tmpfs."
        ),
        CodeRunArgs, "EvidenceArtifact",
        Scope(reads=["workspace", "source"], writes=["workspace"]),
        SideEffect.DURABLE_JOB, "execute_code", "sandbox",
    ),
)

__all__ = ["CodeRunArgs", "SPECS"]
