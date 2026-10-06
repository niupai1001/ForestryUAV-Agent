from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str
    served_name: str
    context_limit: int = Field(default=32768, ge=2048)
    output_limit: int = Field(default=4096, ge=256)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    workspace_root: Path = Path("workspace")
    model_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_base_url: str = "http://127.0.0.1:11434"
    models: dict[str, ModelProfile]
    default_model: str = "small"
    search_base_url: str | None = None
    search_provider: Literal["bing_rss", "searxng"] = "bing_rss"
    roles_path: Path | None = None
    max_model_requests_per_turn: int = Field(default=30, ge=1, le=1000)
    max_output_retries: int = Field(default=3, ge=0, le=5)
    max_model_request_seconds: int = Field(default=300, ge=1, le=1800)
    max_model_requests_per_task: int = Field(default=1000, ge=1)
    max_actions_per_task: int = Field(default=1000, ge=1)
    max_assignments_per_task: int = Field(default=32, ge=1)
    max_unchanged_decisions: int = Field(default=3, ge=1)
    code_image: str = "python:3.12-slim"
    docker_host: str = "npipe:////./pipe/dockerDesktopLinuxEngine"
    max_code_seconds: int = Field(default=14400, ge=1)
    max_code_memory_bytes: int = Field(default=6442450944, ge=67108864)
    max_code_cpus: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def validate_local_models(self):
        if self.default_model not in self.models:
            raise ValueError("default_model must name a configured profile")
        for address in (self.model_base_url, self.ollama_base_url):
            parsed = urlparse(address)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Model endpoints must be local HTTP endpoints; cloud routing is disabled")
            if parsed.username or parsed.password:
                raise ValueError("Credentials in endpoint URLs are not accepted")
        for profile in self.models.values():
            if profile.output_limit >= profile.context_limit:
                raise ValueError("Model output reserve must be below its context limit")
        return self


def load_config(path: str | Path = "config/app.example.yaml") -> Config:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config = Config.model_validate(raw)
    if not config.workspace_root.is_absolute():
        config.workspace_root = (config_path.parent.parent / config.workspace_root).resolve()
    if config.roles_path and not config.roles_path.is_absolute():
        config.roles_path = (config_path.parent / config.roles_path).resolve()
    return config

