"""Shared, dependency-free observation vocabulary for Runtime and evaluation."""

from __future__ import annotations

import re
from typing import Any


OUTCOMES = frozenset({"succeeded", "empty", "pending", "failed"})
REASONS = frozenset({
    "not_found", "ambiguous", "unauthorized", "inapplicable",
    "missing_env", "invalid_arguments", "resource_limit",
    "execution_error", "unknown",
})


def missing_shared_library(message: str) -> str | None:
    """Recognize an explicit loader error; other failures remain unknown."""
    match = re.search(
        r"([\w.-]+\.(?:so(?:\.\d+)*|dll)):\s*cannot open shared object file",
        message, re.IGNORECASE,
    )
    return match.group(1) if match else None

# Map only codes whose meaning is established by their producer. An arbitrary
# exception class is a code for debugging, not a diagnosis.
KNOWN_REASONS = {
    "invalid_arguments": "invalid_arguments",
    "source_path_not_found": "not_found",
    "path_not_found": "not_found",
    "known_invalid_source_path": "not_found",
    "requested_input_unavailable": "not_found",
    "resource_busy": "resource_limit",
    "recursive_scope_unconfirmed": "inapplicable",
}

LEGACY_CATEGORIES = {
    "invalid_arguments": "tool_protocol",
    "source_path_not_found": "path_grounding",
    "known_invalid_source_path": "path_grounding",
    "requested_input_unavailable": "path_grounding",
    "duplicate_failed_call": "premature_stop",
    "action_outcome_unsettled": "input_checkpoint",
    "not_ready": "data_semantics",
}


def normalize_failure(failure: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve producer evidence and make missing diagnoses visible as unknown."""
    result = dict(failure or {})
    result.setdefault("stage", "unknown")
    result.setdefault("code", "unknown")
    reason = result.get("reason") or KNOWN_REASONS.get(str(result["code"]), "unknown")
    result["reason"] = reason if reason in REASONS else "unknown"
    result.setdefault("operation_started", None)
    result.setdefault("side_effects", "unknown")
    result.setdefault("missing", [])
    result.setdefault("evidence_version", None)
    if result["reason"] == "not_found" and not result.get("checked_scope"):
        path = result.get("requested_path")
        if path:
            result["checked_scope"] = {
                "path": path,
                **({"source_id": result["source_id"]} if result.get("source_id") else {}),
            }
    return result


def normalize_result(output: dict[str, Any]) -> dict[str, Any]:
    """Add one outcome axis without changing existing ok/outcome_ok semantics."""
    result = dict(output)
    failed = result.get("outcome_ok", result.get("ok", True)) is False
    if failed:
        result["outcome"] = "failed"
        result["failure"] = normalize_failure(result.get("failure"))
    elif result.get("outcome") not in OUTCOMES:
        data = result.get("data")
        state = data.get("state") if isinstance(data, dict) else None
        result["outcome"] = (
            "pending" if state in {"queued", "running", "waiting"} else "succeeded"
        )
    if result["outcome"] == "empty":
        result.setdefault("control_verified", None)
    return result


def failure_category(failure: dict[str, Any] | None) -> str | None:
    """Compatibility label for existing traces; never guess algorithm failure."""
    if not failure:
        return None
    code = str(failure.get("code") or "")
    return LEGACY_CATEGORIES.get(code, normalize_failure(failure)["reason"])
