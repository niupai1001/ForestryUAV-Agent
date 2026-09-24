"""Paired A/B comparison tests.

The comparison must not be able to flatter an arm. Two properties carry that:

* a slot only one arm produced is reported as unpaired, never dropped -- discarding
  the slots an arm failed to deliver is how a broken arm looks better than it is;
* pairs are keyed by case, declared condition and repeat, so the gap condition is
  never compared against the normal one.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.compare_arms import (  # noqa: E402
    compare, format_text, load_arm,
)


def _trial(
    root: Path, name: str, *, case_id: str, condition: str, repeat: int,
    terminal: str, status: str, checks: dict[str, str],
    model_calls: int = 1, tool_calls: int = 1, include: bool = True,
) -> None:
    if not include:
        return
    trial = root / name
    trial.mkdir(parents=True, exist_ok=True)
    (trial / "raw").mkdir(exist_ok=True)
    (trial / "record.json").write_text(json.dumps({
        "suite_version": "forestry-eval-0.1",
        "scoring_rules_version": "forestry-rules-2.1",
        "case_id": case_id, "condition": condition, "repeat": repeat,
        "status": status, "trial_id": f"{name}-run",
        "checks": {
            key: {"verdict": value, "verifier": "test-v1", "evidence": [],
                  "detail": f"{key} is {value}"}
            for key, value in checks.items()
        },
    }, ensure_ascii=False), encoding="utf-8")
    (trial / "trace.json").write_text(json.dumps({
        "run_id": f"{name}-run", "case_id": case_id, "repeat": repeat,
        "terminal_state": terminal, "configuration": {"code_snapshot": "x"},
        "steps": [{"tool": "fs_list", "status": "success"} for _ in range(tool_calls)],
    }, ensure_ascii=False), encoding="utf-8")
    (trial / "raw" / "events.json").write_text(json.dumps(
        [{"type": "model_call", "number": index} for index in range(1, model_calls + 1)],
        ensure_ascii=False,
    ), encoding="utf-8")


def _configuration(root: Path, **overrides) -> None:
    payload = {
        "code_snapshot": "abc", "model_digest": "sha256:deadbeef",
        "prompt_snapshot": "system+12@abc", "tools_snapshot": "toolsha",
        "dataset_version": "forestry-eval-0.1",
        "environment_snapshot": "same-image", "evaluator_version": "forestry-eval-0.1",
        "sampling": {"temperature": 0.2}, "budgets": {"model_requests": 32},
    } | overrides
    (root / "configuration-agent.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.arm_a = base / "arm-a"
        self.arm_b = base / "arm-b"
        self.arm_a.mkdir()
        self.arm_b.mkdir()
        _configuration(self.arm_a)
        _configuration(self.arm_b)

    def test_a_clean_pair_reports_both_sides(self):
        for repeat in (1, 2, 3):
            _trial(self.arm_a, f"capability-inventory-{repeat}",
                   case_id="capability.inventory", condition="normal", repeat=repeat,
                   terminal="completed", status="evaluated",
                   checks={"counts": "fail", "suitability": "fail"})
            _trial(self.arm_b, f"capability-inventory-{repeat}",
                   case_id="capability.inventory", condition="normal", repeat=repeat,
                   terminal="completed", status="evaluated",
                   checks={"counts": "pass", "suitability": "pass"})
        report = compare(self.arm_a, self.arm_b)
        self.assertEqual(report["slots_total"], 3)
        self.assertEqual(report["slots_paired"], 3)
        self.assertEqual(report["totals"]["a"]["pass"], 0)
        self.assertEqual(report["totals"]["b"]["pass"], 3)
        entry = report["by_case"]["capability.inventory@normal"]
        self.assertEqual(len(entry["differences"]), 3)
        self.assertTrue(report["configuration_identical"])

    def test_a_slot_only_one_arm_produced_is_reported_not_dropped(self):
        """Dropping it is how an arm that failed to deliver looks better."""
        _trial(self.arm_a, "capability-chm-1",
               case_id="capability.chm", condition="normal", repeat=1,
               terminal="completed", status="evaluated",
               checks={"positive": "pass", "claim": "pass", "negative": "pass"})
        _trial(self.arm_b, "capability-chm-1",
               case_id="capability.chm", condition="normal", repeat=1,
               terminal="failed", status="crash",
               checks={"positive": "fail", "claim": "fail", "negative": "fail"})
        _trial(self.arm_a, "capability-chm-2",
               case_id="capability.chm", condition="normal", repeat=2,
               terminal="completed", status="evaluated",
               checks={"positive": "pass", "claim": "pass", "negative": "pass"})
        report = compare(self.arm_a, self.arm_b)
        self.assertEqual(report["slots_total"], 2)
        self.assertEqual(report["slots_paired"], 1)
        self.assertEqual(len(report["slots_unpaired"]), 1)
        self.assertEqual(report["slots_unpaired"][0]["missing"], ["b"])
        # The A arm's pass rate covers only what B also produced, so the unpaired
        # slot never inflates one side of the comparison.
        self.assertEqual(report["totals"]["a"]["graded"], 2)

    def test_the_gap_condition_is_never_paired_with_the_normal_one(self):
        _trial(self.arm_a, "capability-ndvi-1",
               case_id="capability.ndvi", condition="normal", repeat=1,
               terminal="completed", status="evaluated",
               checks={"pixels": "pass", "grid_mask": "pass", "answer": "pass"})
        _trial(self.arm_a, "capability-ndvi-4",
               case_id="capability.ndvi", condition="gap", repeat=1,
               terminal="completed", status="evaluated",
               checks={"pixels": "not_applicable", "grid_mask": "not_applicable",
                       "answer": "pass"})
        slots = load_arm(self.arm_a)
        self.assertIn(("capability.ndvi", "normal", 1), slots)
        self.assertIn(("capability.ndvi", "gap", 1), slots)
        report = compare(self.arm_a, self.arm_b)
        self.assertIn("capability.ndvi@normal", report["by_case"])
        self.assertIn("capability.ndvi@gap", report["by_case"])
        # No un-suffixed entry: the two conditions never share a slot.
        self.assertNotIn("capability.ndvi", report["by_case"])
        self.assertEqual(report["slots_total"], 2)
        self.assertEqual(len(report["slots_unpaired"]), 2)
        self.assertEqual(
            report["by_case"]["capability.ndvi@normal"]["a"]["pass"], 1
        )
        self.assertEqual(
            report["by_case"]["capability.ndvi@gap"]["a"]["pass"], 1
        )

    def test_an_arm_graded_by_different_settings_is_flagged(self):
        _configuration(self.arm_b, sampling={"temperature": 0.9})
        report = compare(self.arm_a, self.arm_b)
        self.assertFalse(report["configuration_identical"])
        self.assertIn("sampling", report["configuration_drift"])

    def test_totals_are_reported_per_dimension(self):
        for repeat in (1, 2):
            _trial(self.arm_a, f"capability-supervised-{repeat}",
                   case_id="capability.supervised", condition="normal", repeat=repeat,
                   terminal="paused", status="crash",
                   checks={"split": "fail", "metrics": "unknown", "baseline": "fail"},
                   model_calls=4, tool_calls=3)
            _trial(self.arm_b, f"capability-supervised-{repeat}",
                   case_id="capability.supervised", condition="normal", repeat=repeat,
                   terminal="completed", status="evaluated",
                   checks={"split": "pass", "metrics": "pass", "baseline": "pass"},
                   model_calls=9, tool_calls=7)
        report = compare(self.arm_a, self.arm_b)
        totals_a = report["totals"]["a"]
        totals_b = report["totals"]["b"]
        # Engineering reliability: the Runtime delivered both Runs, but the A arm's
        # were paused by the repeated-failure guard rail and score as crashes.
        self.assertEqual(totals_a["terminals"], {"paused": 2})
        self.assertEqual(totals_a["statuses"], {"crash": 2})
        self.assertEqual(totals_b["statuses"], {"evaluated": 2})
        # Cost: the B arm spends more model calls to reach a correct answer.
        self.assertEqual(totals_a["model_calls"], 8)
        self.assertEqual(totals_b["model_calls"], 18)
        # Judgement quality is attributable per check.
        self.assertEqual(report["checks"]["capability.supervised@normal:metrics"]["a"],
                         {"unknown": 2})
        self.assertEqual(report["checks"]["capability.supervised@normal:metrics"]["b"],
                         {"pass": 2})

    def test_reproducibility_reports_whether_the_repeats_agreed(self):
        _trial(self.arm_a, "capability-chm-1",
               case_id="capability.chm", condition="normal", repeat=1,
               terminal="completed", status="evaluated",
               checks={"positive": "pass", "claim": "pass", "negative": "pass"})
        _trial(self.arm_a, "capability-chm-2",
               case_id="capability.chm", condition="normal", repeat=2,
               terminal="completed", status="evaluated",
               checks={"positive": "fail", "claim": "fail", "negative": "fail"})
        _trial(self.arm_b, "capability-chm-1",
               case_id="capability.chm", condition="normal", repeat=1,
               terminal="completed", status="evaluated",
               checks={"positive": "pass", "claim": "pass", "negative": "pass"})
        _trial(self.arm_b, "capability-chm-2",
               case_id="capability.chm", condition="normal", repeat=2,
               terminal="completed", status="evaluated",
               checks={"positive": "pass", "claim": "pass", "negative": "pass"})
        report = compare(self.arm_a, self.arm_b)
        entry = report["reproducibility"]["capability.chm@normal"]
        self.assertFalse(entry["a"]["unanimous"])
        self.assertEqual(entry["a"]["distinct"], 2)
        self.assertTrue(entry["b"]["unanimous"])

    def test_not_applicable_does_not_count_as_a_failure(self):
        _trial(self.arm_a, "capability-chm-4",
               case_id="capability.chm", condition="gap", repeat=1,
               terminal="completed", status="evaluated",
               checks={"positive": "not_applicable", "claim": "not_applicable",
                       "negative": "pass"})
        _trial(self.arm_b, "capability-chm-4",
               case_id="capability.chm", condition="gap", repeat=1,
               terminal="completed", status="evaluated",
               checks={"positive": "not_applicable", "claim": "not_applicable",
                       "negative": "pass"})
        report = compare(self.arm_a, self.arm_b)
        self.assertEqual(report["totals"]["a"]["pass"], 1)
        self.assertEqual(report["totals"]["a"]["fail"], 0)

    def test_missing_evidence_is_unknown_not_a_pass(self):
        _trial(self.arm_a, "capability-recompute-1",
               case_id="capability.recompute", condition="normal", repeat=1,
               terminal="running", status="infra_error",
               checks={"lineage": "unknown", "fresh_job": "unknown",
                       "new_result": "unknown"})
        _trial(self.arm_b, "capability-recompute-1",
               case_id="capability.recompute", condition="normal", repeat=1,
               terminal="running", status="infra_error",
               checks={"lineage": "unknown", "fresh_job": "unknown",
                       "new_result": "unknown"})
        report = compare(self.arm_a, self.arm_b)
        self.assertEqual(report["totals"]["a"]["unknown"], 1)
        self.assertEqual(report["totals"]["a"]["pass"], 0)
        self.assertEqual(report["totals"]["a"]["graded_rate"], 1.0)

    def test_the_text_report_names_every_dimension(self):
        _trial(self.arm_a, "capability-chm-1",
               case_id="capability.chm", condition="normal", repeat=1,
               terminal="completed", status="evaluated",
               checks={"positive": "pass", "claim": "pass", "negative": "pass"})
        text = format_text(compare(self.arm_a, self.arm_b))
        for phrase in ("工程可靠性", "任务正确性", "可复现性", "成本", "未配对"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_an_empty_evidence_root_does_not_crash_the_report(self):
        report = compare(self.arm_a, self.arm_b)
        self.assertEqual(report["slots_total"], 0)
        self.assertIsNone(report["totals"]["a"]["graded_rate"])
        self.assertIn("未配对", format_text(report))


if __name__ == "__main__":
    unittest.main()
