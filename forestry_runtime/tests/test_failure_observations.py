"""Failure observations must carry evidence without inventing a diagnosis."""

from pathlib import Path
import unittest

from evaluation.failure_coverage import scan
from runtime.capabilities.job_status.service import JobStatusCapability
from runtime.kernel.protocol import execution_failure
from runtime.workspace import SourcePathError
from shared.outcome import REASONS, failure_category, normalize_result


class FailureObservationTests(unittest.TestCase):
    def test_source_path_failure_preserves_scope_and_actionable_candidate(self):
        result = execution_failure(SourcePathError(
            "Use the observed directory spelling.",
            source_id="grant_" + "a" * 32,
            requested_path="1605 白桦",
            suggested_path="1605白桦",
            argument_key="path",
        ))
        failure = result["failure"]
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(failure["reason"], "not_found")
        self.assertEqual(failure["checked_scope"]["path"], "1605 白桦")
        self.assertEqual(failure["suggested_arguments"]["path"], "1605白桦")
        self.assertEqual(len(failure["candidates"]), 1)

    def test_unknown_execution_error_is_not_called_numeric_failure(self):
        result = execution_failure(RuntimeError("opaque backend failure"))
        self.assertEqual(result["failure"]["reason"], "unknown")
        self.assertEqual(failure_category(result["failure"]), "unknown")

    def test_missing_shared_library_is_environment_not_algorithm(self):
        result = execution_failure(ImportError(
            "libexpat.so.1: cannot open shared object file: No such file or directory"
        ))
        self.assertEqual(result["failure"]["reason"], "missing_env")
        self.assertEqual(result["failure"]["missing"][0]["name"], "libexpat.so.1")

    def test_failed_job_keeps_tool_observation_separate_from_environment_failure(self):
        job = JobStatusCapability()
        job._record_kind = lambda _job_id: "code"
        data = job._decorate("job_123", {
            "state": "failed", "terminal": True, "exit_code": 127,
            "output": "libexpat.so.1: cannot open shared object file: No such file or directory",
        })
        result = normalize_result({"ok": True, "data": data})
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(data["job_failure"]["reason"], "missing_env")
        self.assertEqual(data["job_failure"]["missing"][0]["name"], "libexpat.so.1")

    def test_oom_killed_job_is_named_rather_than_reported_as_unknown(self):
        # Exit 137 with an empty log is SIGKILL. Only the runtime that enforced
        # the limit knows why, so that flag -- not the code -- is the diagnosis.
        # Reported as `unknown`, a real Run retried the same algorithm until the
        # budget ran out instead of rewriting it.
        job = JobStatusCapability()
        job._record_kind = lambda _job_id: "code"
        data = job._decorate("job_123", {
            "state": "failed", "terminal": True, "exit_code": 137,
            "output": "",
            "oom_killed": True,
            "limits": {"memory_bytes": 16 * 2**30, "memory_human": "16.0 GiB"},
        })
        failure = data["job_failure"]
        self.assertEqual(failure["reason"], "out_of_memory")
        self.assertEqual(failure["code"], "container_memory_limit")
        self.assertEqual(failure["exit_code"], 137)
        self.assertEqual(failure["limit"]["memory_human"], "16.0 GiB")
        # The message has to say what to change, not just what happened.
        self.assertIn("内存上限", failure["message"])
        self.assertIn("不要原样重试", failure["message"])

    def test_exit_137_without_the_oom_flag_stays_unknown(self):
        # 137 is also what `docker stop` leaves behind. Classifying every 137 as
        # memory would be the same invention in the other direction.
        job = JobStatusCapability()
        job._record_kind = lambda _job_id: "code"
        data = job._decorate("job_123", {
            "state": "failed", "terminal": True, "exit_code": 137,
            "output": "", "oom_killed": False,
        })
        self.assertEqual(data["job_failure"]["reason"], "unknown")

    def test_empty_result_retains_unverified_negative_conclusion(self):
        result = normalize_result({
            "ok": True, "outcome": "empty",
            "data": {"code": "no_local_peaks", "control_verified": None},
        })
        self.assertEqual(result["outcome"], "empty")
        self.assertIsNone(result["control_verified"])

    def test_coverage_reports_unknown_instead_of_guessing(self):
        report = scan(Path(__file__).resolve().parents[1])
        self.assertGreater(report["total"], 0)
        self.assertEqual(report["total"], len(report["points"]))
        self.assertTrue(all(point["reason"] in REASONS for point in report["points"]))
        self.assertGreater(report["by_diagnosis_source"].get("unknown", 0), 0)


if __name__ == "__main__":
    unittest.main()
