"""`--verify-only`: re-score collected evidence instead of collecting again.

A rules or verifier change must not force re-running the model, and a slot whose
collection succeeded but whose verification did not is the case this exists for.
The flag is a re-scoring resume, never a collection mode: mixing the two in one
command would quietly spend model runs an operator asked to avoid.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation import run_baseline as rb  # noqa: E402
from evaluation.rules import SCORING_RULES_VERSION  # noqa: E402


class VerifyOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "arm"
        self.root.mkdir(parents=True)
        self.configuration = {field: "test" for field in rb.CONFIG_FIELDS}
        (self.root / "configuration-agent.json").write_text(
            json.dumps(self.configuration), encoding="utf-8"
        )
        self.trial = self.root / "capability-inventory-1"
        (self.trial / "raw").mkdir(parents=True)
        (self.trial / "artifacts").mkdir()
        (self.trial / "trace.json").write_text(json.dumps({
            "run_id": "run-verify-only", "case_id": "capability.inventory",
            "repeat": 1, "configuration": self.configuration,
            "terminal_state": "completed", "checkpoint_state": "complete", "steps": [],
        }), encoding="utf-8")
        (self.trial / "raw" / "events.json").write_text(json.dumps([{
            "type": "message",
            "content": "```json\n"
            + json.dumps({
                "counts": {"png": 4, "jpg": 2, "tif": 1, "csv": 2, "las": 1},
                "quality_problems": [
                    {"path": "points.las", "kind": "truncated_point_cloud",
                     "detail": "header declares 500 points"},
                    {"path": "flight_index.csv", "kind": "malformed_row",
                     "detail": "a data row has fewer fields"},
                ],
                "usable_for_chm": False,
                "missing_evidence": ["a DTM matching the DSM grid"],
                "photogrammetry_started": False,
            }, ensure_ascii=False)
            + "\n```",
        }], ensure_ascii=False), encoding="utf-8")

    def _run(self, repeats: set[int], **kwargs):
        with patch.object(rb, "collect_trial") as collector:
            report = rb.run_baseline(
                root=self.root, tracks=["agent"], cases=["capability.inventory"],
                repeats=repeats, verify_only=True, **kwargs,
            )
        return report, collector

    def test_it_scores_existing_evidence_without_collecting(self):
        report, collector = self._run({1})
        collector.assert_not_called()
        record = json.loads((self.trial / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["case_id"], "capability.inventory")
        self.assertEqual(record["scoring_rules_version"], SCORING_RULES_VERSION)
        self.assertEqual(record["status"], "evaluated")
        self.assertEqual(record["condition"], "normal")
        self.assertIn("cases", report)

    def test_no_api_key_is_required_to_re_score(self):
        with patch.dict("os.environ", {"RUNTIME_API_KEY": ""}, clear=False):
            rb.run_baseline(
                root=self.root, tracks=["agent"],
                cases=["capability.inventory"], repeats={1}, verify_only=True,
            )
        self.assertTrue((self.trial / "record.json").is_file())

    def test_a_slot_without_evidence_is_skipped_and_reported(self):
        """Skipped, never silently collected: the flag promises no model runs."""
        report, collector = self._run({1, 2, 3})
        collector.assert_not_called()
        # Repeats 1..3 are the normal condition; 4..6 are the declared gap condition,
        # which `--repeats` selects as well. Only the first has evidence.
        self.assertEqual(
            sorted(report.get("verify_only_skipped") or []),
            [f"capability.inventory-{index}" for index in range(2, 7)],
        )
        self.assertTrue((self.trial / "record.json").is_file())

    def test_a_slot_without_a_trace_is_never_turned_into_a_record(self):
        (self.trial / "trace.json").unlink()
        report, collector = self._run({1})
        collector.assert_not_called()
        self.assertFalse((self.trial / "record.json").is_file())
        self.assertIn("capability.inventory-1", report["verify_only_skipped"])

    def test_verification_does_not_modify_the_collected_evidence(self):
        before = (self.trial / "trace.json").read_bytes()
        self._run({1})
        self.assertEqual((self.trial / "trace.json").read_bytes(), before)

    def test_the_review_error_names_verify_only_as_the_way_out(self):
        (self.trial / "record.json").unlink(missing_ok=True)
        (self.trial / "trace.json").unlink(missing_ok=True)
        with self.assertRaisesRegex(RuntimeError, "--verify-only"):
            rb.run_baseline(
                root=self.root, tracks=["agent"],
                cases=["capability.inventory"], repeats={1},
            )

    def test_the_cli_exposes_the_flag(self):
        source = Path(rb.__file__).read_text(encoding="utf-8")
        self.assertIn('"--verify-only"', source)
        self.assertIn("verify_only=args.verify_only", source)


if __name__ == "__main__":
    unittest.main()
