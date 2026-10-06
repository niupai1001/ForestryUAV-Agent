from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreate(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=32000)
    input_refs: list[str] = Field(default_factory=list, max_length=100)
    requested_metrics: list[str] = Field(default_factory=list)
    requested_method_id: str | None = None
    model_profile: str | None = None
    harness_mode: Literal["H0", "H1", "H2"] = "H2"
    constraints: dict = Field(default_factory=dict)
    output_preferences: dict = Field(default_factory=lambda: {"report_language": "zh"})


class Message(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=32000)
    expected_spec_revision: int = Field(ge=1)
    correlation_id: str | None = None


class Amendment(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_spec_revision: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=32000)
    input_refs: list[str] = Field(default_factory=list)
    requested_method_id: str | None = None
    constraints: dict = Field(default_factory=dict)


class Control(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_state_version: int = Field(ge=1)


class RegisterResource(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=4096)
    classification: Literal["public", "private"] = "private"
    read_only: Literal[True] = True


class SettingsChange(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)
    default_model: str


class Acceptance(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_state_version: int = Field(ge=1)
    expected_spec_revision: int = Field(ge=1)
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["accept", "request_changes"]
    comment: str = Field(default="", max_length=8000)


class Failure(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


class MemorySave(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=10)


class MemoryForget(Boundary):
    request_id: str = Field(min_length=1, max_length=200)

