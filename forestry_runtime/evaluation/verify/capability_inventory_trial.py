"""Verify one collected ``capability.inventory`` evidence package.

Both declared conditions share this adapter. The branch is decided by the
declared condition, not by the answer: a Run that reports nothing cannot move
itself onto the easier branch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..rules import SCORING_RULES_VERSION
from .base import save_verdict, status_for_terminal
from .capability_inventory import inventory_counts_match, suitability_matches
from .core_files import load_trace


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, gold: Path | None = None,
    case_id: str = "capability.inventory", condition: str = "normal",
) -> dict[str, Any]:
    gold = gold or (
        PROJECT_ROOT / "evaluation" / "fixtures" / "gold"
        / f"capability_inventory_{condition}.json"
    )
    trace = load_trace(trial)
    if trace.get("configuration") != configuration or trace.get("repeat") != repeat:
        raise ValueError("Trial configuration or repeat does not match the requested slot")
    counts = inventory_counts_match(
        trial=trial, gold=gold, report=trial / "counts-verifier.json"
    )
    suitability = suitability_matches(
        trial=trial, gold=gold, report=trial / "suitability-verifier.json"
    )
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
        "condition": condition,
        "trial_id": str(trace.get("run_id") or ""),
        "configuration": trace["configuration"],
        "status": status_for_terminal(terminal["actual"], terminal["expected"]),
        "status_evidence": {
            "verifier": "termination-v1",
            "evidence": ["termination-verifier.json", "trace.json"],
        },
        "checks": {
            "counts": save_verdict(counts, trial).as_check(),
            "suitability": save_verdict(suitability, trial).as_check(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--condition", default="normal", choices=["normal", "gap"])
    args = parser.parse_args()
    trace = load_trace(args.trial)
    record = verify_trial(
        args.trial.resolve(), trace["configuration"], int(trace["repeat"]),
        gold=args.gold.resolve(), condition=args.condition,
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
