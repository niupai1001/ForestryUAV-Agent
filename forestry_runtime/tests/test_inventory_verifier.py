"""Contract tests for the forestry.inventory verifier."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.verify.inventory import (  # noqa: E402
    IMAGE_SUFFIXES, answer_matches_manifest, roles_match_manifest, stayed_read_only,
)
from evaluation.verify.inventory_trial import verify_trial  # noqa: E402


FIXTURES = ROOT / "evaluation" / "fixtures"
GOLD = FIXTURES / "gold" / "forestry_inventory.json"
FIXTURE = FIXTURES / "forestry_inventory"

UPLOADED = sorted(
    path.name for path in FIXTURE.iterdir()
    if path.is_file() and path.name != "prompt.txt"
)

GOOD_ANSWER = (
    "盘点完成。参考板与主航线已分开统计。\n```json\n"
    + json.dumps({
        "flight_imagery": 6, "reference_panel_before": 2, "reference_panel_after": 2,
        "geospatial_products": 1, "capture_groups": 5,
        "bands": {"Blue": 2, "Green": 1, "Red": 1, "RedEdge": 1, "NIR": 1},
    }, ensure_ascii=False)
    + "\n```"
)


def _step(tool, *, action_id="a1", status="success", **extra):
    step = {"tool": tool, "action_id": action_id, "status": status, "step": 1}
    step.update(extra)
    return step


class InventoryFixtureTests(unittest.TestCase):
    def test_frozen_manifest_is_independent_of_runtime_code(self):
        """The answer key is a hand-authored manifest, not the code under test.

        The previous design recomputed the expected roles by importing
        ``runtime.capabilities.uav_audit.audit`` at verify time. That made the
        classifier its own answer key: editing ``dataset_image_role`` turned a correct
        run into a failure, and a wrong classifier could never be detected.
        """
        import evaluation.verify.inventory as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from runtime", source)
        self.assertNotIn("import runtime", source)
        gold = json.loads(GOLD.read_text(encoding="utf-8"))
        self.assertEqual(sorted(gold["files"]), UPLOADED)
        # The frozen roles must be internally consistent with the countable files.
        countable = [
            name for name in UPLOADED
            if Path(name).suffix.casefold() in IMAGE_SUFFIXES
        ]
        self.assertEqual(sum(gold["roles"].values()), len(countable))

    def test_the_index_is_not_part_of_the_manifest(self):
        """A CSV is not an image or geospatial product, so it carries no role."""
        gold = json.loads(GOLD.read_text(encoding="utf-8"))
        self.assertIn(gold["index_file"], UPLOADED)
        self.assertNotIn(gold["index_file"], json.dumps(gold["roles"]))
        self.assertNotIn(
            Path(gold["index_file"]).suffix.casefold(), IMAGE_SUFFIXES,
            "a non-image suffix must not enter the manifest",
        )

    def test_a_stale_manifest_is_not_scored_as_a_pass(self):
        """If the fixture changes and the manifest is not updated, say so."""
        with tempfile.TemporaryDirectory() as raw:
            stale = Path(raw) / "gold.json"
            contract = json.loads(GOLD.read_text(encoding="utf-8"))
            contract["files"] = [*contract["files"], "an_extra_file.JPG"]
            stale.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            report = Path(raw) / "report.json"
            verdict = roles_match_manifest(trace={"steps": []}, gold=stale, report=report)
        self.assertEqual(verdict.verdict, "unknown")
        self.assertIn("stale", verdict.detail)

    def test_panels_are_not_counted_as_flight_imagery(self):
        gold = json.loads(GOLD.read_text(encoding="utf-8"))
        panels = sum(
            count for role, count in gold["roles"].items() if role.startswith("reference_panel")
        )
        self.assertEqual(panels, 4)
        self.assertEqual(gold["roles"]["flight_imagery"], 6)
        self.assertEqual(gold["total_uploaded_files"], len(UPLOADED))

    def test_bands_and_groups_match_the_index(self):
        gold = json.loads(GOLD.read_text(encoding="utf-8"))
        rows = [
            line.split(",") for line in
            (FIXTURE / gold["index_file"]).read_text(encoding="utf-8").strip().splitlines()[1:]
        ]
        flight = [row for row in rows if row[1] == "flight_imagery"]
        counted: dict[str, int] = {}
        for row in flight:
            counted[row[2]] = counted.get(row[2], 0) + 1
        self.assertEqual(counted, gold["bands"])
        # The existing deliverable belongs to no capture, so an empty group is not one.
        self.assertEqual(
            len({row[3] for row in rows if row[3]}), gold["capture_groups"]
        )


class InventoryVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trial = Path(self.temp.name) / "forestry-inventory-1"
        self.trial.mkdir()
        (self.trial / "raw").mkdir()

    def _observe(self, names=UPLOADED):
        return _step("fs_list", side_effect="none",
                     result_excerpt={"entries": [{"name": name} for name in names]})

    def _write_answer(self, content=GOOD_ANSWER):
        (self.trial / "raw" / "events.json").write_text(
            json.dumps([{"type": "message", "content": content}], ensure_ascii=False),
            encoding="utf-8",
        )

    def test_roles_check_passes_on_a_real_observation(self):
        verdict = roles_match_manifest(
            trace={"steps": [self._observe()]}, gold=GOLD,
            report=self.trial / "roles-verifier.json",
        )
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_roles_check_is_unknown_when_nothing_was_observed(self):
        verdict = roles_match_manifest(
            trace={"steps": [_step("fs_read", side_effect="none",
                                   result_excerpt={"text": "nothing here"})]},
            gold=GOLD, report=self.trial / "roles-verifier.json",
        )
        self.assertEqual(verdict.verdict, "unknown")

    def test_a_stale_manifest_blocks_instead_of_grading_against_itself(self):
        """A drifted contract must stop the case, not silently become the answer.

        The old check compared the Runtime's classifier to the gold and *failed the
        agent* when they disagreed, which punishes a correct run for a maintainer
        mistake. A stale manifest is an ``unknown`` for this slot plus a reason a
        human can act on.
        """
        broken = json.loads(GOLD.read_text(encoding="utf-8"))
        broken["roles"]["flight_imagery"] = 99
        path = Path(self.temp.name) / "broken-gold.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        verdict = roles_match_manifest(
            trace={"steps": [self._observe()]}, gold=path,
            report=self.trial / "roles-verifier.json",
        )
        self.assertEqual(verdict.verdict, "unknown")
        self.assertIn("stale", verdict.detail)
        self.assertIn("total", verdict.detail)

    def test_answer_check_requires_the_counts_groups_and_bands(self):
        self._write_answer()
        self.assertEqual(
            answer_matches_manifest(
                trial=self.trial, gold=GOLD, report=self.trial / "answer-verifier.json"
            ).verdict,
            "pass",
        )

    def test_a_panel_mixed_into_the_flight_count_fails(self):
        self._write_answer(GOOD_ANSWER.replace('"flight_imagery": 6', '"flight_imagery": 10'))
        verdict = answer_matches_manifest(
            trial=self.trial, gold=GOLD, report=self.trial / "answer-verifier.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("flight_imagery", verdict.detail)

    def test_a_band_the_archive_does_not_contain_fails(self):
        self._write_answer(
            GOOD_ANSWER.replace('"NIR": 1', '"NIR": 1, "SWIR": 3')
        )
        verdict = answer_matches_manifest(
            trial=self.trial, gold=GOLD, report=self.trial / "answer-verifier.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("SWIR", verdict.detail)

    def test_a_success_or_accuracy_claim_fails(self):
        self._write_answer(GOOD_ANSWER + "\n文件齐全，重建一定成功，精度满足要求。")
        verdict = answer_matches_manifest(
            trial=self.trial, gold=GOLD, report=self.trial / "answer-verifier.json"
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("cannot support", verdict.detail)

    def test_prose_without_a_block_is_unknown_not_a_failure(self):
        self._write_answer("共 6 张主航线航片、起飞前 2 张、起飞后 2 张，另有 1 个正射成果。")
        self.assertEqual(
            answer_matches_manifest(
                trial=self.trial, gold=GOLD, report=self.trial / "answer-verifier.json"
            ).verdict,
            "unknown",
        )

    def test_read_only_check_rejects_a_photogrammetry_task(self):
        self.assertEqual(
            stayed_read_only(
                trace={"steps": [self._observe()]}, report=self.trial / "readonly-verifier.json"
            ).verdict,
            "pass",
        )
        for mutation in (
            _step("start_orthomosaic", side_effect="durable_job"),
            _step("fs_write", side_effect="file_write", args_normalized={"path": "x.txt"}),
        ):
            verdict = stayed_read_only(
                trace={"steps": [self._observe(), mutation]},
                report=self.trial / "readonly-verifier.json",
            )
            self.assertEqual(verdict.verdict, "fail", mutation["tool"])

    def test_the_record_declares_the_contracted_checks(self):
        trace = {
            "run_id": "run-inventory", "repeat": 1, "configuration": {"version": "t"},
            "terminal_state": "completed", "checkpoint_state": "complete",
            "steps": [self._observe()],
        }
        (self.trial / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
        self._write_answer()
        record = verify_trial(self.trial, {"version": "t"}, 1, gold=GOLD)
        self.assertEqual(record["case_id"], "forestry.inventory")
        self.assertEqual(record["status"], "evaluated")
        self.assertEqual(set(record["checks"]), {"roles", "answer", "read_only"})
        self.assertTrue(all(
            check["verdict"] == "pass" for check in record["checks"].values()
        ), record["checks"])


if __name__ == "__main__":
    unittest.main()
