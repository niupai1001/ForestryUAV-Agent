from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

Capability = Literal["fs.inputs", "fs.list", "fs.read", "fs.write_workspace", "artifact.inspect",
    "artifact.read", "action.inspect", "document.read", "knowledge.index", "knowledge.search",
    "memory.read", "environment.inspect", "environment.prepare", "code.execute", "job.observe", "role.delegate",
    "web.search", "web.read", "task.plan", "environment.acquire"]
CAPABILITY_NAMES = {"list_inputs": "fs.inputs", "list_files": "fs.list", "read_file": "fs.read",
    "write_file": "fs.write_workspace", "inspect_artifact": "artifact.inspect", "read_artifact": "artifact.read",
    "inspect_action": "action.inspect", "read_document": "document.read", "index_document": "knowledge.index",
    "search_knowledge": "knowledge.search", "read_user_memory": "memory.read", "inspect_environment": "environment.inspect",
    "prepare_environment": "environment.prepare", "code_execute": "code.execute", "wait_job": "job.observe", "delegate_task": "role.delegate",
    "search_web": "web.search", "read_web_page": "web.read", "write_plan": "task.plan", "read_plan": "task.plan",
    "acquire_dependencies": "environment.acquire", "resolve_dependencies": "environment.acquire"}
CAPABILITY_NAMES["process_lidar_terrain"] = "code.execute"
CAPABILITY_NAMES["process_lidar_canopy"] = "code.execute"


class Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskConstraints(Boundary):
    # Omission keeps the existing {} payload and request digest for legacy retries.
    # None inherits configured ceilings; [] denies all registered action capabilities.
    allowed_capabilities: list[Capability] | None = Field(default=None, max_length=20)

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if value.get("allowed_capabilities") is None:
            value.pop("allowed_capabilities", None)
        return value

    @field_validator("allowed_capabilities")
    @classmethod
    def unique_capabilities(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("任务允许能力不能重复")
        return value


class TaskCreate(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=32000)
    input_refs: list[str] = Field(default_factory=list, max_length=100)
    requested_metrics: list[str] = Field(default_factory=list)
    requested_method_id: str | None = None
    model_profile: str | None = None
    harness_mode: Literal["H0", "H1", "H2"] = "H2"
    context_classification: Literal["private", "public"] = "private"
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
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
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)


class Control(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_state_version: int = Field(ge=1)


class RegisterResource(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=4096)
    classification: Literal["public", "private"] = "private"
    read_only: Literal[True] = True


class RegisterResourceFile(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    relative_path: str = Field(min_length=1, max_length=4096)


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


class MemoryContent(Boundary):
    text: str = Field(min_length=1, max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    scope_task_ids: list[str] = Field(default_factory=list, max_length=100)
    source_note: str = Field(default="", max_length=2000)
    conditions: str = Field(default="", max_length=2000)
    limitations: str = Field(default="", max_length=2000)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("有效期必须包含时区")
        return value.astimezone(UTC) if value is not None else None

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value):
        if not value.strip():
            raise ValueError("记忆内容不能为空")
        return value


class MemorySave(MemoryContent):
    request_id: str = Field(min_length=1, max_length=200)


class MemoryUpdate(MemorySave):
    expected_version: int = Field(ge=1)


class MemoryForget(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class KnowledgeImport(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    resource_id: str = Field(min_length=1, max_length=200)
    relative_path: str = Field(default="", max_length=4096)
    page: int = Field(default=0, ge=0)
    claim_kind: Literal["expert_assertion", "measurement", "reference"] = "expert_assertion"
    source_label: str = Field(default="", max_length=1000)
    conditions: str = Field(default="", max_length=2000)
    limitations: str = Field(default="", max_length=2000)


class KnowledgeQuery(Boundary):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)


class ResourceRequest(Boundary):
    cpu_threads: int = Field(ge=1)
    ram_bytes: int = Field(ge=67108864)
    vram_bytes: Literal[0] = 0
    temporary_bytes: int = Field(default=0, ge=0)
    output_bytes: int = Field(ge=1024)


class ApprovalResolution(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_spec_revision: int = Field(ge=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["approve", "reject", "revoke"]

