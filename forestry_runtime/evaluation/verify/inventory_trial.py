"""Verify one collected forestry.inventory evidence package and emit a scorecard record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .base import status_for_terminal
from ..rules import SCORING_RULES_VERSION
from .core_files import load_trace
from .inventory import answer_matches_manifest, roles_match_manifest, stayed_read_only


CASE_ID = "forestry.inventory"


def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, gold: Path | None = None,
    case_id: str = CASE_ID,
) -> dict[str, Any]:
    gold = gold or (
        Path(__file__).resolve().parents[1] / "fixtures" / "gold" / "forestry_inventory.json"
    )
    trace = load_trace(trial)
    if trace.get("configuration") != configuration or trace.get("repeat") != repeat:
        raise ValueError("Trial configuration or repeat does not match the requested slot")
    roles = roles_match_manifest(
        trace=trace, gold=gold, report=trial / "roles-verifier.json"
    )
    answer = answer_matches_manifest(
        trial=trial, gold=gold, report=trial / "answer-verifier.json"
    )
    read_only = stayed_read_only(trace=trace, report=trial / "readonly-verifier.json")
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
        "case_id": CASE_ID,
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
        "checks": {
            "roles": roles.as_check(),
            "answer": answer.as_check(),
            "read_only": read_only.as_check(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    trace = load_trace(args.trial)
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
