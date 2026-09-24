"""Tests for the end-of-collection pipeline.

Two pieces decide whether an A/B report is produced at all, and both must refuse
rather than guess:

* ``audit`` -- an arm must hold exactly the planned slots with no trial id claimed
  twice. A report over an incomplete arm describes a subset while looking final, and a
  duplicated trial id means two different runs answer for one ``(case, condition,
  repeat)`` slot.
* ``restamp_fingerprints`` -- a configuration re-freeze moves the fingerprint the
  runner recomputes, which would otherwise make the resume guard reject every
  already-collected slot as "collected from different inputs". Re-stamping must
  rewrite the value *only* for slots whose own record is self-consistent.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.finalize_ab import EXPECTED, audit  # noqa: E402
from evaluation.rules import SCORING_RULES_VERSION, read_fingerprint  # noqa: E402


def _slot(root: Path, name: str, record: dict, fingerprint: dict | None) -> None:
    trial = root / name
    trial.mkdir(parents=True, exist_ok=True)
    (trial / "record.json").write_text(json.dumps(record), encoding="utf-8")
    if fingerprint is not None:
        (trial / "collection.json").write_text(
            json.dumps({"rules_version": SCORING_RULES_VERSION, **fingerprint}),
            encoding="utf-8",
        )


def _record(case_id: str, condition: str, repeat: int, trial_id: str,
            status: str = "evaluated") -> dict:
    return {"case_id": case_id, "condition": condition, "repeat": repeat,
            "trial_id": trial_id, "status": status, "checks": {}}


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _complete_root(self) -> None:
        for case, repeat in sorted(EXPECTED):
            name = f"{case.replace('.', '-')}-{repeat}"
            _slot(self.root, name,
                  _record(case, "normal" if repeat <= 3 else "gap", repeat, f"run-{name}"),
                  None)

    def test_a_complete_root_is_accepted(self):
        self._complete_root()
        result = audit(self.root)
        self.assertTrue(result["complete"])
        self.assertEqual(result["graded"], len(EXPECTED))
        self.assertEqual(result["duplicate_trial_ids"], [])

    def test_a_missing_slot_is_named(self):
        self._complete_root()
        (self.root / "capability-chm-6").rename(self.root / "capability-chm-6-moved")
        (self.root / "capability-chm-6-moved").rename(self.root / "_aside")
        result = audit(self.root)
        self.assertFalse(result["complete"])
        self.assertIn("capability-chm-6", result["missing"])

    def test_an_extra_slot_is_reported(self):
        self._complete_root()
        _slot(self.root, "capability-chm-7",
              _record("capability.chm", "gap", 4, "run-extra"), None)
        result = audit(self.root)
        self.assertFalse(result["complete"])
        self.assertEqual(result["extra"], ["capability-chm-7"])

    def test_a_duplicate_trial_id_is_reported(self):
        self._complete_root()
        _slot(self.root, "capability-chm-1",
              _record("capability.chm", "normal", 1, "run-capability-ndvi-1"), None)
        result = audit(self.root)
        self.assertTrue(result["duplicate_trial_ids"])
        self.assertEqual(result["duplicate_trial_ids"][0]["trial_id"],
                         "run-capability-ndvi-1")

    def test_an_empty_trial_id_is_reported(self):
        self._complete_root()
        _slot(self.root, "capability-chm-1",
              _record("capability.chm", "normal", 1, ""), None)
        result = audit(self.root)
        self.assertTrue(
            any(item.get("reason") == "empty trial id"
                for item in result["duplicate_trial_ids"])
        )

    def test_infrastructure_slots_are_listed_for_the_report(self):
        self._complete_root()
        _slot(self.root, "capability-chm-1",
              _record("capability.chm", "normal", 1, "run-x", status="infra_error"),
              None)
        result = audit(self.root)
        self.assertEqual(result["ungraded_reasons"], {"capability-chm-1": "infra_error"})


class RestampTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "configuration-agent.json").write_text(
            json.dumps({
                "code_snapshot": "c", "model_digest": "m", "prompt_snapshot": "p",
                "tools_snapshot": "t", "dataset_version": "d",
                "environment_snapshot": "e", "evaluator_version": "v",
                "sampling": {"temperature": 0.2}, "budgets": {"model_requests": 32},
            }), encoding="utf-8")

    def _consistent_slot(self, name: str, condition: str, trial_repeat: int):
        case_id = "capability.chm"
        _slot(self.root, name, _record(case_id, condition, trial_repeat, f"run-{name}"), {
            "fingerprint": "fp_stale_value", "case_id": case_id,
            "condition": condition, "trial_repeat": trial_repeat,
            "slot_repeat": 1, "runtime_environment": {},
        })

    def test_a_stale_fingerprint_is_replaced(self):
        from evaluation.restamp_fingerprints import restamp

        self._consistent_slot("capability-chm-1", "normal", 1)
        report = restamp(self.root, apply=True)
        self.assertEqual(report["summary"]["restamped"], 1)
        stored = read_fingerprint(self.root / "capability-chm-1")
        self.assertNotEqual(stored["fingerprint"], "fp_stale_value")
        self.assertEqual(stored["rules_version"], SCORING_RULES_VERSION)

    def test_a_slot_whose_condition_contradicts_its_number_is_refused(self):
        """Trial 4 of a two-condition case is the gap condition, not normal."""
        from evaluation.restamp_fingerprints import restamp

        self._consistent_slot("capability-chm-4", "normal", 4)
        report = restamp(self.root, apply=True)
        self.assertEqual(report["summary"]["restamped"], 0)
        self.assertEqual(len(report["refused"]), 1)
        self.assertIn("recorded condition", report["refused"][0]["reason"])
        stored = read_fingerprint(self.root / "capability-chm-4")
        self.assertEqual(stored["fingerprint"], "fp_stale_value")

    def test_an_unknown_case_is_refused(self):
        from evaluation.restamp_fingerprints import restamp

        _slot(self.root, "capability-mystery-1",
              _record("capability.mystery", "normal", 1, "run-x"),
              {"fingerprint": "fp_x", "case_id": "capability.mystery",
               "condition": "normal", "trial_repeat": 1, "slot_repeat": 1})
        report = restamp(self.root, apply=True)
        self.assertEqual(report["summary"]["restamped"], 0)
        self.assertEqual(len(report["refused"]), 1)

    def test_a_slot_without_a_fingerprint_is_reported_not_invented(self):
        from evaluation.restamp_fingerprints import restamp

        _slot(self.root, "capability-chm-2",
              _record("capability.chm", "normal", 2, "run-y"), None)
        report = restamp(self.root, apply=True)
        self.assertEqual(report["absent"], ["capability-chm-2"])
        self.assertFalse((self.root / "capability-chm-2" / "collection.json").is_file())

    def test_a_dry_run_changes_nothing(self):
        from evaluation.restamp_fingerprints import restamp

        self._consistent_slot("capability-chm-1", "normal", 1)
        report = restamp(self.root, apply=False)
        self.assertEqual(report["summary"]["restamped"], 1)
        stored = read_fingerprint(self.root / "capability-chm-1")
        self.assertEqual(stored["fingerprint"], "fp_stale_value")


if __name__ == "__main__":
    unittest.main()
