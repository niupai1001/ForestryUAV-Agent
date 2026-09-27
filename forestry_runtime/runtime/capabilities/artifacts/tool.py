"""Artifact and stored-result capability declarations."""

from typing import Literal

from pydantic import Field

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ..base import Args


class ToolResultReadArgs(Args):
    result_id: str = Field(pattern=r"^result_[0-9a-f]{32}$")
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=12000, ge=1, le=12000)


class ArtifactInspectArgs(Args):
    scope: Literal["auto", "workspace", "asset", "source"] = Field(
        default="auto",
        description=(
            "Which root the input belongs to. Leave it 'auto' and pass the "
            "exact_reference returned by the tool that produced the file."
        ),
    )
    path: str = Field(default="", max_length=1000)
    asset_id: str | None = None
    source_id: str | None = None


class ArtifactPreviewArgs(ArtifactInspectArgs):
    max_size: int = Field(default=1024, ge=128, le=2048)


SPECS = (
    ToolSpec("tool_result_read", "Read a page from a complete tool result when an earlier Observation returned result_id and truncated=true.", ToolResultReadArgs, "EvidenceArtifact", Scope(reads=["workspace"]), SideEffect.NONE, "read_tool_result", "text"),
    ToolSpec("artifacts_inspect", "Inspect a managed workspace artifact, an attachment, or one file under an authorized source directory by reference. Returns measured metadata and a small text/image header, plus exact_reference for the next tool; large file content stays outside model context.", ArtifactInspectArgs, "EvidenceArtifact", Scope(reads=["workspace", "asset", "source"]), SideEffect.NONE, "inspect_artifact", "provenance"),
    ToolSpec("artifacts_preview", "Create a bounded PNG preview for a readable image artifact, or return a bounded text preview. Accepts the same reference as artifacts_inspect. Large source data remains outside model context.", ArtifactPreviewArgs, "ImageArtifact", Scope(reads=["workspace", "asset", "source"], writes=["workspace"]), SideEffect.FILE_WRITE, "preview_artifact", "image"),
)


__all__ = [
    "ArtifactInspectArgs", "ArtifactPreviewArgs", "ToolResultReadArgs", "SPECS",
]
