"""Independent collector and verifier contract tests."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from evaluation.cases.core_csv import CASE
from evaluation.collect.engineering import inspect_idempotency
from evaluation.collect.records import assemble
from evaluation.collect.trace import failure_category, normalize_trace
from evaluation.verify.csv import compare_by_business_key
from evaluation.verify.gates import exactly_once
from evaluation.verify.text import claims_match_table


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

    def test_case_declaration_exactly_matches_suite_checks(self):
        suite = json.loads((ROOT / "evaluation" / "suite.json").read_text(encoding="utf-8"))
        contract = next(item for item in suite["cases"] if item["id"] == CASE.id)
        self.assertEqual(set(CASE.checks), set(contract["checks"]))

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
        record["checks"]["x"]["evidence"] = ["../outside.json"]
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ValueError):
            assemble([path], output)


if __name__ == "__main__":
    unittest.main()
