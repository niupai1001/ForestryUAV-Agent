"""Parallel collection must partition slots, not renumber them.

Collection is wall-clock bound, so two roots collect at once. The first attempt split
by *case* and passed `--repeats 1 2 3`, which the shard applied as "repeats 1..3 of
this case" -- so the shard's `capability-recompute-4` was actually repeat 1 of the
*normal* condition, and its trial id collided with the canonical `-1` directory. The
scorecard reported the whole shard as "superseded by the canonical trial directory",
which is the correct behaviour and the wrong experiment.

`--shard` names absolute trial numbers per case, so two roots write the same
directory names for different slots and a merge is a move.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.cases.registry import CASES as CASES_BY_ID  # noqa: E402
from evaluation.run_baseline import _selected_slots, _slot_condition  # noqa: E402

SUITE = json.loads((ROOT / "evaluation" / "suite.json").read_text(encoding="utf-8"))
CASES = {"capability.supervised", "capability.recompute", "capability.chm"}


def pairs(slots):
    return [(case["id"], repeat) for case, repeat in slots]


class ShardSelectionTests(unittest.TestCase):
    def test_the_same_selection_yields_the_same_slots(self):
        """`--repeats 1 2 3` and "no shard" collect exactly the same trials.

        Both must cover each declared condition, because that is what the 12-scenario
        set means; the earlier code silently collected only the first condition when a
        repeat subset was named.
        """
        subset = pairs(_selected_slots(SUITE, {"agent"}, CASES, {1, 2, 3}))
        every = pairs(_selected_slots(SUITE, {"agent"}, CASES, None))
        self.assertEqual(subset, every)
        self.assertIn(("capability.chm", 4), subset)
        self.assertIn(("capability.chm", 6), subset)

    def test_a_shard_partitions_the_slots(self):
        first = pairs(_selected_slots(
            SUITE, {"agent"}, CASES, None,
            shard={"capability.supervised": [1, 2, 3],
                   "capability.recompute": [1, 2, 3]},
        ))
        second = pairs(_selected_slots(
            SUITE, {"agent"}, CASES, None,
            shard={"capability.supervised": [4, 5, 6],
                   "capability.recompute": [4, 5, 6]},
        ))
        self.assertEqual(len(first), 6)
        self.assertEqual(len(second), 6)
        self.assertEqual(set(first) & set(second), set(), "no slot may be collected twice")
        self.assertEqual(set(first) | set(second), set(pairs(
            _selected_slots(SUITE, {"agent"},
                            {"capability.supervised", "capability.recompute"}, None)
        )))

    def test_a_shard_keeps_the_global_trial_numbers(self):
        """The number is the slot's identity; renumbering is the bug being fixed."""
        slots = _selected_slots(
            SUITE, {"agent"}, {"capability.recompute"}, None,
            shard={"capability.recompute": [6]},
        )
        self.assertEqual(pairs(slots), [("capability.recompute", 6)])
        per_condition = int(CASES_BY_ID["capability.recompute"].repeats
                            or SUITE["repeats"]["agent"])
        self.assertEqual(_slot_condition("capability.recompute", 6, per_condition),
                         "changed")

    def test_a_shard_cannot_name_a_slot_the_case_does_not_own(self):
        with self.assertRaises(ValueError):
            _selected_slots(SUITE, {"agent"}, {"capability.recompute"}, None,
                            shard={"capability.recompute": [7]})
        with self.assertRaises(ValueError):
            _selected_slots(SUITE, {"agent"}, CASES, None,
                            shard={"capability.not_a_case": [1]})

    def test_a_shard_does_not_widen_the_selection(self):
        """Naming a case the command did not ask for must not collect it."""
        slots = pairs(_selected_slots(
            SUITE, {"agent"}, {"capability.chm"}, None,
            shard={"capability.chm": [1], "capability.supervised": [1]},
        ))
        self.assertEqual(slots, [("capability.chm", 1)])


if __name__ == "__main__":
    unittest.main()
