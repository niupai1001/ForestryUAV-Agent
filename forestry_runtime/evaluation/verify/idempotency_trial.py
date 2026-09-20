"""Turn collected gate.idempotency evidence into one engineering record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid

from .gates import exactly_once


def verify_trial(trial: Path, configuration: dict) -> dict:
    verdict = exactly_once(
        evidence_root=trial, report=trial / "idempotency-verifier.json"
    )
    pytest_result = json.loads((trial / "raw" / "pytest.json").read_text(encoding="utf-8"))
    status = "evaluated" if pytest_result.get("returncode") == 0 else "crash"
    return {
        "suite_version": "forestry-eval-0.1",
        "case_id": "gate.idempotency",
        "track": "engineering",
        "execution": "engineering",
        "repeat": 1,
        "trial_id": "gate-idempotency-" + uuid.uuid4().hex,
        "configuration": configuration,
        "status": status,
        "status_evidence": {
            "verifier": "pytest-subprocess-v1",
            "evidence": ["raw/pytest.json", "raw/sqlite.json"],
        },
        "checks": {"exactly_once": verdict.as_check()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    record = verify_trial(
        args.trial.resolve(),
        json.loads(args.configuration.read_text(encoding="utf-8")),
    )
    args.record.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": record["status"],
        "verdict": record["checks"]["exactly_once"]["verdict"],
    }))
    return 0 if record["checks"]["exactly_once"]["verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
