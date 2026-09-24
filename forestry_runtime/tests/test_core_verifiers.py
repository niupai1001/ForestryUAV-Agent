"""Contract tests for the three core.* verifiers.

Every case here is graded by running the agent's own code, so the tests check both
directions: a real repair must pass, and the *unrepaired* script must not.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.verify.core_files import (  # noqa: E402
    artifact_texts, executed_snippets, failed_executions, observed_summaries,
    plot_summaries, recorded_job_outputs, reconstruct_files, run_snippet,
    summaries_match,
)
from evaluation.verify.core_paths_trial import verify_trial as verify_paths  # noqa: E402
from evaluation.verify.core_repair_trial import verify_trial as verify_repair  # noqa: E402
from evaluation.verify.paths import (  # noqa: E402
    _entries_from_result, answer_matches_listing, directory_scope_is_read_only,
    names_match_references,
)
from evaluation.verify.core_repair import (  # noqa: E402
    failure_was_observed, rerun_produced_new_result, source_was_not_modified,
)


FIXTURES = ROOT / "evaluation" / "fixtures"
GOLD = FIXTURES / "gold"

# A genuine repair: the mean of a plot with no observed height is absent, not zero.
FIXED_SCRIPT = '''import csv, json
from pathlib import Path

rows = list(csv.DictReader(Path("samples.csv").open(newline="", encoding="utf-8")))
plots = {}
for row in rows:
    plots.setdefault(row["plot_id"], []).append(
        float(row["height_m"]) if row["height_m"].strip() else None
    )
summary = []
for plot, values in sorted(plots.items()):
    observed = [value for value in values if value is not None]
    summary.append({
        "plot_id": plot,
        "tree_records": len(values),
        "observed_heights": len(observed),
        "missing_heights": len(values) - len(observed),
        "mean_height_m": sum(observed) / len(observed) if observed else None,
    })
Path("summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
print(json.dumps({row["plot_id"]: row["mean_height_m"] for row in summary}))
'''

# The agent's answer as it appears in the event stream.
PATH_ANSWER = (
    "盘点完成。\n```json\n"
    + json.dumps({
        "entries": [
            {"name": "1605白桦.tif", "type": "file"},
            {"name": "1605桦.tif", "type": "file"},
            {"name": "IMG_0001.JPG", "type": "file"},
            {"name": "IMG_0001.JPG.bak", "type": "file"},
            {"name": "IMG_0003.JPG", "type": "file"},
            {"name": "README", "type": "file"},
            {"name": "notes with spaces.txt", "type": "file"},
            {"name": "reference_after.JPG", "type": "file"},
            {"name": "reference_before.JPG", "type": "file"},
        ],
        "entry_count": 9,
    }, ensure_ascii=False)
    + "\n```"
)


def _step(tool, *, action_id="a1", status="success", **extra):
    step = {"tool": tool, "action_id": action_id, "status": status, "step": 1}
    step.update(extra)
    return step


class CoreFilesUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_the_recorded_trace_cannot_be_read_as_a_script(self):
        """A snippet that merely names the error is not a snippet that hit it.

        Searching the whole step matched the agent's own source text, so an agent that
        wrote ``except ZeroDivisionError`` scored as having observed the failure.
        """
        trace = {"steps": [
            _step("code_run", args_normalized={
                "language": "python",
                "code": "# guard against ZeroDivisionError\ntry:\n    pass\nexcept: pass",
            }, result_excerpt={"ok": True}),
        ]}
        self.assertEqual(failed_executions(trace, error_type="ZeroDivisionError"), [])
        trace["steps"][0]["result_excerpt"] = {
            "exit_code": 1, "stderr": "ZeroDivisionError: division by zero",
        }
        self.assertEqual(len(failed_executions(trace, error_type="ZeroDivisionError")), 1)

    def test_edits_are_replayed_with_literal_replacement_semantics(self):
        trace = {"steps": [
            _step("fs_write", args_normalized={"path": "broken.py", "content": "aXb"}),
            _step("fs_edit", args_normalized={"path": "broken.py", "old": "X", "new": "Y"}),
            _step("fs_edit", args_normalized={"path": "broken.py", "old": "ZZ", "new": "!"}),
            _step("fs_write", args_normalized={"path": "x.py", "content": "z"}, status="failure"),
        ]}
        self.assertEqual(reconstruct_files(trace), {"broken.py": "aYb"})

    def test_snippets_are_recorded_from_both_execution_and_written_files(self):
        trace = {"steps": [
            _step("code_run", args_normalized={"language": "python", "code": "print(1)"}),
            _step("code_run", args_normalized={"language": "shell", "code": "ls"}),
            _step("fs_write", args_normalized={"path": "helper.py", "content": "print(2)"}),
            _step("fs_write", args_normalized={"path": "notes.txt", "content": "print(3)"}),
        ]}
        origins = [(item["origin"], item["path"]) for item in executed_snippets(trace)]
        self.assertEqual(origins, [("code_run", None), ("fs_write", "helper.py")])

    def test_summary_reader_accepts_the_declared_schema_and_rejects_prose(self):
        rows = [{"plot_id": "P-01", "mean_height_m": "11.0"}]
        self.assertEqual(plot_summaries(rows)["P-01"]["mean_height_m"], 11.0)
        self.assertEqual(plot_summaries({"P-01": {"mean_height_m": 11}}), {
            "P-01": {"mean_height_m": 11.0},
        })
        self.assertIsNone(plot_summaries("done"))
        self.assertIsNone(plot_summaries([{"height": 1}]))

    def test_a_missing_mean_is_not_compared_as_zero(self):
        """An absent mean may be omitted or null, but it may never be reported as 0.0.

        The agent's script writes ``null`` for P-03; a summary that simply leaves the
        key out says the same thing. Inventing ``0.0`` for a plot with no observed
        height is a different claim about the data, so it fails.
        """
        expected = {"P-03": {"mean_height_m": None, "tree_records": 1}}
        matched, problems = summaries_match(
            {"P-03": {"mean_height_m": 0.0, "tree_records": 1}}, expected
        )
        self.assertFalse(matched)
        self.assertEqual(problems, ["P-03.mean_height_m: expected empty, got 0.0"])
        matched, problems = summaries_match({"P-03": {"tree_records": 1}}, expected)
        self.assertTrue(matched, problems)
        matched, problems = summaries_match(
            {"P-03": {"mean_height_m": None, "tree_records": 1}}, expected
        )
        self.assertTrue(matched, problems)

    def test_a_required_value_cannot_be_omitted(self):
        expected = {"P-01": {"mean_height_m": 11.0}}
        matched, problems = summaries_match({"P-01": {}}, expected)
        self.assertFalse(matched)
        self.assertEqual(problems, ["P-01.mean_height_m: missing"])

    def test_the_scoring_host_never_executes_model_generated_code(self):
        """The harness must not run the candidate's program in its own process.

        ``run_snippet`` used to do exactly that, with the harness process's own
        privileges, on the scoring path for two capability cases. Grading evidence
        now comes from the recorded execution result and the delivered artifacts.
        """
        broken = (FIXTURES / "core_repair" / "broken.py").read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError) as caught:
            run_snippet(broken, case_dir=FIXTURES / "core_repair")
        message = str(caught.exception)
        self.assertIn("must not execute model-generated code", message)
        self.assertIn("recorded execution result", message)

    def test_the_module_carries_no_subprocess_or_exec_path(self):
        import evaluation.verify.core_files as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess.run", "subprocess.Popen", "os.system("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_recorded_job_outputs_are_read_from_the_trace(self):
        """Job stdout/stderr as the Runtime recorded it is first-hand evidence."""
        job_id = "job_" + "a" * 32
        trace = {"steps": [
            _step("code_run", args_normalized={"language": "python", "code": "print(1)"},
                  result_excerpt={"ok": True, "data": {
                      "job_id": job_id, "state": "running"}}),
            _step(
                "job_wait",
                result_excerpt={
                    "ok": True,
                    "data": {
                        "job_id": job_id, "state": "succeeded",
                        "terminal": True,
                        "output": '[{"plot_id": "P-01", "mean_height_m": 11.0}]',
                    },
                },
            ),
            _step("job_wait", status="failed", result_excerpt={"output": "ignored"}),
        ]}
        outputs = recorded_job_outputs(trace)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["job_id"], job_id)
        self.assertEqual(outputs[0]["state"], "succeeded")
        found = observed_summaries(outputs, [])
        self.assertEqual(found[0]["summary"]["P-01"]["mean_height_m"], 11.0)
        self.assertIn("job_wait", found[0]["origin"])

    def test_an_observation_of_a_job_the_run_never_started_is_ignored(self):
        """Otherwise a stray observation would be graded as this Run's result."""
        trace = {"steps": [
            _step("job_wait", result_excerpt={"ok": True, "data": {
                "job_id": "job_" + "e" * 32, "state": "succeeded",
                "terminal": True,
                "output": '[{"plot_id": "P-01", "mean_height_m": 11.0}]',
            }}),
        ]}
        self.assertEqual(recorded_job_outputs(trace), [])

    def test_delivered_artifacts_are_read_as_text(self):
        root = Path(self.temp.name) / "artifacts"
        root.mkdir(parents=True)
        (root / "asset_abc-summary.json").write_text(
            '[{"plot_id": "P-02", "mean_height_m": 12.5}]', encoding="utf-8"
        )
        (root / "asset_def-thumb.png").write_bytes(b"\x89PNG\x00\xff")
        texts = artifact_texts(root)
        self.assertEqual([item["name"] for item in texts], ["asset_abc-summary.json"])
        found = observed_summaries([], texts)
        self.assertEqual(found[0]["summary"]["P-02"]["mean_height_m"], 12.5)
        self.assertTrue(found[0]["origin"].startswith("artifact:"))

    def test_prose_alone_is_never_evidence(self):
        """A number the agent claims in its answer must not grade as a result."""
        trace = {"steps": [
            _step("code_run", result_excerpt={"ok": True, "data": {
                "job_id": "job_" + "b" * 32, "state": "running", "output": "",
            }}),
        ]}
        self.assertEqual(observed_summaries(recorded_job_outputs(trace), []), [])


class CorePathsVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trial = Path(self.temp.name) / "core-paths-1"
        self.trial.mkdir()
        self.gold = GOLD / "core_paths.json"

    def _write_events(self, content=PATH_ANSWER):
        (self.trial / "raw").mkdir(exist_ok=True)
        (self.trial / "raw" / "events.json").write_text(
            json.dumps([{"type": "message", "content": content}], ensure_ascii=False),
            encoding="utf-8",
        )

    def test_the_frozen_listing_matches_the_fixture_on_disk(self):
        contract = json.loads(self.gold.read_text(encoding="utf-8"))
        on_disk = sorted(
            path.name for path in (FIXTURES / "core_paths").iterdir()
            if path.name != "prompt.txt"
        )
        self.assertEqual([item["name"] for item in contract["entries"]], on_disk)
        self.assertEqual(contract["entry_count"], len(on_disk))

    def test_listing_check_compares_names_exactly(self):
        trace = {"steps": [_step("fs_list", result_excerpt={"entries": [
            {"name": name, "type": "file"}
            for name in (
                "IMG_0001.JPG", "IMG_0001.JPG.bak", "IMG_0003.JPG",
                "reference_after.JPG", "reference_before.JPG", "1605白桦.tif",
                "1605桦.tif", "notes with spaces.txt", "README",
            )
        ]})]}
        self.assertEqual(
            names_match_references(trace=trace, gold=self.gold,
                                   report=self.trial / "references-verifier.json").verdict,
            "pass",
        )
        # One character apart: the directory's whole reason for existing.
        trace["steps"][0]["result_excerpt"]["entries"][5]["name"] = "1605 白桦.tif"
        verdict = names_match_references(
            trace=trace, gold=self.gold, report=self.trial / "references-verifier.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        report = json.loads((self.trial / "references-verifier.json").read_text(encoding="utf-8"))
        self.assertIn("1605白桦.tif", report["unobserved"])
        self.assertIn("1605 白桦.tif", report["unexpected"])

    def test_the_real_fs_list_shape_is_understood(self):
        """`fs_list` nests its rows under `items` inside a `data` envelope.

        A parser that only looked at the top level found nothing on a *successful*
        call, so a real core.paths run reported ``references: unknown`` even though its
        answer listed the directory perfectly. The shape below is copied from that run.
        """
        real = {"ok": True, "data": {
            "items": [
                {"name": "IMG_0001.JPG", "path": "IMG_0001.JPG", "type": "file",
                 "size": 22, "modified": 1789977798.7, "version": None},
                {"name": ".runtime", "path": ".runtime", "type": "directory",
                 "size": None, "modified": 1789977798.7, "version": None},
            ],
            "page": 1, "page_size": 50, "total": 2, "has_more": False,
        }}
        entries = _entries_from_result(real)
        self.assertEqual([item["name"] for item in entries], ["IMG_0001.JPG", ".runtime"])
        self.assertEqual([item["type"] for item in entries], ["file", "directory"])

    def test_a_reported_kind_that_contradicts_disk_fails(self):
        names = [item["name"] for item in json.loads(
            self.gold.read_text(encoding="utf-8"))["entries"]]
        trace = {"steps": [_step("fs_list", result_excerpt={"data": {"items": [
            {"name": name, "type": "directory" if index == 0 else "file"}
            for index, name in enumerate(names)
        ]}})]}
        verdict = names_match_references(
            trace=trace, gold=self.gold, report=self.trial / "references-verifier.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        report = json.loads((self.trial / "references-verifier.json").read_text(encoding="utf-8"))
        self.assertTrue(report["wrong_kind"])

    def test_a_successful_listing_of_something_else_is_not_a_match(self):
        """Listing only `.runtime` observed the directory without corroborating the answer."""
        trace = {"steps": [_step("fs_list", result_excerpt={
            "ok": True, "data": {"items": [{"name": ".runtime", "type": "directory"}]},
        })]}
        verdict = names_match_references(
            trace=trace, gold=self.gold, report=self.trial / "references-verifier.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("never returned by a successful tool call", verdict.detail)

    def test_the_request_is_not_evidence(self):
        """The filenames are in the prompt, so an agent can answer without observing.

        This is the real core.paths run: a perfect inventory, its only successful
        Action listing `.runtime`. Crediting the answer as its own evidence is exactly
        the mistake this split exists to prevent.
        """
        trace = {"steps": [
            _step("fs_list", result_excerpt={
                "ok": True, "data": {"items": [{"name": ".runtime", "type": "directory"}]},
            }),
        ]}
        verdict = names_match_references(
            trace=trace, gold=self.gold, report=self.trial / "references-verifier.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        report = json.loads((self.trial / "references-verifier.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["unobserved"]), 9)

    def test_reading_an_attachment_counts_as_observing_it(self):
        """`fs_read` of an uploaded asset is as real as listing it."""
        names = [item["name"] for item in json.loads(
            self.gold.read_text(encoding="utf-8"))["entries"]]
        trace = {"steps": [
            _step("fs_read", action_id=f"a{index}", result_excerpt={
                "ok": True, "data": {"text": True, "content": f"contents of {name}"},
            })
            for index, name in enumerate(names)
        ]}
        verdict = names_match_references(
            trace=trace, gold=self.gold, report=self.trial / "references-verifier.json"
        )
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_a_failed_observation_does_not_count(self):
        names = [item["name"] for item in json.loads(
            self.gold.read_text(encoding="utf-8"))["entries"]]
        trace = {"steps": [
            _step("fs_read", status="failure", result_excerpt={
                "ok": False, "error": "path does not exist",
            }),
            _step("fs_read", action_id="ok", result_excerpt={
                "ok": True, "data": {"content": " ".join(names)},
            }),
        ]}
        verdict = names_match_references(
            trace=trace, gold=self.gold, report=self.trial / "references-verifier.json"
        )
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_listing_check_is_unknown_without_an_observation(self):
        verdict = names_match_references(
            trace={"steps": []}, gold=self.gold,
            report=self.trial / "references-verifier.json",
        )
        self.assertEqual(verdict.verdict, "unknown")

    def test_scope_check_fails_on_any_mutation(self):
        read_only = {"steps": [
            _step("fs_list", side_effect="none", args_normalized={"path": "."}),
            _step("fs_read", side_effect="none", args_normalized={"path": "README"}),
        ]}
        self.assertEqual(
            directory_scope_is_read_only(
                trace=read_only, report=self.trial / "scope-verifier.json").verdict,
            "pass",
        )
        for mutation in (
            _step("fs_write", side_effect="file_write", args_normalized={"path": "out.txt"}),
            _step("code_run", side_effect="durable_job", args_normalized={"code": "x"}),
        ):
            verdict = directory_scope_is_read_only(
                trace={"steps": [read_only["steps"][0], mutation]},
                report=self.trial / "scope-verifier.json",
            )
            self.assertEqual(verdict.verdict, "fail", mutation["tool"])

    def test_scope_check_fails_on_a_path_that_leaves_the_directory(self):
        verdict = directory_scope_is_read_only(
            trace={"steps": [_step("fs_read", side_effect="none",
                                   args_normalized={"path": "../secrets.txt"})]},
            report=self.trial / "scope-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")

    def test_answer_check_requires_the_inventory_not_a_summary(self):
        self._write_events()
        self.assertEqual(
            answer_matches_listing(
                trial=self.trial, gold=self.gold, report=self.trial / "answer-verifier.json"
            ).verdict,
            "pass",
        )
        self._write_events("我盘点了这个目录，一共有九个文件。")
        self.assertEqual(
            answer_matches_listing(
                trial=self.trial, gold=self.gold, report=self.trial / "answer-verifier.json"
            ).verdict,
            "unknown",
        )
        self._write_events(PATH_ANSWER.replace("1605桦.tif", "1605桦副本.tif"))
        self.assertEqual(
            answer_matches_listing(
                trial=self.trial, gold=self.gold, report=self.trial / "answer-verifier.json"
            ).verdict,
            "fail",
        )

    def test_trial_record_declares_the_three_contracted_checks(self):
        trace = {
            "run_id": "run-paths", "repeat": 1, "configuration": {"version": "t"},
            "terminal_state": "completed", "checkpoint_state": "complete",
            "steps": [_step("fs_list", side_effect="none", result_excerpt={"entries": [
                {"name": item["name"], "type": "file"} for item in json.loads(
                    self.gold.read_text(encoding="utf-8"))["entries"]
            ]})],
        }
        (self.trial / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
        self._write_events()
        record = verify_paths(self.trial, {"version": "t"}, 1, gold=self.gold)
        self.assertEqual(record["status"], "evaluated")
        self.assertEqual(set(record["checks"]), {"scope", "references", "answer"})
        self.assertTrue(all(
            check["verdict"] == "pass" for check in record["checks"].values()
        ), record["checks"])


class CoreRepairVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gold = GOLD / "core_repair.json"

    def _summary_output(self, gold: Path | None = None) -> str:
        contract = json.loads((gold or self.gold).read_text(encoding="utf-8"))
        rows = [
            {"plot_id": plot, **fields}
            for plot, fields in contract["expected_summary"].items()
        ]
        return json.dumps(rows, ensure_ascii=False)

    def _trial(self, *, case_id="core.repair", code=FIXED_SCRIPT,
               write_path="summary.json", answer=None, job_output=None,
               job_state="succeeded"):
        """Build one evidence package from the recorded evidence only.

        The delivered result is the runtime job's own recorded stdout -- the
        harness never runs the agent's program to find out what it computed.
        """
        trial = self.root / "trial"
        if trial.exists():
            shutil.rmtree(trial)
        trial.mkdir()
        (trial / "raw").mkdir()
        trace = {
            "run_id": f"run-{case_id}", "repeat": 1, "configuration": {"version": "t"},
            "terminal_state": "completed", "checkpoint_state": "complete",
            "steps": [
                _step("code_run", action_id="a1", args_normalized={
                    "language": "python", "code": "import broken",
                }, result_excerpt={"exit_code": 1,
                                   "stderr": "ZeroDivisionError: division by zero"}),
                _step("code_run", action_id="a2", args_normalized={
                    "language": "python", "code": code,
                }, result_excerpt={"exit_code": 0, "data": {
                    "job_id": "job_" + "c" * 32, "state": "running",
                }}),
                _step("job_wait", action_id="a4", result_excerpt={"ok": True, "data": {
                    "job_id": "job_" + "c" * 32, "state": job_state,
                    "terminal": True, "exit_code": 0 if job_state == "succeeded" else 1,
                    "output": (
                        self._summary_output() if job_output is None else job_output
                    ),
                }}),
            ],
        }
        if write_path:
            trace["steps"].append(_step(
                "fs_write", action_id="a3",
                args_normalized={"path": write_path, "content": code},
            ))
        (trial / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
        (trial / "raw" / "events.json").write_text(json.dumps([{
            "type": "message",
            "content": answer or (
                "已修复。```json\n"
                + json.dumps({"observed_error": "ZeroDivisionError", "fixed": True,
                              "plot_count": 3, "output_file": "summary.json"})
                + "\n```"
            ),
        }], ensure_ascii=False), encoding="utf-8")
        return trial

    def test_a_real_repair_passes_every_check(self):
        trial = self._trial()
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.repair",
                               gold=self.gold)
        self.assertEqual(record["status"], "evaluated")
        self.assertEqual(set(record["checks"]), {"failure_observed", "repair", "delivery"})
        self.assertEqual(
            {name: check["verdict"] for name, check in record["checks"].items()},
            {"failure_observed": "pass", "repair": "pass", "delivery": "pass"},
        )

    def test_the_scored_summary_is_the_one_the_run_actually_printed(self):
        """Grading reads the recorded job output, not a replayed computation."""
        trial = self._trial(job_output=json.dumps(
            [{"plot_id": "P-01", "mean_height_m": 999.0}], ensure_ascii=False
        ))
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.repair",
                               gold=self.gold)
        self.assertEqual(record["checks"]["repair"]["verdict"], "fail")
        report = json.loads(
            (trial / "repair-verifier.json").read_text(encoding="utf-8")
        )
        self.assertIn("recorded execution results", report["graded_from"])
        self.assertTrue(report["attempts"][0]["origin"].startswith("job_wait"))

    def test_an_unrepaired_script_fails_even_though_it_was_executed(self):
        """The bug the fixture plants: treating an absent mean as zero."""
        unpatched = json.dumps([
            {"plot_id": "P-01", "tree_records": 3, "observed_heights": 2,
             "missing_heights": 1, "mean_height_m": 11.0},
            {"plot_id": "P-02", "tree_records": 2, "observed_heights": 2,
             "missing_heights": 0, "mean_height_m": 9.0},
            {"plot_id": "P-03", "tree_records": 1, "observed_heights": 0,
             "missing_heights": 1, "mean_height_m": 0.0},
        ], ensure_ascii=False)
        trial = self._trial(job_output=unpatched)
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.repair",
                               gold=self.gold)
        self.assertEqual(record["checks"]["repair"]["verdict"], "fail")
        self.assertIn("P-03", record["checks"]["repair"]["detail"])

    def test_a_claim_without_execution_is_not_a_repair(self):
        """A job observation whose job this Run never started is not its result."""
        trial = self._trial()
        trace = json.loads((trial / "trace.json").read_text(encoding="utf-8"))
        trace["steps"] = [step for step in trace["steps"] if step["tool"] != "code_run"]
        (trial / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.repair",
                               gold=self.gold)
        self.assertEqual(record["checks"]["failure_observed"]["verdict"], "fail")
        self.assertEqual(record["checks"]["repair"]["verdict"], "unknown")
        self.assertIn("cannot be observed", record["checks"]["repair"]["detail"])

    def test_prose_only_output_does_not_grade_as_a_result(self):
        """A summary the agent merely asserts in its answer is not evidence."""
        trial = self._trial(answer=(
            "修复完成，结果是 ```json\n"
            + self._summary_output()
            + "\n```"
        ), job_output="修复完成")
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.repair",
                               gold=self.gold)
        self.assertEqual(record["checks"]["repair"]["verdict"], "unknown")
        self.assertIn("cannot be observed", record["checks"]["repair"]["detail"])

    def test_delivery_requires_machine_readable_claims(self):
        trial = self._trial(answer="我已经修好了这个脚本，现在可以正常运行。")
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.repair",
                               gold=self.gold)
        self.assertEqual(record["checks"]["delivery"]["verdict"], "unknown")
        self.assertEqual(record["status"], "evaluated")

    def test_a_failed_run_is_a_crash_not_an_untested_slot(self):
        trial = self._trial()
        trace = json.loads((trial / "trace.json").read_text(encoding="utf-8"))
        trace["terminal_state"] = "failed"
        (trial / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.repair",
                               gold=self.gold)
        self.assertEqual(record["status"], "crash")

    def test_a_mismatched_slot_is_refused(self):
        trial = self._trial()
        with self.assertRaisesRegex(ValueError, "does not match"):
            verify_repair(trial, {"version": "other"}, 1, case_id="core.repair",
                          gold=self.gold)

    def test_failure_check_wants_the_specific_error(self):
        trial = self._trial()
        trace = json.loads((trial / "trace.json").read_text(encoding="utf-8"))
        verdict = failure_was_observed(
            trace=trace, gold=self.gold, report=trial / "failure-verifier.json"
        )
        self.assertEqual(verdict.verdict, "pass")
        other = json.loads(self.gold.read_text(encoding="utf-8"))
        other["observed_error"] = "KeyError"
        path = self.root / "other-gold.json"
        path.write_text(json.dumps(other), encoding="utf-8")
        self.assertEqual(
            failure_was_observed(
                trace=trace, gold=path, report=trial / "failure-verifier.json"
            ).verdict,
            "fail",
        )


class CoreChangedInputVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gold = GOLD / "core_changed_input.json"
        self.fixture = FIXTURES / "core_changed_input"

    def _trial(self, *, second_run=True, mutate_source=False, workspace_copy=False,
               job_output=None):
        trial = self.root / "trial"
        if trial.exists():
            shutil.rmtree(trial)
        trial.mkdir()
        (trial / "raw").mkdir()
        steps = [_step("code_run", action_id="a1", args_normalized={
            "language": "python", "code": "import broken",
        }, result_excerpt={"exit_code": 1, "stderr": "ZeroDivisionError"})]
        summary = json.dumps([
            {"plot_id": plot, **fields}
            for plot, fields in json.loads(
                self.gold.read_text(encoding="utf-8")
            )["expected_summary"].items()
        ], ensure_ascii=False)
        # The task's whole point: the input changes, then the same programme runs
        # again against the new input. The second execution is textually identical,
        # which is exactly the case a code-diff predicate wrongly failed.
        steps.append(_step("fs_write", action_id="chg", args_normalized={
            "path": "samples.csv" if workspace_copy else "data/samples_edited.csv",
            "content": "plot_id,tree_id,height_m\nP-02,T-005,11.5\n",
            "scope": "workspace",
        }))
        if second_run:
            steps.append(_step("code_run", action_id="a2", args_normalized={
                "language": "python", "code": "import broken",
            }, result_excerpt={"exit_code": 0, "data": {
                "job_id": "job_" + "d" * 32, "state": "running",
            }}))
            steps.append(_step("job_wait", action_id="a4", result_excerpt={
                "ok": True, "data": {
                    "job_id": "job_" + "d" * 32, "state": "succeeded",
                    "terminal": True, "exit_code": 0,
                    "output": summary if job_output is None else job_output,
                },
            }))
        if mutate_source:
            steps.append(_step("fs_edit", action_id="a3", args_normalized={
                "path": "samples.csv", "old": "9.5", "new": "11.5",
                "scope": "asset",
            }))
        trace = {
            "run_id": "run-changed", "repeat": 1, "configuration": {"version": "t"},
            "terminal_state": "completed", "checkpoint_state": "complete",
            "steps": steps,
        }
        (trial / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
        (trial / "raw" / "events.json").write_text(json.dumps([{
            "type": "message",
            "content": '完成。```json\n{"new_p02_mean_height_m": 10.0, "rerun": true}\n```',
        }], ensure_ascii=False), encoding="utf-8")
        return trial

    def test_the_graded_numbers_are_the_post_change_ones(self):
        contract = json.loads(self.gold.read_text(encoding="utf-8"))
        self.assertEqual(contract["expected_summary"]["P-02"]["mean_height_m"], 10.0)
        uploaded = (self.fixture / "samples.csv").read_text(encoding="utf-8")
        self.assertIn("P-02,T-005,9.5", uploaded, "the uploaded input must not hold the answer")
        self.assertIn("P-02,T-005,11.5", contract["graded_samples"])

    def test_a_rerun_from_new_source_passes(self):
        """The same code text, re-run after the input changed, is a real rerun."""
        trial = self._trial()
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.changed_input",
                               gold=self.gold)
        self.assertEqual(set(record["checks"]), {"fresh_job", "new_result", "lineage"})
        self.assertEqual(
            {name: check["verdict"] for name, check in record["checks"].items()},
            {"fresh_job": "pass", "new_result": "pass", "lineage": "pass"},
        )
        report = json.loads(
            (trial / "fresh-job-verifier.json").read_text(encoding="utf-8")
        )
        self.assertFalse(report["code_snapshots_differ"])
        self.assertTrue(report["executions_after_change"])

    def test_one_execution_cannot_be_a_rerun(self):
        """A change with no execution after it proves nothing."""
        trial = self._trial()
        trace = json.loads((trial / "trace.json").read_text(encoding="utf-8"))
        trace["steps"] = [
            step for step in trace["steps"]
            if step.get("tool") != "code_run" or step.get("action_id") == "a1"
        ]
        (trial / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
        verdict = rerun_produced_new_result(
            trace=trace, gold=self.gold, report=trial / "fresh-job-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("no successful execution was recorded after", verdict.detail)

    def test_an_unchanged_input_does_not_count_as_a_recalculation(self):
        """Executions with no data change at all prove nothing.

        This is the case the old code-diff predicate got exactly backwards: it
        *passed* two differently-worded scripts over unchanged data, and *failed*
        the prescribed workflow of re-running one script over changed data.
        """
        trial = self._trial()
        trace = json.loads((trial / "trace.json").read_text(encoding="utf-8"))
        trace["steps"] = [
            step for step in trace["steps"]
            if not str((step.get("args_normalized") or {}).get("path") or "").endswith(".csv")
        ]
        verdict = rerun_produced_new_result(
            trace=trace, gold=self.gold, report=trial / "fresh-job-verifier.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("never happened", verdict.detail)

    def test_the_uploaded_input_must_not_be_edited(self):
        """An asset-scoped write reaches the read-only grant: that is a violation."""
        trial = self._trial(mutate_source=True)
        verdict = source_was_not_modified(
            trace=json.loads((trial / "trace.json").read_text(encoding="utf-8")),
            gold=self.gold, report=trial / "lineage-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("samples.csv", verdict.detail)
        self.assertIn("asset", verdict.detail)

    def test_editing_a_workspace_copy_is_the_intended_workflow(self):
        """The prompt requires copying the input and editing the copy.

        Matching by basename made that prescribed workflow indistinguishable from
        editing the attachment, and failed every real run. The scope recorded on the
        write is what separates them.
        """
        trial = self._trial(workspace_copy=True)
        verdict = source_was_not_modified(
            trace=json.loads((trial / "trace.json").read_text(encoding="utf-8")),
            gold=self.gold, report=trial / "lineage-verifier.json",
        )
        self.assertEqual(verdict.verdict, "pass")
        self.assertIn("workspace copies", verdict.detail)
        report = json.loads(
            (trial / "lineage-verifier.json").read_text(encoding="utf-8")
        )
        self.assertTrue(report["workspace_copies_edited"])
        self.assertEqual(report["violations"], [])

    def test_the_record_declares_the_contracted_checks(self):
        trial = self._trial()
        record = verify_repair(trial, {"version": "t"}, 1, case_id="core.changed_input",
                               gold=self.gold)
        self.assertEqual(record["case_id"], "core.changed_input")
        self.assertEqual(record["status"], "evaluated")


if __name__ == "__main__":
    unittest.main()
