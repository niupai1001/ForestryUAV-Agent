"""Normalize raw Runtime events without changing or inventing observations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


FAILURE_TAXONOMY = {
    ("arguments", "invalid_arguments"): "tool_protocol",
    ("preconditions", "invalid_arguments"): "tool_protocol",
    ("preconditions", "source_path_not_found"): "path_grounding",
    ("preconditions", "known_invalid_source_path"): "path_grounding",
    ("agent_control", "duplicate_failed_call"): "premature_stop",
    ("recovery", "action_outcome_unsettled"): "input_checkpoint",
    ("preconditions", "not_ready"): "data_semantics",
}


def failure_category(failure: dict[str, Any] | None) -> str | None:
    if not failure:
        return None
    stage = str(failure.get("stage") or "")
    code = str(failure.get("code") or "")
    return FAILURE_TAXONOMY.get(
        (stage, code),
        "algorithm_numeric" if stage == "execution" else "unknown",
    )


def _artifact_ids(result: Any) -> list[str]:
    found: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            candidate = value.get("id")
            if isinstance(candidate, str) and candidate.startswith("asset_"):
                found.append(candidate)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return list(dict.fromkeys(found))


def normalize_trace(
    *, events: list[dict[str, Any]], run: dict[str, Any], case_id: str,
    repeat: int, configuration: dict[str, Any],
    turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the evaluator trace view while retaining raw identifiers."""
    actions: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    artifacts: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    safety_events: list[dict[str, Any]] = []
    token_usage: dict[str, Any] = {}

    for raw in events:
        event = deepcopy(raw)
        kind = event.get("type")
        if kind == "tool_start":
            action_id = str(event.get("action_id") or "")
            if not action_id:
                continue
            spec = event.get("spec") if isinstance(event.get("spec"), dict) else {}
            actions[action_id] = {
                "step": event.get("step") or event.get("number"),
                "seq": event.get("seq"),
                "turn_id": event.get("turn_id"),
                "action_id": action_id,
                "attempt_id": event.get("attempt_id"),
                "tool": event.get("name"),
                "equivalent_group": spec.get("equivalent"),
                "side_effect": spec.get("side_effect"),
                "permission": spec.get("scope"),
                "args_normalized": event.get("arguments"),
                "status": "running",
                "latency_ms": None,
                "artifact_ids": [],
                "failure": None,
                "failure_category": None,
            }
            order.append(action_id)
        elif kind == "tool_end":
            action_id = str(event.get("action_id") or "")
            action = actions.get(action_id)
            if action is None:
                continue
            result = event.get("result")
            failure = event.get("failure")
            if failure is None and isinstance(result, dict):
                failure = result.get("failure")
            outcome_ok = event.get("outcome_ok")
            if outcome_ok is None and isinstance(result, dict):
                outcome_ok = result.get("outcome_ok", result.get("ok"))
            action.update({
                "status": "success" if outcome_ok is not False else "failure",
                "latency_ms": (
                    event.get("duration_ms")
                    if event.get("duration_ms") is not None
                    else (
                        float(event["duration_seconds"]) * 1000
                        if event.get("duration_seconds") is not None else None
                    )
                ),
                "artifact_ids": _artifact_ids(result),
                "failure": failure,
                "failure_category": failure_category(failure),
            })
        elif kind == "done":
            for artifact in event.get("artifacts") or []:
                if isinstance(artifact, dict):
                    artifacts.append(artifact)
        elif kind in {"permission_denied", "cancel_timeout", "security_event"}:
            safety_events.append(event)
        elif kind == "usage" and isinstance(event.get("usage"), dict):
            token_usage.update(event["usage"])
        elif kind == "knowledge_result":
            citations.extend(event.get("citations") or [])

    turn_rows = turns or []
    last_turn = turn_rows[-1] if turn_rows else {}
    return {
        "run_id": run.get("id"),
        "turn_id": last_turn.get("id"),
        "agent_run_id": last_turn.get("agent_run_id"),
        "case_id": case_id,
        "repeat": repeat,
        "configuration": deepcopy(configuration),
        "steps": [actions[action_id] for action_id in order],
        "artifacts": artifacts,
        "citations": citations,
        "token_usage": token_usage,
        "safety_events": safety_events,
        "terminal_state": run.get("state"),
        "checkpoint_state": last_turn.get("checkpoint_state"),
    }


__all__ = ["FAILURE_TAXONOMY", "failure_category", "normalize_trace"]
