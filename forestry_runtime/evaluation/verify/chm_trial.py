"""Verify one collected forestry.chm evidence package and emit a scorecard record.

The contract declares its input conditions up front, in ``Case.fixture_conditions``:
``normal`` (a DSM and DTM that share grid and vertical reference, so a CHM must be
built) and ``gap`` (an input that cannot support a CHM, so refusing to compute and
naming the missing evidence is the correct outcome).

The expected branch therefore comes from the **declared condition**, never from what
the model wrote. An earlier version read the answer's ``built`` flag to choose the
branch, which let a run that produced nothing move itself onto the easier branch by
writing ``{"built": false}`` -- and left the declared gap fixture unexercised by any
driver path. A delivered raster still forces the positive branch, because a built
surface must be graded as one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .base import Verdict, save_verdict, status_for_terminal
from ..rules import SCORING_RULES_VERSION
from .chm import (
    chm_claims_match_artifact, claims_block, compare_chm_grid_mask,
    compare_chm_pixels, raster_candidates,
)


def _not_applicable(check: str, reason: str) -> dict[str, Any]:
    """A check that this declared condition genuinely does not exercise."""
    return {
        "verdict": "not_applicable",
        "verifier": f"chm-{check}-not-applicable-v2",
        "evidence": [],
        "detail": reason,
    }


def _failed(check: str, reason: str) -> dict[str, Any]:
    return {
        "verdict": "fail",
        "verifier": f"chm-{check}-missing-surface-v2",
        "evidence": [],
        "detail": reason,
    }


def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, gold: Path | None = None,
    case_id: str = "forestry.chm", condition: str = "normal",
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

    # The declared condition decides the branch. A delivered raster overrides it,
    # because a surface that was produced must be judged as a produced surface.
    require_built = condition == "normal" or bool(delivered)

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
        f"The declared condition is '{condition}': the inputs cannot support a CHM, "
        "so no surface was expected and the pixel comparison does not apply."
    )
    missing_surface = (
        "The task required a CHM and the declared condition supports one, but no "
        "raster was delivered."
    )
    if pixels is not None:
        positive_check = save_verdict(pixels, trial).as_check()
    elif require_built:
        # The condition requires a surface and none was delivered: a failure, not an
        # abstention. Reporting `unknown` here let a run that exhausted its context
        # budget look like a correct refusal.
        positive_check = _failed("positive", missing_surface)
    else:
        positive_check = _not_applicable("positive", not_applicable)
    if grid is not None:
        claim_check = save_verdict(grid, trial).as_check()
    elif require_built:
        claim_check = _failed("claim", missing_surface)
    else:
        claim_check = _not_applicable("claim", not_applicable)
    checks = {
        "positive": positive_check,
        "negative": save_verdict(claims, trial).as_check(),
        "claim": claim_check,
    }
    return {
        "suite_version": "forestry-eval-0.1",
        "scoring_rules_version": SCORING_RULES_VERSION,
        "case_id": "forestry.chm",
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
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--condition", default="normal", choices=["normal", "gap"])
    args = parser.parse_args()
    trace = json.loads((args.trial / "trace.json").read_text(encoding="utf-8"))
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
