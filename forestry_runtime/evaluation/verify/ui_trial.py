"""Convert Playwright UI contract results into scorecard-ready browser records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CASES = {
    "ui.send": {
        "title": "ui.send paints pending input before acknowledgement and restores failed input",
        "checks": ("visible", "stable"),
    },
    "ui.reconnect": {
        "title": "ui.reconnect paginates, deduplicates and restores the terminal state",
        "checks": ("events", "terminal"),
    },
}


def _specs(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        spec["title"]: spec
        for suite in report.get("suites", [])
        for spec in suite.get("specs", [])
        if isinstance(spec, dict) and isinstance(spec.get("title"), str)
    }


def verify_trial(trial: Path, configuration: dict, repeat: int) -> list[dict[str, Any]]:
    report = json.loads((trial / "playwright-report.json").read_text(encoding="utf-8"))
    specs = _specs(report)
    records = []
    for case_id, contract in CASES.items():
        spec = specs.get(contract["title"])
        if spec is None:
            status, verdict, detail, trial_id = (
                "verifier_error", "unknown", "Playwright report lacks the preregistered test.",
                f"missing-{case_id}-{repeat}",
            )
        else:
            results = [
                result
                for test in spec.get("tests", [])
                for result in test.get("results", [])
            ]
            passed = bool(spec.get("ok")) and bool(results) and all(
                result.get("status") == "passed" for result in results
            )
            status = "evaluated"
            verdict = "pass" if passed else "fail"
            detail = "Real Chromium executed the case's observable DOM and network assertions."
            trial_id = str(spec.get("id") or f"{case_id}-{repeat}") + f"-r{repeat}"
        check = {
            "verdict": verdict,
            "verifier": "playwright-contract-v1",
            "evidence": ["playwright-report.json", "summary.json"],
            "detail": detail,
        }
        records.append({
            "suite_version": "forestry-eval-0.1",
            "case_id": case_id,
            "track": "ui",
            "execution": "browser",
            "repeat": repeat,
            "trial_id": trial_id,
            "configuration": configuration,
            "status": status,
            "status_evidence": {
                "verifier": "playwright-run-v1",
                "evidence": ["playwright-report.json", "summary.json"],
            },
            "checks": {name: dict(check) for name in contract["checks"]},
        })
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = verify_trial(
        args.trial.resolve(),
        json.loads(args.configuration.read_text(encoding="utf-8")),
        args.repeat,
    )
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({record["case_id"]: {
        name: check["verdict"] for name, check in record["checks"].items()
    } for record in records}, ensure_ascii=False))
    return 0 if all(
        check["verdict"] == "pass"
        for record in records for check in record["checks"].values()
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
