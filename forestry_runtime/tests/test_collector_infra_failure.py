"""The collector must not fabricate a gradable trial when the Runtime is down.

This is the registry-level regression behind a real incident: the Runtime was not
listening, collection failed, and the collector still wrote a trace whose run_id
was ``None``. The verifier then produced a record whose ``trial_id`` was the empty
string, the scorecard rejected the whole run as a duplicate trial id, and the CLI
exited 1 -- which the viewer reported as "gate blocked" for what was really an
unreachable service. Eleven slots were poisoned this way in one run.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from evaluation.collect.api import RuntimeApiClient, collect_trial


ROOT = Path(__file__).resolve().parents[1]


class _UnreachableClient(RuntimeApiClient):
    """Fails the way a stopped Runtime does: connection refused on first use."""

    def __init__(self):
        super().__init__("http://127.0.0.1:1", "k" * 40, "evaluator", "chat-unreachable")

    def request(self, method, path, payload=None, **kwargs):
        raise RuntimeError("Runtime unavailable: [WinError 10061] connection refused")

    def upload(self, path):
        raise RuntimeError("Runtime unavailable: [WinError 10061] connection refused")


class UnreachableRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def configuration(self) -> dict:
        return {
            "code_snapshot": "test", "model_digest": "test",
            "prompt_snapshot": "test", "tools_snapshot": "test",
            "dataset_version": "test", "environment_snapshot": "test",
            "evaluator_version": "test", "sampling": {"fixed": True},
            "budgets": {"seconds": 1},
        }

    def collect(self) -> dict:
        fixture = ROOT / "evaluation" / "fixtures" / "forestry_ndvi" / "forest.tif"
        return collect_trial(
            client=_UnreachableClient(), case_id="forestry.ndvi", repeat=1,
            prompt="compute NDVI", fixture_files=[fixture],
            output=self.root / "trial", configuration=self.configuration(),
        )

    def test_collection_reports_infrastructure_failure(self):
        result = self.collect()
        self.assertEqual(result["status"], "infra_error")
        self.assertIsNone(result["trace"], "no trace means no gradable trial")
        self.assertIn("unavailable", result["error"])

    def test_no_trace_file_is_written(self):
        self.collect()
        self.assertFalse(
            (self.root / "trial" / "trace.json").is_file(),
            "a trace with no run_id is what poisoned the records",
        )

    def test_the_cause_is_preserved_for_the_report(self):
        self.collect()
        error_file = self.root / "trial" / "raw" / "collector_error.txt"
        self.assertTrue(error_file.is_file())
        self.assertIn("10061", error_file.read_text(encoding="utf-8"))

    def test_an_infrastructure_record_carries_a_unique_trial_id(self):
        """The runner marks such a slot with a real id, not an empty string."""
        from evaluation.run_baseline import _infrastructure_record

        first = _infrastructure_record("forestry.ndvi", 1, self.configuration(), "boom")
        second = _infrastructure_record("forestry.ndvi", 1, self.configuration(), "boom")
        self.assertEqual(first["status"], "infra_error")
        self.assertTrue(first["trial_id"].strip())
        self.assertNotEqual(first["trial_id"], second["trial_id"])
        self.assertEqual(first["checks"], {}, "no check may be claimed")
        self.assertIn("boom", first["infrastructure_error"])

        # The scorecard must accept it as unknown rather than rejecting the run.
        from evaluation.scorecard import scorecard

        suite = {
            "version": "forestry-eval-0.1",
            "repeats": {"agent": 3, "engineering": 1, "ui": 1},
            "agent_groups": ["forestry"],
            "cases": [{
                "id": "forestry.ndvi", "track": "agent", "group": "forestry",
                "checks": {"pixels": "x"},
            }],
        }
        report = scorecard(suite, [first], self.root)
        self.assertEqual(report["cases"][0]["unknown"], 3)
        self.assertEqual(report["cases"][0]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
