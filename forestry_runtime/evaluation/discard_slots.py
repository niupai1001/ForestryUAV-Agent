"""Delete graded slot directories whose evidence is known to be unusable.

Each group named on the command line is removed, including any stale
`infra_error` record.  The evidence roots' own bookkeeping files
(`configuration-agent.json`, `records.json`, `scorecard.json`) are never touched:
they are files, not trial directories, and losing them would lose the frozen
configuration the comparison depends on.

Used when a deployment defect -- not the Agent -- produced the result, so the slot
must be collected again rather than resumed. `run_baseline --force` would reuse
the existing record, which is exactly the wrong thing here.

    python -m evaluation.discard_slots evaluation/work/arm-a \
        capability-chm-1 capability-chm-2 ...
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

KEEP = {"configuration-agent.json", "records.json", "scorecard.json"}


def discard(root: Path, names: list[str]) -> dict:
    removed: list[str] = []
    missing: list[str] = []
    refused: list[str] = []
    for name in names:
        target = root / name
        if name in KEEP:
            refused.append(name)
            continue
        if not target.is_dir():
            missing.append(name)
            continue
        shutil.rmtree(target)
        removed.append(name)
    return {"root": str(root), "removed": removed, "missing": missing,
            "refused": refused}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("names", nargs="+")
    args = parser.parse_args()
    result = discard(args.root, args.names)
    for name in result["removed"]:
        print("removed", name)
    for name in result["missing"]:
        print("not present", name)
    for name in result["refused"]:
        print("refused (bookkeeping file)", name)
    return 0 if not result["refused"] else 2


if __name__ == "__main__":
    sys.exit(main())
