"""Verify one collected forestry.ndvi evidence package and emit a scorecard record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .base import Verdict, save_verdict
from .ndvi import (
    compare_ndvi_grid_mask, compare_ndvi_pixels, reported_facts_match_artifact,
)



def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, gold: Path | None = None,
) -> dict[str, Any]:
    gold = gold or (
        Path(__file__).resolve().parents[1] / "fixtures" / "gold" / "forestry_ndvi.json"
    )
    trace = json.loads((trial / "trace.json").read_text(encoding="utf-8"))
    if trace.get("configuration") != configuration or trace.get("repeat") != repeat:
        raise ValueError("Trial configuration or repeat does not match the requested slot")
    events = json.loads((trial / "raw" / "events.json").read_text(encoding="utf-8"))
    artifacts = trial / "artifacts"

    pixels = compare_ndvi_pixels(
        artifacts=artifacts, gold=gold, report=trial / "pixels-verifier.json"
    )
    grid = compare_ndvi_grid_mask(
        artifacts=artifacts, gold=gold, report=trial / "grid-mask-verifier.json"
    )
    answer_text = "".join(
        str(event.get("content") or "")
        for event in events if event.get("type") == "message"
    )
    answer = reported_facts_match_artifact(
        answer=answer_text, artifacts=artifacts, gold=gold,
        report=trial / "answer-verifier.json",
    )
    terminal = {
        "expected": "completed", "actual": trace.get("terminal_state"),
        "checkpoint": trace.get("checkpoint_state"),
    }
    (trial / "termination-verifier.json").write_text(
        json.dumps(terminal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    checks = {
        "pixels": save_verdict(pixels, trial).as_check(),
        "grid_mask": save_verdict(grid, trial).as_check(),
        "answer": save_verdict(answer, trial).as_check(),
    }
    return {
        "suite_version": "forestry-eval-0.1",
        "case_id": "forestry.ndvi",
        "track": "agent",
        "execution": "real_model",
        "repeat": trace["repeat"],
        "trial_id": str(trace.get("run_id") or ""),
        "configuration": trace["configuration"],
        "status": "evaluated" if terminal["actual"] == terminal["expected"] else "infra_error",
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
