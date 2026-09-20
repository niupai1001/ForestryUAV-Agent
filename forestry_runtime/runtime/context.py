"""Compile bounded, inspectable context for each model request."""
from __future__ import annotations

import json
import os
from typing import Callable


class ContextCompiler:
    """Build request-local facts without owning model history."""

    def __init__(
        self,
        toolbox,
        *,
        run_facts: Callable[[], dict] | None = None,
        project_context: Callable[[], dict] | None = None,
    ):
        self.toolbox = toolbox
        self.run_facts = run_facts
        self.project_context = project_context

    def compile(self, query: str, tool_defs: list, history_messages: int) -> tuple[str, dict, int]:
        attachments = self.toolbox.attachment_context()
        grants = self.toolbox.workspaces.list_grants(
            self.toolbox.owner, self.toolbox.chat_id
        )
        facts = {
            "workspace": "managed and writable; inspect changing values with tools",
            "attachments": attachments[:30],
            "attachment_count": len(attachments),
            "source_grants": [
                {key: item.get(key) for key in ("id", "access", "host_path")}
                for item in grants[:20]
            ],
        }
        if self.run_facts:
            facts["run"] = self.run_facts()
        project = self.project_context() if self.project_context else {}
        if project:
            facts["project"] = project

        tool_names = [getattr(item, "name", "") for item in tool_defs]
        schema_chars = sum(
            len(json.dumps(
                getattr(item, "parameters_json_schema", {}),
                ensure_ascii=False,
            ))
            for item in tool_defs
        )
        instruction = "Runtime facts for this model request:\n" + json.dumps(
            facts, ensure_ascii=False, allow_nan=False
        )
        estimated_input_tokens = max(1, len(instruction) // 4)
        manifest = {
            "compiler": "request-v1",
            "history_messages": history_messages,
            "tool_count": len(tool_defs),
            "tool_names": tool_names,
            "tool_schema_chars": schema_chars,
            "context_chars": len(instruction),
            "estimated_context_tokens": estimated_input_tokens,
            "output_reserve_tokens": int(os.getenv("OLLAMA_OUTPUT_TOKENS", "8192")),
            "project_bound": bool(project.get("project_id")) if project else False,
            "retrieval_mode": None,
            "knowledge_source_count": len(project.get("knowledge_sources", [])) if project else 0,
        }
        return instruction, manifest, estimated_input_tokens
