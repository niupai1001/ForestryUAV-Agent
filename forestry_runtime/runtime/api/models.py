"""HTTP request and public response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SessionRequest(ApiModel):
    chat_id: str


class Sources(ApiModel):
    file_ids: list[str] = Field(default_factory=list, max_length=1000)


class ProjectRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectSelection(ApiModel):
    project_id: str | None = Field(default=None, max_length=80)


class MemoryRequest(ApiModel):
    content: str = Field(min_length=1, max_length=12000)
    confirmed: bool = False


class KnowledgeSourceRequest(ApiModel):
    kind: Literal["local", "url"]
    locator: str = Field(min_length=1, max_length=4000)


class GrantRequest(ApiModel):
    path: str = Field(min_length=3, max_length=2000)
    access: Literal["read", "write"] = "read"
    basis: str = Field(min_length=1, max_length=4000)


class Message(ApiModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=100000)
    input_id: str | None = Field(
        default=None, pattern=r"^input_[0-9A-Za-z_-]{8,128}$"
    )


class RunRequest(ApiModel):
    messages: list[Message] = Field(min_length=1, max_length=100)
    asset_ids: list[str] = Field(default_factory=list, max_length=1000)
    use_tools: bool = True


class RunMessage(ApiModel):
    content: str = Field(min_length=1, max_length=100000)
    asset_ids: list[str] = Field(default_factory=list, max_length=1000)
    input_id: str | None = Field(
        default=None, pattern=r"^input_[0-9A-Za-z_-]{8,128}$"
    )


class AssetResponse(ApiModel):
    id: str
    name: str
    size: int
    media_type: str | None = None
    parent_id: str | None = None
    artifact_kind: str | None = None
    metadata: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    sealed_at: str | float | None = None
    created_at: str | float | None = None


class AssetListResponse(ApiModel):
    assets: list[AssetResponse]


class RunResponse(ApiModel):
    id: str
    chat_id: str
    state: str
    model_calls: int = 0
    state_version: int = 0
    recovery_reason: str | None = None
    final: dict[str, Any] | None = None
    created_at: float
    updated_at: float


class RunEventsResponse(ApiModel):
    events: list[dict[str, Any]]
    next: int
    has_more: bool
    run: RunResponse


class RunTurnsResponse(ApiModel):
    turns: list[dict[str, Any]]


__all__ = [
    "AssetListResponse", "AssetResponse", "GrantRequest",
    "KnowledgeSourceRequest", "MemoryRequest", "Message", "ProjectRequest",
    "ProjectSelection", "RunEventsResponse", "RunMessage", "RunRequest",
    "RunResponse", "RunTurnsResponse", "SessionRequest", "Sources",
]
