"""Filesystem capability declaration."""

from typing import Literal

from pydantic import Field, model_validator

from ...kernel.spec import Scope, SideEffect, ToolSpec
from ...workspace import is_host_path
from ..base import Args


class FsListArgs(Args):
    scope: Literal["workspace", "assets", "source"] = "workspace"
    path: str = Field(
        default=".", max_length=2000,
        description=(
            "Workspace-relative path, or the exact absolute Windows/UNC directory "
            "explicitly requested by the user. Absolute host paths are routed to "
            "the authorized source automatically; scope=source is not required."
        ),
    )
    source_id: str | None = Field(default=None, max_length=80)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=200)

    @model_validator(mode="after")
    def source_contract(self):
        if self.scope == "source" and not (self.source_id or is_host_path(self.path)):
            raise ValueError(
                "source scope requires source_id or an explicit Windows path "
                "from the latest user request"
            )
        return self


class FsReadArgs(Args):
    scope: Literal["workspace", "asset", "source"] = "workspace"
    path: str = Field(
        default="", max_length=2000,
        description=(
            "Exact path returned by a tool. For workspace files it is relative to "
            "the workspace root (for example 'result.txt'), never 'workspace/result.txt'."
        ),
    )
    asset_id: str | None = None
    source_id: str | None = None
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=200, ge=1, le=2000)
    max_chars: int = Field(default=32000, ge=1, le=262144)


class FsSearchArgs(Args):
    query: str = Field(min_length=1, max_length=1000)
    scope: Literal["workspace", "source"] = "workspace"
    path: str = Field(
        default=".", max_length=2000,
        description="Workspace-root-relative path; do not add a 'workspace/' prefix.",
    )
    source_id: str | None = None
    glob: str = Field(default="*", max_length=200)
    max_results: int = Field(default=50, ge=1, le=200)


class FsWriteArgs(Args):
    path: str = Field(
        min_length=1, max_length=1000,
        description=(
            "Destination relative to the workspace root, such as 'report.json'. "
            "Never prefix it with 'workspace/'."
        ),
    )
    content: str = Field(max_length=262144)
    overwrite: bool = False


class FsEditArgs(Args):
    scope: Literal["workspace", "source"] = "workspace"
    path: str = Field(
        min_length=1, max_length=1000,
        description=(
            "Exact workspace-relative path returned by a tool, without a "
            "'workspace/' prefix; or an authorized source path when scope=source."
        ),
    )
    source_id: str | None = None
    old: str = Field(min_length=1, max_length=131072)
    new: str = Field(max_length=131072)
    replace_all: bool = False
    expected_version: str | None = Field(
        default=None, max_length=100,
        description=(
            "Version returned by fs_read/fs_list. Required when editing a previously "
            "observed workspace file to detect concurrent changes."
        ),
    )


SPECS = (
    ToolSpec("fs_list", "List real files and directories with pagination. Pass an explicitly requested absolute Windows/UNC directory directly in path; the Runtime authorizes and routes it automatically. No file type is filtered by domain.", FsListArgs, "EvidenceArtifact", Scope(reads=["workspace", "source"], host_path_args=["path"]), SideEffect.NONE, "inspect_filesystem", "filesystem"),
    ToolSpec("fs_read", "Read a bounded UTF-8 text range from a workspace file, attachment, or authorized source. Reuse the exact path returned by tools; workspace paths are already root-relative. Binary/non-UTF-8 files return that fact instead of domain-specific inspection.", FsReadArgs, "TextArtifact", Scope(reads=["workspace", "asset", "source"], host_path_args=["path"]), SideEffect.NONE, "read_text", "text"),
    ToolSpec("fs_search", "Search text in workspace files or an authorized source directory. Returns bounded path, line, and excerpt matches.", FsSearchArgs, "EvidenceArtifact", Scope(reads=["workspace", "source"], host_path_args=["path"]), SideEffect.NONE, "search_text", "text"),
    ToolSpec("fs_write", "Create a UTF-8 file in the managed workspace. The path is root-relative, so use 'report.json', not 'workspace/report.json'. Parent directories may be created. Existing files require overwrite=true.", FsWriteArgs, "TextArtifact", Scope(reads=["workspace"], writes=["workspace"]), SideEffect.FILE_WRITE, "write_file", "text"),
    ToolSpec("fs_edit", "Apply an exact text replacement to a workspace file or one explicitly authorized host file. Source-directory grants remain read-only. Fails if old text is missing or ambiguous unless replace_all=true.", FsEditArgs, "TextArtifact", Scope(reads=["workspace", "source"], writes=["workspace", "source_file"], host_path_args=["path"]), SideEffect.FILE_WRITE, "edit_file", "text"),
)


__all__ = [
    "FsEditArgs", "FsListArgs", "FsReadArgs", "FsSearchArgs", "FsWriteArgs",
    "SPECS",
]
