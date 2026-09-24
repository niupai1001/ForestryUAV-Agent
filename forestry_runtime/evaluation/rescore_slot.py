"""Re-run one verifier over one collected trial, without touching the evidence.

Scoring rules change while an experiment is running, and re-scoring must not require
re-spending model calls. `run_baseline --verify-only` does this for a whole root; this
does it for a single slot, which is what is wanted while deciding *whether* a rule
change is correct.

    python -m evaluation.rescore_slot evaluation/work/arm-b/capability-chm-4
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.cases.registry import CASES  # noqa: E402
from evaluation.verify.chm import chm_claims_match_artifact  # noqa: E402
from evaluation.verify.capability_common import answer_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", type=Path)
    parser.add_argument("--check", default="negative")
    args = parser.parse_args()
    trial = args.trial.resolve()
    record = json.loads((trial / "record.json").read_text(encoding="utf-8"))
    case = CASES[record["case_id"]]
    gold = PROJECT_ROOT / case.gold
    answer = answer_text(trial)
    print("before:", json.dumps(
        record["checks"].get(args.check, {}), ensure_ascii=False)[:200])
    verdict = chm_claims_match_artifact(
        answer=answer, artifacts=trial / "artifacts", gold=gold,
        require_built=False, report=trial / "claims-rescore.json",
    )
    print(json.dumps({
        "verdict": verdict.verdict, "verifier": verdict.verifier,
        "detail": verdict.detail,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
