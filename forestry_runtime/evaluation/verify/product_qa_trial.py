"""Verify one collected forestry.product_qa evidence package and emit a record.

Unlike forestry.ndvi and forestry.chm this case has no output raster to compare:
the delivered "artifact" under test is the agent's own metadata claim, and the
evaluator's job is to check that claim against an independent read of the same
product. The three checks stay separate so a wrong number, an overreaching
boundary claim and an unsupported conclusion are distinguishable in the report.

The prompt does not mandate a tool. ``inspect_file``/``inspect_raster`` can answer
from an attachment while ``inspect_uav_products`` answers from an authorized host
directory, so this case runs through the shared attachment collection path rather
than requiring a host grant.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .base import Verdict, save_verdict
from .product_qa import (
    boundaries_respected, evidence_is_traceable, metadata_matches_artifact,
)


def verify_trial(
    trial: Path, configuration: dict, repeat: int, *, gold: Path | None = None,
) -> dict[str, Any]:
    gold = gold or (
        Path(__file__).resolve().parents[1] / "fixtures" / "gold"
        / "forestry_product_qa.json"
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

    metadata = metadata_matches_artifact(
        answer=answer_text, artifacts=artifacts, gold=gold,
        report=trial / "metadata-verifier.json",
    )
    boundaries = boundaries_respected(
        answer=answer_text, artifacts=artifacts, gold=gold,
        report=trial / "boundaries-verifier.json",
    )
    evidence = evidence_is_traceable(
        answer=answer_text, artifacts=artifacts, gold=gold,
        report=trial / "evidence-verifier.json",
    )
    terminal = {
        "expected": "completed", "actual": trace.get("terminal_state"),
        "checkpoint": trace.get("checkpoint_state"),
    }
    (trial / "termination-verifier.json").write_text(
        json.dumps(terminal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    checks = {
        "metadata": save_verdict(metadata, trial).as_check(),
        "boundaries": save_verdict(boundaries, trial).as_check(),
        "evidence": save_verdict(evidence, trial).as_check(),
    }
    return {
        "suite_version": "forestry-eval-0.1",
        "case_id": "forestry.product_qa",
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
