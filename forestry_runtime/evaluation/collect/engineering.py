"""Collect engineering evidence through subprocess tests and read-only SQLite queries."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any


IDEMPOTENCY_TESTS = [
    "tests/test_idempotency_gate.py::IdempotencyGateTests::test_exactly_once_probe",
    "tests/test_generic_runtime.py::GenericRuntimeTests::test_uncertain_submission_is_reconciled_by_action_id",
]

PERMISSIONS_TESTS = [
    "tests/test_permissions_gate.py::PermissionsGateTests::test_permission_boundaries_probe",
    "tests/test_host_bridge.py::HostBridgeTests::test_revoked_grant_is_rejected_even_with_a_valid_old_token",
    "tests/test_generic_runtime.py::GenericRuntimeTests::test_source_directory_is_read_only_and_write_grant_is_one_file",
]


def _scalar(conn: sqlite3.Connection, query: str) -> int:
    return int(conn.execute(query).fetchone()[0])


def inspect_idempotency(database: Path) -> dict[str, Any]:
    """Read linkage/count invariants without importing the implementation under test."""
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        counts = {
            table: _scalar(conn, f"SELECT COUNT(*) FROM {table}")
            for table in ("inputs", "turns", "actions", "attempts", "job_refs")
        }
        violations = {
            "duplicate_inputs": _scalar(conn, """
                SELECT COUNT(*) FROM (
                  SELECT owner,chat_id,id FROM inputs
                  GROUP BY owner,chat_id,id HAVING COUNT(*) > 1
                )
            """),
            "orphan_inputs": _scalar(conn, """
                SELECT COUNT(*) FROM inputs i LEFT JOIN runs r ON r.id=i.run_id
                WHERE r.id IS NULL
            """),
            "orphan_turns": _scalar(conn, """
                SELECT COUNT(*) FROM turns t LEFT JOIN runs r ON r.id=t.run_id
                WHERE r.id IS NULL
            """),
            "orphan_actions": _scalar(conn, """
                SELECT COUNT(*) FROM actions a
                LEFT JOIN runs r ON r.id=a.run_id
                LEFT JOIN turns t ON t.id=a.turn_id
                WHERE r.id IS NULL OR (a.turn_id IS NOT NULL AND t.id IS NULL)
            """),
            "orphan_attempts": _scalar(conn, """
                SELECT COUNT(*) FROM attempts p
                LEFT JOIN actions a ON a.id=p.action_id
                LEFT JOIN runs r ON r.id=p.run_id
                LEFT JOIN turns t ON t.id=p.turn_id
                WHERE a.id IS NULL OR r.id IS NULL
                   OR p.run_id != a.run_id
                   OR (p.turn_id IS NOT NULL AND t.id IS NULL)
            """),
            "actions_without_attempt": _scalar(conn, """
                SELECT COUNT(*) FROM actions a LEFT JOIN attempts p ON p.action_id=a.id
                WHERE p.id IS NULL
            """),
            "multiple_attempts": _scalar(conn, """
                SELECT COUNT(*) FROM (
                  SELECT action_id FROM attempts GROUP BY action_id HAVING COUNT(*) > 1
                )
            """),
            "orphan_jobs": _scalar(conn, """
                SELECT COUNT(*) FROM job_refs j
                LEFT JOIN runs r ON r.id=j.run_id
                LEFT JOIN actions a ON a.id=j.action_id
                LEFT JOIN turns t ON t.id=j.turn_id
                WHERE r.id IS NULL
                   OR (j.action_id IS NOT NULL AND (a.id IS NULL OR a.run_id != j.run_id))
                   OR (j.turn_id IS NOT NULL AND t.id IS NULL)
            """),
        }
        action_attempts = [
            {"action_id": row[0], "attempts": int(row[1])}
            for row in conn.execute("""
                SELECT action_id,COUNT(*) FROM attempts GROUP BY action_id ORDER BY action_id
            """).fetchall()
        ]
        probe = {
            "input_rows": _scalar(conn, "SELECT COUNT(*) FROM inputs WHERE id='input_gate_once'"),
            "turn_rows": _scalar(conn, """
                SELECT COUNT(*) FROM turns WHERE run_id=(
                  SELECT run_id FROM inputs WHERE id='input_gate_once' LIMIT 1
                )
            """),
            "action_rows": _scalar(conn, "SELECT COUNT(*) FROM actions WHERE id='action_gate_once'"),
            "attempt_rows": _scalar(conn, "SELECT COUNT(*) FROM attempts WHERE action_id='action_gate_once'"),
            "job_rows": _scalar(conn, "SELECT COUNT(*) FROM job_refs WHERE job_id='job_gate_once'"),
            "rolled_back_actions": _scalar(conn, "SELECT COUNT(*) FROM actions WHERE id='action_gate_rollback'"),
        }
    return {
        "database": database.name,
        "counts": counts,
        "violations": violations,
        "action_attempts": action_attempts,
        "probe": probe,
    }


def collect_idempotency(output: Path, *, project_root: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    raw = output / "raw"
    raw.mkdir()
    database = raw / "idempotency.sqlite3"
    environment = dict(os.environ)
    environment["EVALUATION_IDEMPOTENCY_DB"] = str(database.resolve())
    command = [sys.executable, "-m", "pytest", "-q", *IDEMPOTENCY_TESTS]
    completed = subprocess.run(
        command, cwd=project_root, env=environment,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, check=False,
    )
    pytest_evidence = {
        "command": command,
        "tests": IDEMPOTENCY_TESTS,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    (raw / "pytest.json").write_text(
        json.dumps(pytest_evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    snapshot = inspect_idempotency(database) if database.exists() else {
        "error": "The probe did not produce its SQLite database."
    }
    (raw / "sqlite.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"pytest": pytest_evidence, "snapshot": snapshot}


def collect_permissions(output: Path, *, project_root: Path) -> dict[str, Any]:
    """Run confinement probes and preserve their independently inspectable observations."""
    output.mkdir(parents=True, exist_ok=False)
    raw = output / "raw"
    raw.mkdir()
    report = raw / "permissions.json"
    environment = dict(os.environ)
    environment["EVALUATION_PERMISSIONS_REPORT"] = str(report.resolve())
    command = [sys.executable, "-m", "pytest", "-q", *PERMISSIONS_TESTS]
    completed = subprocess.run(
        command, cwd=project_root, env=environment,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, check=False,
    )
    pytest_evidence = {
        "command": command,
        "tests": PERMISSIONS_TESTS,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    (raw / "pytest.json").write_text(
        json.dumps(pytest_evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not report.exists():
        report.write_text(
            json.dumps({"error": "The permission probe did not produce a report."}, indent=2),
            encoding="utf-8",
        )
    return {
        "pytest": pytest_evidence,
        "report": json.loads(report.read_text(encoding="utf-8")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--gate", choices=("idempotency", "permissions"), default="idempotency"
    )
    args = parser.parse_args()
    collector = {
        "idempotency": collect_idempotency,
        "permissions": collect_permissions,
    }[args.gate]
    result = collector(args.output, project_root=args.project_root.resolve())
    print(json.dumps({
        "pytest_returncode": result["pytest"]["returncode"],
        "evidence_error": result.get("snapshot", result.get("report", {})).get("error"),
    }, ensure_ascii=False))
    evidence = result.get("snapshot", result.get("report", {}))
    return 0 if result["pytest"]["returncode"] == 0 and "error" not in evidence else 2


if __name__ == "__main__":
    raise SystemExit(main())
