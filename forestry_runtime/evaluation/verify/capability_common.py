"""Shared plumbing for the capability task set.

The capability cases ask only for a goal, the data and the delivery requirements.
They deliberately do not name the tool to call or the steps to take -- that is the
capability under test -- so the checks here judge the delivered result, the
execution evidence and the stated judgement, never a prescribed tool sequence.

Two things live here so every capability trial agrees on them:

* how one collected trial becomes a scorecard record (including the declared
  condition, so ``not_applicable`` checks are never confused with failures);
* how a delivered mosaic/raster is located when the fixture itself is a raster, so
  the uploaded input is never mistaken for the produced output.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .base import Verdict, save_verdict, status_for_terminal
from ..rules import SCORING_RULES_VERSION


def load_trace(trial: Path) -> dict[str, Any]:
    return json.loads((trial / "trace.json").read_text(encoding="utf-8"))


def load_events(trial: Path) -> list[dict[str, Any]]:
    """Recorded Runtime events, or an empty list when none were collected.

    A missing or unreadable event file is *absence of evidence*, not an error: a
    verifier that raises here would abort the whole record instead of reporting the
    check as unobserved.
    """
    path = trial / "raw" / "events.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def answer_text(trial: Path) -> str:
    return "".join(
        str(event.get("content") or "")
        for event in load_events(trial)
        if event.get("type") == "message"
    )


def claims_block(
    answer: str, *, required: tuple[str, ...] = ("counts",)
) -> dict | None:
    """The last fenced JSON object that carries the contract's required fields.

    Only fenced blocks are read. Prose that happens to contain braces is not a
    machine-readable claim, and treating it as one would let an answer be graded on
    a number it never committed to.
    """
    for candidate in reversed(
        re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.DOTALL)
    ):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict) and all(field in decoded for field in required):
            return decoded
    return None


def require_slot(trace: dict, configuration: dict, repeat: int) -> None:
    if trace.get("configuration") != configuration or trace.get("repeat") != repeat:
        raise ValueError("Trial configuration or repeat does not match the requested slot")


def submitted_job_ids(trace: dict[str, Any]) -> set[str]:
    """Job ids this Run actually submitted, from the recorded tool results."""
    from .core_files import recorded_job_outputs

    return {
        item["job_id"] for item in recorded_job_outputs(trace) if item.get("job_id")
    }


def _normalise(verdict: Any) -> Verdict:
    """Accept a Verdict or an already-rendered check dict."""
    if isinstance(verdict, Verdict):
        return verdict
    if isinstance(verdict, dict):
        return Verdict(
            verdict=verdict.get("verdict", "unknown"),
            verifier=str(verdict.get("verifier") or "unspecified"),
            evidence=list(verdict.get("evidence") or []),
            detail=str(verdict.get("detail") or ""),
        )
    raise TypeError(f"Unsupported verdict payload: {type(verdict).__name__}")


def capability_record(
    *,
    trial: Path,
    trace: dict[str, Any],
    case_id: str,
    condition: str,
    checks: dict[str, Any],
    expected_terminal: str = "completed",
) -> dict[str, Any]:
    """Assemble one scorecard record for a capability case."""
    terminal = {
        "expected": expected_terminal, "actual": trace.get("terminal_state"),
        "checkpoint": trace.get("checkpoint_state"),
    }
    (trial / "termination-verifier.json").write_text(
        json.dumps(terminal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "suite_version": "forestry-eval-0.1",
        "scoring_rules_version": SCORING_RULES_VERSION,
        "case_id": case_id,
        "track": "agent",
        "execution": "real_model",
        "repeat": trace["repeat"],
        "condition": condition,
        "trial_id": str(trace.get("run_id") or ""),
        "configuration": trace["configuration"],
        "status": status_for_terminal(terminal["actual"], terminal["expected"]),
        "status_evidence": {
            "verifier": "termination-v1",
            "evidence": ["termination-verifier.json", "trace.json"],
        },
        "checks": {
            name: save_verdict(_normalise(verdict), trial).as_check()
            for name, verdict in checks.items()
        },
    }


def not_applicable(check: str, reason: str) -> dict[str, Any]:
    """A check this declared condition genuinely does not exercise.

    Not a failure and not an unknown: the condition itself makes the check
    inapplicable, and the reason says why.
    """
    return {
        "verdict": "not_applicable",
        "verifier": f"{check}-not-applicable-v1",
        "evidence": [],
        "detail": reason,
    }


def missing_deliverable(check: str, reason: str) -> dict[str, Any]:
    """The condition required a deliverable and none was produced."""
    return {
        "verdict": "fail",
        "verifier": f"{check}-missing-deliverable-v1",
        "evidence": [],
        "detail": reason,
    }


__all__ = [
    "answer_text", "capability_record", "claims_block", "load_events", "load_trace",
    "missing_deliverable", "not_applicable", "require_slot", "submitted_job_ids",
]
