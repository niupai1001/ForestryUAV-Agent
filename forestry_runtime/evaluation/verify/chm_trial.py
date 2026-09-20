"""Verify one collected forestry.chm evidence package and emit a scorecard record.

The contract has two halves and one verifier serves both. The positive half
delivers a raster, so ``pixels`` and ``mask_units`` are decidable. The negative
half must *not* deliver one, which makes those two checks undecidable for that
trial -- they report ``unknown`` and stay in the denominator rather than being
silently dropped or falsely passed. ``claims`` decides which half applies by
reading the answer's ``built`` flag, so a trial that fabricates a CHM while the
inputs cannot support one is judged on the raster it should not have produced.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .base import Verdict, save_verdict
from .chm import (
    chm_claims_match_artifact, claims_block, compare_chm_grid_mask,
    compare_chm_pixels, raster_candidates,
)



def _deferred(check: str, reason: str) -> dict[str, Any]:
    return {
        "verdict": "unknown",
        "verifier": f"chm-{check}-not-applicable-v1",
        "evidence": [],
        "detail": reason,
    }


def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, gold: Path | None = None,
) -> dict[str, Any]:
    gold = gold or (
        Path(__file__).resolve().parents[1] / "fixtures" / "gold" / "forestry_chm.json"
    )
    trace = json.loads((trial / "trace.json").read_text(encoding="utf-8"))
    if trace.get("configuration") != configuration or trace.get("repeat") != repeat:
        raise ValueError("Trial configuration or repeat does not match the requested slot")
    events = json.loads((trial / "raw" / "events.json").read_text(encoding="utf-8"))
    artifacts = trial / "artifacts"
    answer_text = "".join(
        str(event.get("content") or "")
        for event in events if event.get("type") == "message"
    )

    gold_contract = json.loads(gold.read_text(encoding="utf-8"))
    reported = claims_block(answer_text)
    delivered, excluded_inputs = raster_candidates(artifacts, gold_contract)
    # The fixture decides the expected branch: a raster means the inputs were
    # meant to support a CHM, so "built" must be true.
    require_built = bool(delivered)
    if reported is not None and isinstance(reported.get("built"), bool):
        require_built = reported["built"] or bool(delivered)

    claims = chm_claims_match_artifact(
        answer=answer_text, artifacts=artifacts, gold=gold,
        require_built=require_built, report=trial / "claims-verifier.json",
    )
    if delivered:
        pixels = compare_chm_pixels(
            artifacts=artifacts, gold=gold, report=trial / "pixels-verifier.json"
        )
        grid = compare_chm_grid_mask(
            artifacts=artifacts, gold=gold, report=trial / "mask-units-verifier.json"
        )
    else:
        pixels = None
        grid = None

    terminal = {
        "expected": "completed", "actual": trace.get("terminal_state"),
        "checkpoint": trace.get("checkpoint_state"),
    }
    (trial / "termination-verifier.json").write_text(
        json.dumps(terminal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    not_applicable = (
        "The inputs cannot support a CHM, so no surface was expected; "
        "FRAMEWORK.md §5 allows correct abstention to satisfy the contract."
    )
    checks = {
        "positive": (
            save_verdict(pixels, trial).as_check() if pixels is not None
            else _deferred("positive", not_applicable)
        ),
        "negative": save_verdict(claims, trial).as_check(),
        "claim": (
            save_verdict(grid, trial).as_check() if grid is not None
            else _deferred("claim", not_applicable)
        ),
    }
    return {
        "suite_version": "forestry-eval-0.1",
        "case_id": "forestry.chm",
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
