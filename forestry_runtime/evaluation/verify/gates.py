"""Independent verifiers for engineering gate evidence."""

from __future__ import annotations

import json
from pathlib import Path

from .base import Verdict


def exactly_once(*, evidence_root: Path, report: Path) -> Verdict:
    pytest_path = evidence_root / "raw" / "pytest.json"
    sqlite_path = evidence_root / "raw" / "sqlite.json"
    relative_report = report.relative_to(evidence_root).as_posix()
    try:
        test_result = json.loads(pytest_path.read_text(encoding="utf-8"))
        snapshot = json.loads(sqlite_path.read_text(encoding="utf-8"))
        violations = snapshot["violations"]
        probe = snapshot["probe"]
        assertions = {
            "fault_tests_passed": test_result.get("returncode") == 0,
            "all_links_resolve": all(value == 0 for value in violations.values()),
            "one_input": probe.get("input_rows") == 1,
            "one_turn": probe.get("turn_rows") == 1,
            "one_action": probe.get("action_rows") == 1,
            "one_attempt": probe.get("attempt_rows") == 1,
            "one_job": probe.get("job_rows") == 1,
            "failed_reservation_rolled_back": probe.get("rolled_back_actions") == 0,
        }
        verdict = "pass" if all(assertions.values()) else "fail"
        detail = "Fault-injection tests and persisted relationship counts were checked."
        payload = {"assertions": assertions, "pytest": test_result, "sqlite": snapshot}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        verdict = "unknown"
        detail = f"Engineering evidence is incomplete: {type(exc).__name__}: {exc}"
        payload = {"verifier_error": detail}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return Verdict(verdict, "idempotency-sqlite-v1", [relative_report], detail)


__all__ = ["exactly_once"]
