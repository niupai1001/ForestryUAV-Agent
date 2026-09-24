"""One-screen progress report for the capability collection.

Answers the only three questions that matter while the experiment runs: how many of
the planned slots have a graded record, how the families are doing, and which slots
are still in flight. Reads only evidence already on disk.

An arm may be collected by two roots at once (see ``run_baseline --shard``), so the
report groups roots by arm and counts each arm's slots once: a shard that owns
``capability.supervised`` 1..6 plus a main root that owns the other four cases are one
arm of 36, not two experiments of 36.

    python evaluation/watch_progress.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent / "work"
PLANNED = 36


def _arms() -> dict[str, list[str]]:
    """Every evidence root, grouped by the arm it belongs to.

    Discovered rather than listed: collection gets split across shard roots as the
    wall-clock pressure changes (``arm-a-p3``, ``-p4``, ...), and a hard-coded list
    silently stops counting the newest one -- which reads as "collection stalled".
    """
    arms: dict[str, list[str]] = {}
    if not ROOT.is_dir():
        return arms
    for entry in sorted(ROOT.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("arm-"):
            continue
        # "arm-a", "arm-a-p3", "arm-b" all name their arm in the second field; using
        # everything up to the last "-" put "arm-a-p3" in a different arm than
        # "arm-a", so a shard's slots were counted twice under two arm labels.
        arm = entry.name.split("-")[1]
        arms.setdefault(arm, []).append(entry.name)
    return arms


def _load(root: Path) -> tuple[dict[str, dict], list[str]]:
    graded: dict[str, dict] = {}
    in_flight: list[str] = []
    if not root.is_dir():
        return graded, in_flight
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        record = entry / "record.json"
        if record.is_file():
            graded[entry.name] = json.loads(record.read_text(encoding="utf-8"))
        else:
            in_flight.append(entry.name)
    return graded, in_flight


def main() -> int:
    total = 0
    for arm, roots in _arms().items():
        graded: dict[str, dict] = {}
        in_flight: list[str] = []
        for name in roots:
            part, pending = _load(ROOT / name)
            for slot, record in part.items():
                if slot in graded:
                    # Two roots claiming one slot is a collection defect, not a
                    # majority vote: say so rather than silently keeping the last read.
                    print(f"  !! {arm}: {slot} is graded by more than one root")
                graded[slot] = record
            in_flight.extend(pending)
        total += len(graded)
        statuses = Counter(record["status"] for record in graded.values())
        print(f"{arm:<12} graded {len(graded):>2}/{PLANNED}  {dict(sorted(statuses.items()))}")
        families = Counter(slot.rsplit("-", 1)[0] for slot in graded)
        for family, count in sorted(families.items()):
            verdicts: Counter = Counter()
            for slot, record in graded.items():
                if not slot.startswith(family):
                    continue
                for check in record.get("checks", {}).values():
                    verdicts[check["verdict"]] += 1
            print(f"    {family:<26} {count}/6  {dict(sorted(verdicts.items()))}")
        if in_flight:
            print(f"    in flight: {', '.join(sorted(in_flight))}")
    print(f"\ntotal graded: {total} of {PLANNED * 2} planned")
    print("time:", time.strftime("%H:%M:%S"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
