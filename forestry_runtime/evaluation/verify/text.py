"""Conservative structured-answer checks for core.csv."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .base import Verdict


def claims_match_table(
    *, answer: Any, gold: Path, report: Path,
) -> Verdict:
    """Validate explicit structured claims; free-form answers require human review."""
    contract = json.loads(gold.read_text(encoding="utf-8"))
    expected = contract.get("answer_claims", {})
    observed = answer if isinstance(answer, dict) else None
    if isinstance(answer, str):
        candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.DOTALL)
        if not candidates:
            candidates = [answer.strip()]
        try:
            decoded = json.loads(candidates[-1])
            observed = decoded if isinstance(decoded, dict) else None
        except (json.JSONDecodeError, TypeError):
            observed = None
    checks = {
        key: observed is not None and observed.get(key) == value
        for key, value in expected.items()
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "expected_claims": expected, "observed_claims": observed, "checks": checks,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if observed is None:
        return Verdict(
            "unknown", "structured-claims-v1", [report.name],
            "No machine-readable claims block was found; blind human semantic review is required.",
        )
    passed = bool(checks) and all(checks.values())
    return Verdict(
        "pass" if passed else "fail", "structured-claims-v1", [report.name],
        "Structured claims were compared with the frozen table claims.",
    )


__all__ = ["claims_match_table"]
