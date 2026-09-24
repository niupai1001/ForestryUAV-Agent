"""Independent collector and verifier contract tests."""

from __future__ import annotations

from contextlib import closing
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import evaluation.run_baseline as run_baseline
from evaluation.collect.engineering import inspect_idempotency
from evaluation.collect.records import assemble
from evaluation.collect.trace import failure_category, normalize_trace
from evaluation.rules import SCORING_RULES_VERSION
from evaluation.scorecard import CONFIG_FIELDS
from evaluation.verify.csv import compare_by_business_key
from evaluation.verify.gates import exactly_once
from evaluation.verify.text import claims_match_table
from evaluation.verify.ui_trial import CASES as UI_CASES, verify_trial as verify_ui_trial


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evaluation" / "fixtures" / "gold" / "core_csv.json"


class EvaluationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_collectors_and_verifiers_do_not_import_runtime(self):
        code = (
            "import sys; "
            "import evaluation.collect.api,evaluation.collect.engineering,"
            "evaluation.collect.trace,evaluation.verify.csv,evaluation.verify.gates; "
            "raise SystemExit(1 if any(x=='runtime' or x.startswith('runtime.') for x in sys.modules) else 0)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_trace_preserves_ids_metadata_latency_and_failure_taxonomy(self):
        trace = normalize_trace(
            events=[
                {"type": "tool_start", "seq": 2, "action_id": "a1", "attempt_id": "p1",
                 "turn_id": "t1", "name": "fs_read", "arguments": {"path": "x"},
                 "spec": {"equivalent": "read", "side_effect": "none", "scope": {"reads": ["workspace"]}}},
                {"type": "tool_end", "action_id": "a1", "outcome_ok": False,
                 "duration_seconds": 0.125, "result": {"failure": {
                     "stage": "preconditions", "code": "source_path_not_found"
                 }}},
            ],
            run={"id": "r1", "state": "failed"}, case_id="core.csv", repeat=1,
            configuration={"version": "test"},
            turns=[{"id": "t1", "agent_run_id": "g1", "checkpoint_state": "complete"}],
        )
        self.assertEqual(trace["steps"][0]["attempt_id"], "p1")
        self.assertEqual(trace["steps"][0]["latency_ms"], 125)
        self.assertEqual(trace["steps"][0]["failure_category"], "path_grounding")
        self.assertEqual(failure_category({"stage": "unmapped", "code": "x"}), "unknown")

    def test_trace_keeps_empty_control_and_job_failure_as_distinct_observations(self):
        trace = normalize_trace(
            events=[
                {"type": "tool_start", "action_id": "empty", "name": "inspect_uav_source"},
                {"type": "tool_end", "action_id": "empty", "result": {
                    "ok": True, "outcome": "empty", "control_verified": None,
                    "data": {"code": "no_matching_images", "control_verified": None},
                }},
                {"type": "tool_start", "action_id": "job", "name": "job_status"},
                {"type": "tool_end", "action_id": "job", "result": {
                    "ok": True, "data": {"state": "failed", "job_failure": {
                        "reason": "missing_env", "code": "missing_shared_library",
                    }},
                }},
            ],
            run={"id": "r1", "state": "completed"}, case_id="test", repeat=1,
            configuration={},
        )
        self.assertEqual(trace["steps"][0]["outcome"], "empty")
        self.assertIsNone(trace["steps"][0]["control_verified"])
        self.assertEqual(trace["steps"][1]["job_failure_reason"], "missing_env")
        self.assertEqual(trace["steps"][1]["status"], "success")

    def test_csv_verifier_checks_keys_types_nulls_and_values(self):
        actual = self.root / "plot_summary.csv"
        actual.write_text(
            "plot_id,total_tree_records,observed_height_count,missing_height_count,mean_height_m\n"
            "P-03,1,0,1,\nP-01,3,2,1,11.0\nP-02,2,2,0,9.0000001\n",
            encoding="utf-8",
        )
        report = self.root / "report.json"
        self.assertEqual(compare_by_business_key(actual, GOLD, report=report).verdict, "pass")
        actual.write_text(
            "plot_id,total_tree_records,observed_height_count,missing_height_count,mean_height_m\n"
            "P-01,3,2,1,11\nP-02,2,2,0,9\nP-03,1,0,1,0\n",
            encoding="utf-8",
        )
        verdict = compare_by_business_key(actual, GOLD, report=report)
        self.assertEqual(verdict.verdict, "fail")
        self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["mismatches"])

    def test_answer_verifier_requires_and_checks_structured_claims(self):
        report = self.root / "answer.json"
        answer = "完成。```json\n{\"total_tree_records\":6,\"missing_height_count\":2,\"plot_count\":3,\"missing_values_excluded_from_mean\":true,\"missing_values_treated_as_zero\":false}\n```"
        self.assertEqual(claims_match_table(answer=answer, gold=GOLD, report=report).verdict, "pass")
        self.assertEqual(claims_match_table(answer="完成", gold=GOLD, report=report).verdict, "unknown")

    def test_case_declarations_exactly_match_suite_checks(self):
        """Every registered case, not just the first one.

        Checking a single case meant a new case could ship with check names that no
        verifier implements -- the declaration looked fine and every Run of that case
        scored ``unknown``.
        """
        from evaluation.cases.registry import AGENT_TRIALS, CASES

        suite = json.loads((ROOT / "evaluation" / "suite.json").read_text(encoding="utf-8"))
        contracts = {item["id"]: item for item in suite["cases"]}
        self.assertTrue(CASES, "no case is registered")
        for case_id, case in sorted(CASES.items()):
            with self.subTest(case=case_id):
                contract = contracts.get(case_id)
                self.assertIsNotNone(contract, f"{case_id} is absent from suite.json")
                self.assertEqual(set(case.checks), set(contract["checks"]))
                self.assertIn(case_id, AGENT_TRIALS, "an agent case needs a trial verifier")
                self.assertTrue(
                    (ROOT / case.fixture).is_dir(), f"{case_id}'s fixture is missing"
                )
                self.assertTrue((ROOT / case.gold).is_file(), f"{case_id}'s gold is missing")
                for check, verifier in case.checks.items():
                    with self.subTest(case=case_id, check=check):
                        module = importlib.import_module(verifier.module)
                        self.assertTrue(
                            callable(getattr(module, verifier.function, None)),
                            f"{verifier.module}.{verifier.function} is missing",
                        )

    def test_sqlite_snapshot_and_gate_verifier(self):
        database = self.root / "probe.sqlite3"
        with closing(sqlite3.connect(database)) as conn:
            conn.executescript("""
                CREATE TABLE runs(id TEXT PRIMARY KEY);
                CREATE TABLE inputs(id TEXT,owner TEXT,chat_id TEXT,run_id TEXT);
                CREATE TABLE turns(id TEXT,run_id TEXT);
                CREATE TABLE actions(id TEXT,run_id TEXT,turn_id TEXT);
                CREATE TABLE attempts(id TEXT,action_id TEXT,run_id TEXT,turn_id TEXT);
                CREATE TABLE job_refs(job_id TEXT,run_id TEXT,action_id TEXT,turn_id TEXT);
                INSERT INTO runs VALUES('run1');
                INSERT INTO inputs VALUES('input_gate_once','owner','chat','run1');
                INSERT INTO turns VALUES('turn1','run1');
                INSERT INTO actions VALUES('action_gate_once','run1','turn1');
                INSERT INTO attempts VALUES('attempt1','action_gate_once','run1','turn1');
                INSERT INTO job_refs VALUES('job_gate_once','run1','action_gate_once','turn1');
            """)
            conn.commit()
        snapshot = inspect_idempotency(database)
        self.assertTrue(all(value == 0 for value in snapshot["violations"].values()))
        evidence = self.root / "trial"
        (evidence / "raw").mkdir(parents=True)
        (evidence / "raw" / "sqlite.json").write_text(json.dumps(snapshot), encoding="utf-8")
        (evidence / "raw" / "pytest.json").write_text(json.dumps({"returncode": 0}), encoding="utf-8")
        verdict = exactly_once(evidence_root=evidence, report=evidence / "verifier.json")
        self.assertEqual(verdict.verdict, "pass")

    def test_records_are_rebased_and_cannot_escape_package(self):
        trial = self.root / "trial-1"
        trial.mkdir()
        (trial / "proof.json").write_text("{}", encoding="utf-8")
        record = {
            "status_evidence": {"evidence": ["proof.json"]},
            "checks": {"x": {"evidence": ["proof.json"]}},
        }
        path = trial / "record.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        output = self.root / "records.json"
        assembled = assemble([path], output)
        self.assertEqual(assembled[0]["checks"]["x"]["evidence"], ["trial-1/proof.json"])
        path.write_text(json.dumps([record, record]), encoding="utf-8")
        self.assertEqual(len(assemble([path], output)), 2)
        record["checks"]["x"]["evidence"] = ["../outside.json"]
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ValueError):
            assemble([path], output)

    def test_ui_verifier_requires_real_passing_playwright_results(self):
        trial = self.root / "ui"
        trial.mkdir()
        specs = []
        for contract in UI_CASES.values():
            specs.append({
                "title": contract["title"], "ok": True, "id": contract["title"],
                "tests": [{"results": [{"status": "passed"}]}],
            })
        (trial / "playwright-report.json").write_text(json.dumps({
            "suites": [{"specs": specs}],
        }), encoding="utf-8")
        records = verify_ui_trial(trial, {"snapshot": "test"}, 1)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(
            check["verdict"] == "pass"
            for record in records for check in record["checks"].values()
        ))
        specs[0]["tests"] = []
        (trial / "playwright-report.json").write_text(json.dumps({
            "suites": [{"specs": specs}],
        }), encoding="utf-8")
        failed = verify_ui_trial(trial, {"snapshot": "test"}, 1)
        self.assertTrue(all(
            check["verdict"] == "fail" for check in failed[0]["checks"].values()
        ))

    def test_run_baseline_is_callable_and_resumes_canonical_trial_path(self):
        baseline = self.root / "baseline"
        trial = baseline / "gate-idempotency-1"
        trial.mkdir(parents=True)
        configuration = {
            "code_snapshot": "test", "model_digest": "n/a",
            "prompt_snapshot": "n/a", "tools_snapshot": "test",
            "dataset_version": "test", "environment_snapshot": "test",
            "evaluator_version": "test", "sampling": {"fixed": True},
            "budgets": {"seconds": 1},
        }
        (baseline / "configuration-engineering.json").write_text(
            json.dumps(configuration), encoding="utf-8"
        )
        (trial / "proof.json").write_text("{}", encoding="utf-8")
        record = {
            "suite_version": "forestry-eval-0.1",
            "scoring_rules_version": SCORING_RULES_VERSION,
            "case_id": "gate.idempotency", "track": "engineering",
            "execution": "engineering", "repeat": 1,
            "trial_id": "resume-test", "configuration": configuration,
            "status": "evaluated",
            "status_evidence": {"verifier": "test", "evidence": ["proof.json"]},
            "checks": {"exactly_once": {
                "verdict": "pass", "verifier": "test", "evidence": ["proof.json"],
            }},
        }
        record_file = trial / "record.json"
        record_file.write_text(json.dumps(record), encoding="utf-8")
        original = record_file.read_bytes()

        report = run_baseline.run_baseline(
            root=baseline, tracks=["engineering"],
            cases=["gate.idempotency"], repeats={1},
        )

        self.assertEqual(report["qualification"], "incomplete")
        self.assertEqual(record_file.read_bytes(), original)
        assembled = json.loads((baseline / "records.json").read_text(encoding="utf-8"))
        self.assertEqual(
            assembled[0]["checks"]["exactly_once"]["evidence"],
            ["gate-idempotency-1/proof.json"],
        )
        self.assertTrue((baseline / "scorecard.json").is_file())

    def test_run_baseline_discards_an_empty_trial_directory(self):
        """An interrupted attempt leaves an empty directory, which is not review work.

        Treating it as review work aborted the whole run, and because the abort
        happened before any scorecard was written, the viewer kept showing the
        previous run's gates -- which reads as "gate blocked" when nothing failed.
        The slot is re-collected instead.
        """
        baseline = self.root / "baseline"
        empty = baseline / "gate-permissions-1"
        empty.mkdir(parents=True)
        self.assertEqual(list(empty.iterdir()), [])
        (baseline / "configuration-engineering.json").write_text(
            json.dumps({field: "test" for field in CONFIG_FIELDS}), encoding="utf-8"
        )
        collected = {}

        def fake_collector(trial: Path, *, project_root: Path):
            collected["trial"] = trial
            (trial / "raw").mkdir(parents=True, exist_ok=True)
            (trial / "raw" / "pytest.json").write_text("{}", encoding="utf-8")

        def fake_verify(case_id: str, trial: Path, configuration: dict, repeat: int):
            (trial / "raw" / "permissions.json").write_text("{}", encoding="utf-8")
            return {
                "suite_version": "forestry-eval-0.1", "case_id": case_id,
                "scoring_rules_version": SCORING_RULES_VERSION,
                "track": "engineering", "execution": "engineering", "repeat": repeat,
                "trial_id": f"{case_id}-{repeat}", "configuration": configuration,
                "status": "evaluated",
                "status_evidence": {
                    "verifier": "stub", "evidence": ["raw/pytest.json"],
                },
                "checks": {"enforced": {
                    "verdict": "pass", "verifier": "stub",
                    "evidence": ["raw/permissions.json"],
                }},
            }

        with patch.dict(
            run_baseline.ENGINEERING_COLLECTORS,
            {"gate.permissions": fake_collector},
        ), patch.object(run_baseline, "_verify_gate", fake_verify):
            report = run_baseline.run_baseline(
                root=baseline, tracks=["engineering"],
                cases=["gate.permissions"], repeats={1},
            )

        self.assertEqual(collected["trial"], empty, "the same slot is re-collected")
        self.assertTrue((empty / "raw" / "pytest.json").is_file())

    def test_run_baseline_rejects_an_interrupted_trial_that_holds_evidence(self):
        """Evidence without a record means an interrupted verification, not garbage."""
        baseline = self.root / "baseline"
        partial = baseline / "gate-permissions-1"
        (partial / "raw").mkdir(parents=True)
        (partial / "raw" / "pytest.json").write_text("{}", encoding="utf-8")
        (baseline / "configuration-engineering.json").write_text(
            json.dumps({field: "test" for field in CONFIG_FIELDS}), encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "must be reviewed") as caught:
            run_baseline.run_baseline(
                root=baseline, tracks=["engineering"],
                cases=["gate.permissions"], repeats={1},
            )
        self.assertIn("--force", str(caught.exception), "the error must name the way out")
        self.assertTrue(
            (partial / "raw" / "pytest.json").is_file(),
            "recorded evidence must not be discarded",
        )

    def test_run_baseline_rejects_bom_configuration_before_collection(self):
        baseline = self.root / "baseline"
        baseline.mkdir()
        (baseline / "configuration-agent.json").write_bytes(b"\xef\xbb\xbf{}")
        with self.assertRaisesRegex(ValueError, "without BOM"):
            run_baseline.run_baseline(
                root=baseline, tracks=["agent"], cases=["core.csv"], repeats={1},
            )


if __name__ == "__main__":
    unittest.main()
