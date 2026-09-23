"""Offline aggregation of independently verified trials; never runs the Agent.

Records are evaluator-produced evidence, not model declarations. A present file
is necessary provenance, not a substitute for reviewing the verifier itself.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import mean


CONFIG_FIELDS = (
    "code_snapshot", "model_digest", "prompt_snapshot", "tools_snapshot",
    "dataset_version", "environment_snapshot", "evaluator_version",
    "sampling", "budgets",
)


def numeric_check(actual, expected, *, actual_unit: str, expected_unit: str,
                  abs_tol: float, rel_tol: float) -> dict:
    """Compare already normalized units; do not silently infer unit conversion."""
    values = (actual, expected, abs_tol, rel_tol)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("Numeric observations and tolerances must be finite numbers")
    if abs_tol < 0 or rel_tol < 0 or not actual_unit or not expected_unit:
        raise ValueError("Tolerances must be nonnegative and units explicit")
    error = abs(actual - expected)
    tolerance = max(abs_tol, rel_tol * abs(expected))
    return {
        "passed": actual_unit == expected_unit and error <= tolerance,
        "unit_correct": actual_unit == expected_unit,
        "absolute_error": error,
        "tolerance": tolerance,
    }


def _proof(check: dict, root: Path) -> bool:
    """Missing/invalid provenance stays unknown instead of granting a pass."""
    references = check.get("evidence")
    if not isinstance(references, list) or not references:
        return False
    for reference in references:
        if not isinstance(reference, str) or not reference.strip():
            return False
        target = (root / reference).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return False
    return bool(isinstance(check.get("verifier"), str) and check["verifier"].strip())


def _summary(verdicts: list[str]) -> dict:
    counts = Counter(verdicts)
    total = len(verdicts)
    if not total:
        raise ValueError("An empty denominator cannot be scored")
    lower = 100 * counts["pass"] / total
    upper = 100 * (counts["pass"] + counts["unknown"]) / total
    return {
        "planned": total, "passed": counts["pass"], "failed": counts["fail"],
        "unknown": counts["unknown"],
        "evidence_coverage": (counts["pass"] + counts["fail"]) / total,
        "score": lower if not counts["unknown"] else None,
        "missing_evidence_bounds": [lower, upper],
    }


def scorecard(suite: dict, records: list[dict], evidence_root: Path) -> dict:
    root = evidence_root.resolve()
    cases = {case["id"]: case for case in suite["cases"]}
    if not cases or len(cases) != len(suite["cases"]):
        raise ValueError("Cases must be nonempty with unique IDs")
    tracks = {"agent", "engineering", "ui"}
    for case in cases.values():
        if case["track"] not in tracks or not case.get("checks"):
            raise ValueError("Each case needs a known track and required checks")
        repeats = suite["repeats"][case["track"]]
        if type(repeats) is not int or repeats < 1:
            raise ValueError("Repeat counts must be positive integers")
    index = {}
    seen_run_ids = set()
    configurations = {}
    for record in records:
        case_id = record["case_id"]
        if case_id not in cases:
            raise ValueError(f"Unknown case: {case_id}")
        case = cases[case_id]
        if record.get("suite_version") != suite["version"]:
            raise ValueError("Suite version mismatch")
        if record.get("track") != case["track"]:
            raise ValueError("Evidence track mismatch")
        if case["track"] == "agent" and record.get("execution") != "real_model":
            raise ValueError("Agent scores require actual model runs")
        if case["track"] == "ui" and record.get("execution") != "browser":
            raise ValueError("UI scores require browser observations")
        if case["track"] == "engineering" and record.get("execution") != "engineering":
            raise ValueError("Engineering evidence must be labeled explicitly")
        repeat = record.get("repeat")
        if type(repeat) is not int or not 1 <= repeat <= suite["repeats"][case["track"]]:
            raise ValueError("Repeat slot outside the preregistered denominator")
        key = (case_id, repeat)
        run_id = record.get("trial_id")
        # Reported separately because they need different fixes, and because an
        # empty id used to surface as "Duplicate trial ID" on a run whose real
        # problem was an unreachable Runtime producing traces with no run_id.
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError(
                f"Record for {case_id} repeat {repeat} has no trial_id.\n"
                f"  That happens when collection produced no Runtime run.\n"
                f"  Re-collect the slot with: python -m evaluation.run_baseline "
                f"--cases {case_id} --force"
            )
        if run_id in seen_run_ids or key in index:
            raise ValueError(
                f"Two records claim {case_id} repeat {repeat} (trial_id {run_id!r})."
            )
        seen_run_ids.add(run_id)
        config = record.get("configuration", {})
        if any(not config.get(field) for field in CONFIG_FIELDS):
            raise ValueError("Configuration lacks frozen snapshots, sampling or budgets")
        signature = json.dumps(config, sort_keys=True, allow_nan=False)
        track = case["track"]
        if track in configurations and configurations[track] != signature:
            raise ValueError("Mixed configurations require separate scorecards")
        configurations[track] = signature
        if record.get("status") not in {"evaluated", "timeout", "crash", "infra_error", "verifier_error"}:
            raise ValueError("Unknown trial status")
        checks = record.get("checks", {})
        if set(checks) - set(case["checks"]):
            raise ValueError("Unexpected checks: cannot silently change the contract")
        for check in checks.values():
            if check.get("verdict") not in {"pass", "fail", "unknown"}:
                raise ValueError("Check verdict must be pass, fail or unknown")
        index[key] = record

    case_reports = []
    missing = []
    for case_id, case in cases.items():
        trials = []
        for repeat in range(1, suite["repeats"][case["track"]] + 1):
            record = index.get((case_id, repeat))
            verdict = "unknown"
            reasons = []
            if record is None:
                reasons.append("not_run")
            else:
                checks = record.get("checks", {})
                verified = {}
                for name in case["checks"]:
                    check = checks.get(name, {})
                    verified[name] = check.get("verdict", "unknown") if _proof(check, root) else "unknown"
                    if verified[name] != "pass":
                        reasons.append(f"{name}:{verified[name]}")
                status = record["status"]
                if "fail" in verified.values():
                    verdict = "fail"
                elif status in {"timeout", "crash"} and _proof(record.get("status_evidence", {}), root):
                    verdict = "fail"
                    reasons.append(status)
                elif (status == "evaluated"
                      and _proof(record.get("status_evidence", {}), root)
                      and all(value == "pass" for value in verified.values())):
                    verdict = "pass"
                else:
                    reasons.append(status)
            trials.append({"repeat": repeat, "verdict": verdict, "reasons": reasons})
            if verdict == "unknown":
                missing.append({"case_id": case_id, "repeat": repeat, "reasons": reasons})
        case_reports.append({
            "id": case_id, "track": case["track"], "group": case["group"],
            "gate": bool(case.get("gate")), "trials": trials,
            **_summary([trial["verdict"] for trial in trials]),
        })

    group_reports = {}
    for track in sorted(tracks):
        for group in sorted({case["group"] for case in case_reports if case["track"] == track}):
            items = [case for case in case_reports if case["track"] == track and case["group"] == group]
            # Every case in a track has the same preregistered repeat count.
            report = _summary([trial["verdict"] for item in items for trial in item["trials"]])
            report["cases"] = len(items)
            report["all_repeats_success_rate"] = (
                sum(item["score"] == 100 for item in items) / len(items)
                if not report["unknown"] else None
            )
            group_reports[f"{track}.{group}"] = report
    agent = [group_reports.get(f"agent.{name}") for name in suite["agent_groups"]]
    if not agent or any(item is None for item in agent):
        raise ValueError("All declared agent groups need cases")
    if {case["group"] for case in case_reports if case["track"] == "agent"} != set(suite["agent_groups"]):
        raise ValueError("Agent group declarations must exactly cover the cases")
    gate_items = [item for item in case_reports if item["gate"]]
    gate_state = "unknown"
    if any(item["failed"] for item in gate_items):
        gate_state = "fail"
    elif gate_items and all(item["score"] == 100 for item in gate_items):
        gate_state = "pass"
    complete = all(item["unknown"] == 0 for item in case_reports)
    return {
        "suite_version": suite["version"],
        "qualification": "blocked" if gate_state == "fail" else (
            "measured_unqualified" if complete and gate_state == "pass" else "incomplete"
        ),
        "gates": gate_state,
        "agent_macro_score": mean(item["score"] for item in agent)
        if all(item["score"] is not None for item in agent) else None,
        "agent_missing_evidence_bounds": [
            mean(item["missing_evidence_bounds"][bound] for item in agent)
            for bound in (0, 1)
        ],
        "worst_agent_group_score": min(item["score"] for item in agent)
        if all(item["score"] is not None for item in agent) else None,
        "confidence_interval": None,
        "confidence_note": "Pilot reports task-level results; no population reliability claim.",
        "groups": group_reports, "cases": case_reports, "missing_evidence": missing,
        "configurations": {key: json.loads(value) for key, value in configurations.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path(__file__).with_name("suite.json"))
    parser.add_argument("--records", type=Path)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    records = json.loads(args.records.read_text(encoding="utf-8")) if args.records else []
    root = args.records.parent if args.records else Path.cwd()
    result = scorecard(suite, records, root)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return {"blocked": 1, "incomplete": 2, "measured_unqualified": 0}[result["qualification"]]


if __name__ == "__main__":
    raise SystemExit(main())
