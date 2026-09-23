"""Verify one collected core.csv evidence package and emit a scorecard record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .base import Verdict, save_verdict
from .csv import compare_by_business_key
from .provenance import artifact_links_to_action
from .text import claims_match_table



def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, gold: Path | None = None,
) -> dict[str, Any]:
    gold = gold or Path(__file__).resolve().parents[1] / "fixtures" / "gold" / "core_csv.json"
    trace = json.loads((trial / "trace.json").read_text(encoding="utf-8"))
    if trace.get("configuration") != configuration or trace.get("repeat") != repeat:
        raise ValueError("Trial configuration or repeat does not match the requested slot")
    events = json.loads((trial / "raw" / "events.json").read_text(encoding="utf-8"))
    outputs = list((trial / "artifacts").glob("asset_*-plot_summary.csv"))
    if len(outputs) == 1:
        table = compare_by_business_key(
            outputs[0], gold, report=trial / "table-verifier.json"
        )
    else:
        (trial / "table-verifier.json").write_text(json.dumps({
            "error": "Expected exactly one downloaded plot_summary.csv",
            "candidates": [path.name for path in outputs],
        }, indent=2), encoding="utf-8")
        table = Verdict(
            "fail" if outputs else "unknown", "csv-keys-values-v1",
            ["table-verifier.json"], f"Found {len(outputs)} matching output files.",
        )
    artifact = artifact_links_to_action(
        trace=trace, artifacts=trial / "artifacts",
        report=trial / "artifact-verifier.json", expected_name="plot_summary.csv",
    )
    answer_text = "".join(
        str(event.get("content") or "")
        for event in events if event.get("type") == "message"
    )
    answer = claims_match_table(
        answer=answer_text, gold=gold, report=trial / "answer-verifier.json"
    )
    terminal = {
        "expected": "completed", "actual": trace.get("terminal_state"),
        "checkpoint": trace.get("checkpoint_state"),
    }
    (trial / "termination-verifier.json").write_text(
        json.dumps(terminal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    checks = {
        "table": save_verdict(table, trial).as_check(),
        "artifact": save_verdict(artifact, trial).as_check(),
        "answer": save_verdict(answer, trial).as_check(),
    }
    completed = terminal["actual"] == terminal["expected"]
    run_id = str(trace.get("run_id") or "")
    return {
        "suite_version": "forestry-eval-0.1",
        "case_id": "core.csv",
        "track": "agent",
        "execution": "real_model",
        "repeat": trace["repeat"],
        "trial_id": run_id,
        "configuration": trace["configuration"],
        "status": "evaluated" if completed else "infra_error",
        "status_evidence": {
            "verifier": "termination-v1",
            "evidence": ["termination-verifier.json", "trace.json"],
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    trace = json.loads((args.trial / "trace.json").read_text(encoding="utf-8"))
    record = verify_trial(
        args.trial.resolve(), trace["configuration"], int(trace["repeat"]),
        gold=args.gold.resolve(),
    )
    args.record.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "status": record["status"],
        "checks": {key: value["verdict"] for key, value in record["checks"].items()},
    }, ensure_ascii=False))
    return 0 if record["status"] == "evaluated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
