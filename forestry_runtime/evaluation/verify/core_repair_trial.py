"""Verify one collected core.repair or core.changed_input evidence package.

The two contracts share a fixture generator, a scoring recipe and a set of checks, so
they share this adapter. They stay separate cases because they measure different
things: ``core.repair`` asks whether a diagnosed failure was actually fixed, and
``core.changed_input`` asks whether the fixed programme was genuinely rerun after its
input changed. Averaging them into one case would hide which of the two failed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .base import save_verdict, status_for_terminal
from ..rules import SCORING_RULES_VERSION
from .core_files import load_trace
from .core_repair import (
    failure_was_observed, rerun_produced_new_result, source_was_not_modified,
    summary_matches,
)
from .text import claims_match_table


PROJECT_ROOT = Path(__file__).resolve().parents[2]

SPECS = {
    "core.repair": {
        "fixture": "evaluation/fixtures/core_repair",
        "gold": "evaluation/fixtures/gold/core_repair.json",
        "result_check": "repair",
        "result_label": "repair",
    },
    "core.changed_input": {
        "fixture": "evaluation/fixtures/core_changed_input",
        "gold": "evaluation/fixtures/gold/core_changed_input.json",
        "result_check": "new_result",
        "result_label": "new-result",
    },
}


def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, case_id: str,
    gold: Path | None = None, fixture: Path | None = None,
) -> dict[str, Any]:
    spec = SPECS[case_id]
    gold = gold or PROJECT_ROOT / spec["gold"]
    case_dir = fixture or PROJECT_ROOT / spec["fixture"]
    trace = load_trace(trial)
    if trace.get("configuration") != configuration or trace.get("repeat") != repeat:
        raise ValueError("Trial configuration or repeat does not match the requested slot")

    failure = failure_was_observed(
        trace=trace, gold=gold, report=trial / "failure-verifier.json"
    )
    result = summary_matches(
        trial=trial, case_dir=case_dir, gold=gold,
        report=trial / f"{spec['result_label']}-verifier.json",
        label=spec["result_label"],
    )
    events = json.loads((trial / "raw" / "events.json").read_text(encoding="utf-8"))
    answer_text = "".join(
        str(event.get("content") or "")
        for event in events if event.get("type") == "message"
    )
    delivery = claims_match_table(
        answer=answer_text, gold=gold, report=trial / "delivery-verifier.json"
    )
    if case_id == "core.repair":
        # Declared order: failure_observed, repair, delivery.
        verdicts = {
            "failure_observed": failure,
            "repair": result,
            "delivery": delivery,
        }
    else:
        # Declared order: fresh_job, new_result, lineage. The rerun check replaces
        # failure_observed because a second run is the case's real subject.
        verdicts = {
            "fresh_job": rerun_produced_new_result(
                trace=trace, gold=gold, report=trial / "fresh-job-verifier.json"
            ),
            "new_result": result,
            "lineage": source_was_not_modified(
                trace=trace, gold=gold, report=trial / "lineage-verifier.json"
            ),
        }
    checks = {
        name: save_verdict(verdict, trial).as_check()
        for name, verdict in verdicts.items()
    }
    terminal = {
        "expected": "completed", "actual": trace.get("terminal_state"),
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
        "trial_id": str(trace.get("run_id") or ""),
        "configuration": trace["configuration"],
        "status": status_for_terminal(trace.get("terminal_state")),
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
    parser.add_argument("--case", required=True, choices=sorted(SPECS))
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    trace = load_trace(args.trial)
    record = verify_trial(
        args.trial.resolve(), trace["configuration"], int(trace["repeat"]),
        case_id=args.case, gold=args.gold.resolve(),
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
