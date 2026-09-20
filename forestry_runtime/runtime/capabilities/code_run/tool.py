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


SPECS = (
    ToolSpec("code_run", "Run Python or POSIX shell code written by you in a durable Docker job. The workspace is writable, selected source grants are read-only, network is disabled, and the call returns a job_id immediately.", CodeRunArgs, "EvidenceArtifact", Scope(reads=["workspace", "source"], writes=["workspace"]), SideEffect.DURABLE_JOB, "execute_code", "sandbox"),
)

__all__ = ["CodeRunArgs", "SPECS"]
