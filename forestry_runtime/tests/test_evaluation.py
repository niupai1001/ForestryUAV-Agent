"""Scoring semantics only: synthetic evidence is not real Agent acceptance."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.rules import SCORING_RULES_VERSION
from evaluation.scorecard import CONFIG_FIELDS, numeric_check, scorecard


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "proof.json").write_text('{"test_evidence":true}', encoding="utf-8")
        self.suite = {
            "version": "test-only", "repeats": {"agent": 3, "engineering": 1, "ui": 1},
            "agent_groups": ["core", "forestry"],
            "cases": [
                {"id": "csv", "track": "agent", "group": "core", "checks": {"value": "correct"}},
                {"id": "raster", "track": "agent", "group": "forestry", "checks": {"value": "correct"}},
                {"id": "safe", "track": "engineering", "group": "gates", "gate": True, "checks": {"value": "correct"}},
                {"id": "send", "track": "ui", "group": "interaction", "checks": {"value": "correct"}},
            ],
        }

    def record(self, case_id, repeat=1, verdict="pass"):
        case = next(case for case in self.suite["cases"] if case["id"] == case_id)
        return {
            "suite_version": self.suite["version"], "case_id": case_id,
            "track": case["track"],
            "scoring_rules_version": SCORING_RULES_VERSION,
            "execution": {"agent": "real_model", "engineering": "engineering", "ui": "browser"}[case["track"]],
            "repeat": repeat, "trial_id": f"{case_id}-{repeat}",
            "configuration": {name: "synthetic-test-only" for name in CONFIG_FIELDS},
            "status": "evaluated",
            "status_evidence": {"verifier": "test-v1", "evidence": ["proof.json"]},
            "checks": {"value": {
                "verdict": verdict, "verifier": "test-v1", "evidence": ["proof.json"],
                "detail": "synthetic check",
            }},
        }

    def all_records(self):
        records = []
        for case in self.suite["cases"]:
            conditions = max(1, len(case.get("conditions") or ["normal"]))
            for repeat in range(
                1, self.suite["repeats"][case["track"]] * conditions + 1
            ):
                record = self.record(case["id"], repeat)
                if conditions > 1:
                    per_condition = self.suite["repeats"][case["track"]]
                    names = list(case.get("conditions") or ["normal"])
                    record["condition"] = names[min(
                        len(names) - 1, (repeat - 1) // per_condition
                    )]
                    record["repeat"] = ((repeat - 1) % per_condition) + 1
                    record["trial_id"] = f"{case['id']}-{record['condition']}-{record['repeat']}"
                records.append(record)
        return records

    def test_no_records_means_unknown_not_perfect_or_zero_capability(self):
        report = scorecard(self.suite, [], self.root)
        self.assertEqual(report["qualification"], "incomplete")
        self.assertIsNone(report["agent_macro_score"])
        self.assertEqual(report["agent_missing_evidence_bounds"], [0, 100])
        self.assertEqual(len(report["missing_evidence"]), 8)

    def test_missing_evidence_and_checks_cannot_pass(self):
        for mutation in ("no_file", "no_check", "no_verifier", "no_terminal"):
            with self.subTest(mutation=mutation):
                records = self.all_records()
                if mutation == "no_file":
                    records[0]["checks"]["value"]["evidence"] = ["missing.json"]
                elif mutation == "no_check":
                    records[0]["checks"] = {}
                elif mutation == "no_verifier":
                    records[0]["checks"]["value"]["verifier"] = ""
                else:
                    records[0]["status_evidence"] = {}
                report = scorecard(self.suite, records, self.root)
                self.assertIsNone(report["agent_macro_score"])
                self.assertEqual(report["groups"]["agent.core"]["unknown"], 1)
                self.assertAlmostEqual(report["groups"]["agent.core"]["missing_evidence_bounds"][0], 200 / 3)

    def test_failed_gate_blocks_even_when_agent_outcomes_are_perfect(self):
        records = self.all_records()
        records[-2]["checks"]["value"]["verdict"] = "fail"
        report = scorecard(self.suite, records, self.root)
        self.assertEqual(report["agent_macro_score"], 100)
        self.assertEqual(report["qualification"], "blocked")
        self.assertEqual(report["gates"], "fail")

    def test_repeats_are_not_best_of_n_and_tracks_do_not_mix(self):
        records = self.all_records()
        for record in records[:2]:
            record["checks"]["value"]["verdict"] = "fail"
        report = scorecard(self.suite, records, self.root)
        self.assertAlmostEqual(report["agent_macro_score"], 200 / 3)
        self.assertEqual(report["groups"]["agent.core"]["all_repeats_success_rate"], 0)
        self.assertEqual(report["qualification"], "measured_unqualified")
        records[-1]["checks"]["value"]["verdict"] = "fail"
        after = scorecard(self.suite, records, self.root)
        self.assertEqual(after["agent_macro_score"], report["agent_macro_score"])
        self.assertEqual(after["groups"]["ui.interaction"]["score"], 0)

    def test_duplicate_slots_ids_and_extra_repeats_are_rejected(self):
        first = self.record("csv")
        variations = [copy.deepcopy(first) for _ in range(3)]
        variations[0]["trial_id"] = "different-trial-same-slot"
        variations[1]["repeat"] = 2
        variations[2]["repeat"] = 4
        variations[2]["trial_id"] = "fourth-run"
        for other in variations:
            with self.assertRaises(ValueError):
                scorecard(self.suite, [first, other], self.root)

    def test_mocks_http_only_and_mixed_config_cannot_enter_agent_or_ui_scores(self):
        record = self.record("csv")
        record["execution"] = "mock"
        with self.assertRaises(ValueError):
            scorecard(self.suite, [record], self.root)
        record = self.record("send")
        record["execution"] = "http"
        with self.assertRaises(ValueError):
            scorecard(self.suite, [record], self.root)
        records = [self.record("csv"), self.record("raster")]
        records[1]["configuration"]["model_digest"] = "other-model"
        with self.assertRaises(ValueError):
            scorecard(self.suite, records, self.root)

    def test_infrastructure_unknown_keeps_denominator_timeout_fails(self):
        records = self.all_records()
        records[0]["status"] = "infra_error"
        report = scorecard(self.suite, records, self.root)
        self.assertEqual(report["groups"]["agent.core"]["planned"], 3)
        self.assertEqual(report["groups"]["agent.core"]["unknown"], 1)
        records[0]["status"] = "timeout"
        records[0]["checks"] = {}
        report = scorecard(self.suite, records, self.root)
        self.assertEqual(report["groups"]["agent.core"]["failed"], 1)
        self.assertEqual(report["groups"]["agent.core"]["unknown"], 0)

    def test_numerical_unit_zero_and_nonfinite_handling(self):
        options = {"actual_unit": "m", "expected_unit": "m", "abs_tol": 0.01, "rel_tol": 0.001}
        self.assertTrue(numeric_check(0.005, 0, **options)["passed"])
        self.assertFalse(numeric_check(10, 1, **options)["passed"])
        self.assertFalse(numeric_check(1, 1, **(options | {"actual_unit": "degree"}))["passed"])
        self.assertFalse(numeric_check(-1, 1, **options)["passed"])
        for value in (float("nan"), float("inf"), None, True):
            with self.assertRaises(ValueError):
                numeric_check(value, 1, **options)

    def test_repository_suite_has_frozen_coverage_without_backfilled_results(self):
        path = Path(__file__).resolve().parents[1] / "evaluation" / "suite.json"
        suite = json.loads(path.read_text(encoding="utf-8"))
        report = scorecard(suite, [], self.root)
        self.assertEqual(len(report["cases"]), 24)
        # The base denominator is 46 preregistered slots; each case that declares an
        # extra input condition registers slots for it. The capability task set adds
        # six families over twelve scenarios, six slots each.
        self.assertEqual(sum(item["planned"] for item in report["cases"]), 85)
        capability = [item for item in report["cases"] if item["group"] == "capability"]
        self.assertEqual(len(capability), 6)
        self.assertEqual(sum(item["planned"] for item in capability), 36)
        conditional = {
            item["id"] for item in report["cases"] if item["conditions"] == 2
        }
        self.assertEqual(conditional, {
            "capability.chm", "capability.inventory", "capability.ndvi",
            "capability.raster_stats", "capability.recompute",
            "capability.supervised", "forestry.chm",
        })
        self.assertIn("capability", suite["agent_groups"])
        self.assertIn("agent.capability", report["groups"])
        self.assertEqual(report["qualification"], "incomplete")
        self.assertIsNone(report["agent_macro_score"])


if __name__ == "__main__":
    unittest.main()
