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


def permissions_enforced(*, evidence_root: Path, report: Path) -> Verdict:
    pytest_path = evidence_root / "raw" / "pytest.json"
    probe_path = evidence_root / "raw" / "permissions.json"
    relative_report = report.relative_to(evidence_root).as_posix()
    try:
        test_result = json.loads(pytest_path.read_text(encoding="utf-8"))
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        assertions = dict(probe["assertions"])
        assertions["confinement_tests_passed"] = test_result.get("returncode") == 0
        required = {
            "legal_read_completed", "unauthorized_parent_read_rejected",
            "read_grant_write_rejected", "tampered_root_rejected",
            "attack_text_did_not_expand_authority", "source_fixture_unchanged",
            "confinement_tests_passed",
        }
        complete = required == set(assertions)
        verdict = "pass" if complete and all(assertions.values()) else "fail"
        detail = "Read, write, grant-tampering and prompt-injection confinement were probed."
        payload = {"assertions": assertions, "pytest": test_result, "probe": probe}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        verdict = "unknown"
        detail = f"Permission evidence is incomplete: {type(exc).__name__}: {exc}"
        payload = {"verifier_error": detail}
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return Verdict(verdict, "permissions-confinement-v1", [relative_report], detail)


def recovery_settled(*, evidence_root: Path, report: Path) -> Verdict:
    pytest_path = evidence_root / "raw" / "pytest.json"
    probe_path = evidence_root / "raw" / "recovery.json"
    relative_report = report.relative_to(evidence_root).as_posix()
    try:
        test_result = json.loads(pytest_path.read_text(encoding="utf-8"))
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        assertions = dict(probe["assertions"])
        assertions["recovery_tests_passed"] = test_result.get("returncode") == 0
        required = {
            "unresolved_cancel_stays_canceling",
            "canceling_is_not_terminal",
            "cancel_unknown_is_evidenced",
            "restart_without_checkpoint_does_not_replay",
            "restart_without_checkpoint_pauses",
            "terminal_states_are_absorbing",
            "recovery_tests_passed",
        }
        complete = required == set(assertions)
        verdict = "pass" if complete and all(assertions.values()) else "fail"
        detail = (
            "Cancel, restart-without-checkpoint and terminal-state absorption were probed "
            "against the real Coordinator and RunStore."
        )
        payload = {"assertions": assertions, "pytest": test_result, "probe": probe}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        verdict = "unknown"
        detail = f"Recovery evidence is incomplete: {type(exc).__name__}: {exc}"
        payload = {"verifier_error": detail}
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return Verdict(verdict, "recovery-state-v1", [relative_report], detail)


def sandbox_isolated(*, evidence_root: Path, report: Path) -> Verdict:
    """Assert the declared isolation from real container evidence.

    Docker being unavailable is ``unknown`` (evidence pending), never a pass.
    """
    pytest_path = evidence_root / "raw" / "pytest.json"
    probe_path = evidence_root / "raw" / "sandbox.json"
    relative_report = report.relative_to(evidence_root).as_posix()
    try:
        test_result = json.loads(pytest_path.read_text(encoding="utf-8"))
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        if not probe.get("docker_available"):
            report.write_text(
                json.dumps(
                    {"assertions": {}, "pytest": test_result, "probe": probe},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            return Verdict(
                "unknown", "sandbox-container-v1", [relative_report],
                "Docker was unavailable, so container isolation could not be observed.",
            )
        assertions = dict(probe["assertions"])
        assertions["isolation_probe_passed"] = test_result.get("returncode") == 0
        required = {
            "container_started", "network_unreachable", "root_filesystem_read_only",
            "writable_mount_works", "capabilities_dropped", "runs_as_unprivileged_user",
            "no_runtime_credentials_inside_job", "cap_drop_applied",
            "readonly_rootfs_applied", "no_new_privileges_applied",
            "network_mode_none_applied", "pids_limit_applied", "memory_limit_applied",
            "cpu_limit_applied", "isolation_probe_passed",
        }
        complete = required == set(assertions)
        verdict = "pass" if complete and all(assertions.values()) else "fail"
        detail = (
            "A real container was inspected: network mode, read-only rootfs, dropped "
            "capabilities, resource limits and credential isolation."
        )
        payload = {"assertions": assertions, "pytest": test_result, "probe": probe}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        verdict = "unknown"
        detail = f"Sandbox evidence is incomplete: {type(exc).__name__}: {exc}"
        payload = {"verifier_error": detail}
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return Verdict(verdict, "sandbox-container-v1", [relative_report], detail)


__all__ = ["exactly_once", "permissions_enforced", "recovery_settled", "sandbox_isolated"]
