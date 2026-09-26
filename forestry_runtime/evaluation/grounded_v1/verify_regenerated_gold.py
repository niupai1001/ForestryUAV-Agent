"""Verify the regenerated OAM suite against the frozen gold metadata.

The acceptance criterion of PHASE_A_RECOVERY_PLAN step 3: the recomputed
``valid_pixels``, ``gold_canopy_pixels`` and ``gold_coverage_percent`` must equal
the frozen values field by field. Anything else means the seed or the selection
rule did not line up, and the frozen values must NOT be overwritten.

Usage:
    python evaluation/grounded_v1/verify_regenerated_gold.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/oam_tcd/grounded_v1_1_private/manifest.json"
FROZEN_FILES = [
    ROOT / "evaluation/grounded_v1/tasks.grounded-v1.1.json",
    ROOT / "evaluation/grounded_v1/tasks.grounded-v1.2.json",
]
FIELDS = ("valid_pixels", "gold_canopy_pixels", "gold_coverage_percent")
IDENTITY_FIELDS = ("image_id", "oam_id", "kind", "public_input", "gold_mask",
                   "valid_definition", "total_pixels", "gold_foreground_pixels")


def gold_of(tasks_file: Path) -> dict[str, dict]:
    document = json.loads(tasks_file.read_text(encoding="utf-8"))
    gold = document.get("gold")
    if not isinstance(gold, dict):
        return {}
    return gold


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    regenerated = {task["task_id"]: task for task in manifest["tasks"]}
    failures: list[str] = []

    for tasks_file in FROZEN_FILES:
        if not tasks_file.exists():
            print(f"[skip] {tasks_file.name} not present")
            continue
        gold = gold_of(tasks_file)
        print(f"\n=== {tasks_file.name}: {len(gold)} frozen gold entries ===")
        header = f"{'task':8} {'field':24} {'frozen':>22} {'regen':>22}  ok"
        print(header)
        for task_id in sorted(gold):
            if task_id not in regenerated:
                failures.append(f"{tasks_file.name}:{task_id}: missing from regenerated manifest")
                continue
            entry = gold[task_id]
            fresh = regenerated[task_id]
            for field in FIELDS:
                if field not in entry:
                    continue
                frozen_value = entry[field]
                fresh_value = fresh[field]
                ok = frozen_value == fresh_value
                if not ok:
                    failures.append(f"{tasks_file.name}:{task_id}:{field}: {frozen_value} != {fresh_value}")
                print(f"{task_id:8} {field:24} {str(frozen_value):>22} {str(fresh_value):>22}  {'OK' if ok else 'MISMATCH'}")
            for field in IDENTITY_FIELDS:
                if field in entry and entry[field] != fresh.get(field):
                    failures.append(f"{tasks_file.name}:{task_id}:{field}: {entry[field]} != {fresh.get(field)}")

    print("\n=== selection rule replay ===")
    print(json.dumps(manifest["selection_rule"], ensure_ascii=False, indent=2))
    print("rejected candidates:", json.dumps(manifest["rejected_candidates"], ensure_ascii=False))

    if failures:
        print(f"\nFAILED: {len(failures)} mismatch(es)")
        for line in failures:
            print("  -", line)
        return 1
    print("\nPASSED: every frozen gold field is reproduced exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
