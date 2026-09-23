"""Tests for record discovery, which decides what a scorecard actually covers.

Discovery has two jobs that pull in opposite directions: report every slot on disk
so a partial run still describes the whole system, and never let two files describe
one slot. Getting the first wrong hid one track's evidence when the other was run;
getting the second wrong aborted the run outright, because the repository documents
*two* trial-directory layouts and both exist on disk after following the README.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from evaluation.run_baseline import _canonical_trial_dir, _discover_records


def record(case_id: str, repeat: int, trial_id: str) -> dict:
    return {
        "suite_version": "forestry-eval-0.1", "case_id": case_id,
        "track": "engineering", "execution": "engineering", "repeat": repeat,
        "trial_id": trial_id, "configuration": {}, "status": "evaluated",
        "status_evidence": {}, "checks": {},
    }


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, directory: str, filename: str, *records) -> Path:
        target = self.root / directory
        target.mkdir(parents=True, exist_ok=True)
        path = target / filename
        payload = list(records) if len(records) > 1 else records[0]
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_every_slot_on_disk_is_discovered(self):
        self.write("gate-idempotency-1", "record.json", record("gate.idempotency", 1, "a"))
        self.write("forestry-ndvi-2", "record.json", record("forestry.ndvi", 2, "b"))
        found = _discover_records(self.root)
        self.assertEqual(len(found), 2)

    def test_an_aggregate_ui_file_contributes_each_of_its_records(self):
        self.write(
            "ui-repeat-1", "records.json",
            record("ui.send", 1, "s"), record("ui.reconnect", 1, "r"),
        )
        found = _discover_records(self.root)
        self.assertEqual(len(found), 1, "one file, read once")
        payload = json.loads(found[0].read_text(encoding="utf-8"))
        self.assertEqual({item["case_id"] for item in payload}, {"ui.send", "ui.reconnect"})

    def test_the_assembled_root_records_file_is_ignored(self):
        """It is this run's own output; reading it would feed stale rows back in."""
        self.write("gate-idempotency-1", "record.json", record("gate.idempotency", 1, "a"))
        (self.root / "records.json").write_text(
            json.dumps([record("gate.idempotency", 1, "stale")]), encoding="utf-8"
        )
        found = _discover_records(self.root)
        self.assertEqual([item.parent.name for item in found], ["gate-idempotency-1"])

    def test_both_documented_layouts_for_one_slot_prefer_the_canonical_one(self):
        """The README writes `gate-idempotency/`; the runner writes `gate-idempotency-1/`.

        Following the README and then using the runner leaves both on disk. That is
        normal, not a defect, and refusing to score aborted the whole run.
        """
        self.write("gate-idempotency", "record.json", record("gate.idempotency", 1, "manual"))
        canonical = self.write(
            "gate-idempotency-1", "record.json", record("gate.idempotency", 1, "runner")
        )
        found = _discover_records(self.root)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0], canonical)
        self.assertEqual(len(_discover_records.last_superseded), 1)
        self.assertIn("gate-idempotency", _discover_records.last_superseded[0])

    def test_a_half_written_record_does_not_hide_the_others(self):
        self.write("gate-idempotency-1", "record.json", record("gate.idempotency", 1, "a"))
        broken = self.root / "gate-permissions-1"
        broken.mkdir()
        (broken / "record.json").write_text("{not json", encoding="utf-8")
        found = _discover_records(self.root)
        self.assertEqual(len(found), 1, "the readable slot survives")

    def test_canonical_directory_name_follows_the_runner_convention(self):
        self.assertEqual(_canonical_trial_dir("gate.idempotency", 1), "gate-idempotency-1")
        self.assertEqual(_canonical_trial_dir("forestry.product_qa", 3), "forestry-product_qa-3")


if __name__ == "__main__":
    unittest.main()
