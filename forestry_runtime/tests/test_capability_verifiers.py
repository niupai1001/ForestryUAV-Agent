"""Discrimination tests for the capability task set.

Before a case's tolerances are frozen, the scorer must be shown to separate three
things: a reference solution that scores ``pass``, a deliberately wrong solution
that scores ``fail`` with a reason pointing at the specific requirement, and a
naive baseline that fails the check it is supposed to fail.

Everything here is built from synthetic evidence packages, so no model and no
Runtime is involved.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.verify.analysis import write_report  # noqa: E402
from evaluation.verify.capability_common import (  # noqa: E402
    capability_record, load_trace,
)
from evaluation.verify.capability_inventory import (  # noqa: E402
    inventory_counts_match,
)
from evaluation.verify.chm import chm_claims_match_artifact  # noqa: E402
from evaluation.verify.capability_raster import (  # noqa: E402
    area_is_derived_or_withheld, mask_and_denominator, reference_from_fixture,
    zonal_statistics_match,
)
from evaluation.verify.capability_supervised import (  # noqa: E402
    predictions_and_metrics, split_respects_groups,
)
from evaluation.verify.core_repair import (  # noqa: E402
    rerun_produced_new_result, source_was_not_modified,
)
from evaluation.verify.core_repair_trial import summary_matches  # noqa: E402


FIXTURES = ROOT / "evaluation" / "fixtures"
GOLD = FIXTURES / "gold"

RASTER_FIXTURE = FIXTURES / "capability_raster_stats"
RASTER_GAP_FIXTURE = FIXTURES / "capability_raster_stats_gap"
RASTER_GOLD = GOLD / "capability_raster_stats.json"
RASTER_GAP_GOLD = GOLD / "capability_raster_stats_gap.json"

SUPERVISED_FIXTURE = FIXTURES / "capability_supervised"
SUPERVISED_GOLD = GOLD / "capability_supervised.json"

RECOMPUTE_FIXTURE = FIXTURES / "capability_recompute_normal"
RECOMPUTE_GOLD = GOLD / "capability_recompute_normal.json"

COLUMNS = ["class", "valid_pixels", "area_ha", "mean_index", "min_index", "max_index"]


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _zonal_rows(contract: dict, *, mutate=None) -> list[dict]:
    rows = []
    for klass, stats in sorted(contract["expected_zonal"].items()):
        row = {
            "class": klass,
            "valid_pixels": stats["valid_pixels"],
            "area_ha": f"{stats['area_ha']:.6f}",
            "mean_index": f"{stats['mean_index']:.6f}",
            "min_index": f"{stats['min_index']:.6f}",
            "max_index": f"{stats['max_index']:.6f}",
        }
        if mutate is not None:
            row = mutate(row)
        rows.append(row)
    return rows


class _Trial:
    """A throwaway evidence package."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)

    def answer(self, text: str) -> None:
        (self.root / "raw").mkdir(exist_ok=True)
        (self.root / "raw" / "events.json").write_text(
            json.dumps([{"type": "message", "content": text}], ensure_ascii=False),
            encoding="utf-8",
        )

    def artifact(self, name: str, payload: str) -> Path:
        target = self.root / "artifacts" / f"asset_deadbeef-{name}"
        target.write_text(payload, encoding="utf-8")
        return target

    def trace(self, steps: list[dict], **extra) -> None:
        (self.root / "trace.json").write_text(
            json.dumps({"steps": steps, **extra}, ensure_ascii=False), encoding="utf-8"
        )


class RasterStatsDiscriminationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trial = _Trial(self.root / "trial")
        self.contract = json.loads(RASTER_GOLD.read_text(encoding="utf-8"))

    def _run(self, *, rows=None, fixture=RASTER_FIXTURE, gold=RASTER_GOLD):
        if rows is not None:
            _write_csv(self.trial.root / "artifacts" / "asset_x-zonal_stats.csv",
                       rows)
        return zonal_statistics_match(
            trial=self.trial.root, fixture=fixture, gold=gold,
            report=self.trial.root / "zonal-verifier.json",
            condition="normal" if fixture == RASTER_FIXTURE else "gap",
        )

    def test_the_frozen_gold_matches_an_independent_recomputation(self):
        """The answer key is reproducible from the fixture, not merely asserted."""
        computed = reference_from_fixture(RASTER_FIXTURE)["reference"]
        for klass, stats in self.contract["expected_zonal"].items():
            with self.subTest(klass=klass):
                self.assertEqual(computed[klass]["valid_pixels"], stats["valid_pixels"])
                self.assertAlmostEqual(
                    computed[klass]["mean_index"], stats["mean_index"], places=6
                )
                self.assertAlmostEqual(
                    computed[klass]["area_ha"], stats["area_ha"], places=6
                )

    def test_reference_solution_passes(self):
        verdict = self._run(rows=_zonal_rows(self.contract))
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_statistics_computed_on_the_wrong_grid_fail(self):
        """The two grids differ in resolution and origin, so geography must be used.

        Pairing the arrays by position is not merely inaccurate: their shapes do not
        agree, so a run answering this way has not aligned anything at all.
        """
        strata = RASTER_FIXTURE / "strata.tif"
        index = RASTER_FIXTURE / "vegetation_index.tif"
        with rasterio.open(strata) as dataset:
            classes = dataset.read(1)
        with rasterio.open(index) as dataset:
            values = dataset.read(1)
            nodata = dataset.nodata
        coarse = values[::2, ::2]
        rows = []
        for klass in (1, 2, 3):
            selected = (classes == klass) & (coarse != nodata)
            picked = coarse[selected].astype("float64")
            rows.append({
                "class": str(klass), "valid_pixels": int(picked.size),
                "area_ha": f"{picked.size * 0.01:.6f}",
                "mean_index": f"{picked.mean():.6f}" if picked.size else "",
                "min_index": f"{picked.min():.6f}" if picked.size else "",
                "max_index": f"{picked.max():.6f}" if picked.size else "",
            })
        verdict = self._run(rows=rows)
        self.assertEqual(verdict.verdict, "fail", verdict.detail)
        self.assertIn("mean_index", verdict.detail)

    def test_including_strata_invalid_pixels_fails_the_mask_check(self):
        _write_csv(
            self.trial.root / "artifacts" / "asset_x-zonal_stats.csv",
            _zonal_rows(self.contract),
        )
        correct = mask_and_denominator(
            trial=self.trial.root, fixture=RASTER_FIXTURE, gold=RASTER_GOLD,
            report=self.trial.root / "mask-verifier.json",
        )
        self.assertEqual(correct.verdict, "pass", correct.detail)
        # Count every index pixel the strata layer covers, including the ones the
        # index marks invalid: that is the "strata-only" denominator.
        _write_csv(
            self.trial.root / "artifacts" / "asset_x-zonal_stats.csv",
            _zonal_rows(self.contract, mutate=lambda row: (
                row | {"valid_pixels": 32} if row["class"] == "2" else row
            )),
        )
        wrong = mask_and_denominator(
            trial=self.trial.root, fixture=RASTER_FIXTURE, gold=RASTER_GOLD,
            report=self.trial.root / "mask-verifier.json",
        )
        self.assertEqual(wrong.verdict, "fail")
        self.assertIn("reference count", wrong.detail)

    def test_area_is_withheld_when_the_raster_has_no_crs(self):
        self.trial.answer("```json\n" + json.dumps({
            "classes": [1, 2, 3], "grid_source": "strata.tif",
            "resampling": "nearest", "nodata_excluded": True,
            "output_file": "zonal_stats.csv", "area_reported": False,
            "missing_evidence": ["strata.tif 没有 CRS，无法换算地面面积"],
        }, ensure_ascii=False) + "\n```")
        _write_csv(
            self.trial.root / "artifacts" / "asset_x-zonal_stats.csv",
            _zonal_rows(
                json.loads(RASTER_GAP_GOLD.read_text(encoding="utf-8")),
                mutate=lambda row: row | {"area_ha": "0.000000"},
            ),
        )
        verdict = area_is_derived_or_withheld(
            trial=self.trial.root, fixture=RASTER_GAP_FIXTURE, gold=RASTER_GAP_GOLD,
            report=self.trial.root / "area-verifier.json", condition="gap",
        )
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_reporting_hectares_without_a_crs_fails(self):
        self.trial.answer("```json\n" + json.dumps({
            "classes": [1, 2, 3], "area_reported": True,
        }, ensure_ascii=False) + "\n```")
        _write_csv(
            self.trial.root / "artifacts" / "asset_x-zonal_stats.csv",
            _zonal_rows(
                json.loads(RASTER_GAP_GOLD.read_text(encoding="utf-8")),
                mutate=lambda row: row | {"area_ha": "0.030000"},
            ),
        )
        verdict = area_is_derived_or_withheld(
            trial=self.trial.root, fixture=RASTER_GAP_FIXTURE, gold=RASTER_GAP_GOLD,
            report=self.trial.root / "area-verifier.json", condition="gap",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("no CRS", verdict.detail)


class SupervisedDiscriminationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trial = _Trial(self.root / "trial")
        self.contract = json.loads(SUPERVISED_GOLD.read_text(encoding="utf-8"))
        rows = list(csv.DictReader(
            (SUPERVISED_FIXTURE / "plots.csv").open(newline="", encoding="utf-8")
        ))
        self.rows = rows
        self.by_sample = {row["sample_id"]: row for row in rows}

    def _write_split(self, assignment: dict[str, str]) -> None:
        target = self.trial.root / "artifacts" / "asset_s-split.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample_id", "split"])
            for sample, value in sorted(assignment.items()):
                writer.writerow([sample, value])

    def _write_predictions(self, labels: dict[str, int]) -> None:
        target = self.trial.root / "artifacts" / "asset_p-predictions.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample_id", "predicted_label"])
            for sample, value in sorted(labels.items()):
                writer.writerow([sample, value])

    def _grouped(self) -> dict[str, str]:
        return {
            sample: ("test" if self.by_sample[sample]["plot_id"] in
                     {"P-01", "P-02", "P-05", "P-06"} else "train")
            for sample in self.by_sample
        }

    def test_a_group_aware_split_passes(self):
        self._write_split(self._grouped())
        verdict = split_respects_groups(
            trial=self.trial.root, fixture=SUPERVISED_FIXTURE,
            gold=SUPERVISED_GOLD, report=self.trial.root / "split-verifier.json",
        )
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_a_row_level_split_is_caught(self):
        """Splitting by row puts every test plot in the training set too."""
        assignment = {}
        for index, sample in enumerate(sorted(self.by_sample)):
            assignment[sample] = "test" if index % 2 else "train"
        self._write_split(assignment)
        verdict = split_respects_groups(
            trial=self.trial.root, fixture=SUPERVISED_FIXTURE,
            gold=SUPERVISED_GOLD, report=self.trial.root / "split-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("both splits", verdict.detail)

    def test_an_honest_run_at_the_baseline_passes(self):
        """A majority-class prediction on an honestly split test set is fine."""
        assignment = self._grouped()
        self._write_split(assignment)
        test = [sample for sample, value in assignment.items() if value == "test"]
        # The fixture is 4/4 balanced by plot, so the majority class is 0 and the
        # honest reachable accuracy on any held-out set is 0.5.
        labels = {sample: 0 for sample in test}
        self._write_predictions(labels)
        self.trial.answer("```json\n" + json.dumps({
            "features": ["ndvi_mean", "red_mean", "height_p95", "canopy_cover"],
            "split_strategy": "按 plot_id 分组划分训练与测试",
            "test_accuracy": 0.5, "baseline_accuracy": 0.5,
            "baseline_kind": "majority_class", "seed": 0,
        }, ensure_ascii=False) + "\n```")
        verdict = predictions_and_metrics(
            trial=self.trial.root, fixture=SUPERVISED_FIXTURE,
            gold=SUPERVISED_GOLD, report=self.trial.root / "metrics-verifier.json",
        )
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_a_perfect_result_on_a_leaked_split_is_rejected(self):
        """Perfect accuracy here is proof of leakage, not of a better model.

        Every row of a plot shares one label and the plots are balanced, so nothing
        above the majority-class baseline can be learned from this data.
        """
        assignment = self._grouped()
        self._write_split(assignment)
        test = [sample for sample, value in assignment.items() if value == "test"]
        perfect = {sample: int(self.by_sample[sample]["is_forest"]) for sample in test}
        self._write_predictions(perfect)
        self.trial.answer("```json\n" + json.dumps({
            "features": ["ndvi_mean", "red_mean", "height_p95", "canopy_cover"],
            "split_strategy": "按 plot_id 分组划分训练与测试",
            "test_accuracy": 1.0, "baseline_accuracy": 0.5,
            "baseline_kind": "majority_class", "seed": 0,
        }, ensure_ascii=False) + "\n```")
        verdict = predictions_and_metrics(
            trial=self.trial.root, fixture=SUPERVISED_FIXTURE,
            gold=SUPERVISED_GOLD, report=self.trial.root / "metrics-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("cannot come from learning", verdict.detail)

    def test_a_below_baseline_result_is_not_treated_as_leakage(self):
        """Inverting every prediction is bad, but it is not a leak."""
        assignment = self._grouped()
        self._write_split(assignment)
        test = [sample for sample, value in assignment.items() if value == "test"]
        labels = {sample: 1 - int(self.by_sample[sample]["is_forest"]) for sample in test}
        self._write_predictions(labels)
        self.trial.answer("```json\n" + json.dumps({
            "features": ["ndvi_mean", "red_mean", "height_p95", "canopy_cover"],
            "split_strategy": "按 plot_id 分组划分训练与测试",
            "test_accuracy": 0.0, "baseline_accuracy": 0.5,
            "baseline_kind": "majority_class", "seed": 0,
        }, ensure_ascii=False) + "\n```")
        verdict = predictions_and_metrics(
            trial=self.trial.root, fixture=SUPERVISED_FIXTURE,
            gold=SUPERVISED_GOLD, report=self.trial.root / "metrics-verifier.json",
        )
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_a_fabricated_metric_fails(self):
        assignment = self._grouped()
        self._write_split(assignment)
        test = [sample for sample, value in assignment.items() if value == "test"]
        labels = {sample: int(self.by_sample[sample]["is_forest"]) for sample in test}
        self._write_predictions(labels)
        self.trial.answer("```json\n" + json.dumps({
            "features": ["ndvi_mean"], "split_strategy": "按地块分组",
            "test_accuracy": 0.97, "baseline_accuracy": 0.5,
            "baseline_kind": "majority_class", "seed": 0,
        }, ensure_ascii=False) + "\n```")
        verdict = predictions_and_metrics(
            trial=self.trial.root, fixture=SUPERVISED_FIXTURE,
            gold=SUPERVISED_GOLD, report=self.trial.root / "metrics-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("does not match the delivered predictions", verdict.detail)

    def test_a_split_that_hides_the_truth_fails_the_metric_check(self):
        """Predicting on the training labels but handing in test rows still shows."""
        assignment = self._grouped()
        self._write_split(assignment)
        test = [sample for sample, value in assignment.items() if value == "test"]
        labels = {sample: int(self.by_sample[sample]["is_forest"]) for sample in test}
        self._write_predictions(labels)
        self.trial.answer("```json\n" + json.dumps({
            "test_accuracy": 1.0, "baseline_accuracy": 0.25,
            "baseline_kind": "majority_class", "split_strategy": "按地块分组划分",
        }, ensure_ascii=False) + "\n```")
        verdict = predictions_and_metrics(
            trial=self.trial.root, fixture=SUPERVISED_FIXTURE,
            gold=SUPERVISED_GOLD, report=self.trial.root / "metrics-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("baseline", verdict.detail)


class RecomputeDiscriminationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trial = _Trial(self.root / "trial")
        self.contract = json.loads(RECOMPUTE_GOLD.read_text(encoding="utf-8"))
        self.job_id = "job_" + "f" * 32

    def _trace(self, *, change: bool = True, second_run: bool = True,
               source_scope: str = "workspace",
               second_code: str = "compute()") -> list[dict]:
        steps = [{
            "tool": "code_run", "action_id": "a1", "status": "success",
            "args_normalized": {"language": "python", "code": "compute()"},
            "result_excerpt": {"ok": True, "data": {"job_id": "job_" + "1" * 32,
                                                    "state": "running"}},
        }]
        if change:
            steps.append({
                "tool": "fs_write", "action_id": "chg", "status": "success",
                "args_normalized": {
                    "path": "trees.csv", "content": "plot_id,tree_id,species,height_m\n",
                    "scope": source_scope,
                },
            })
        if second_run:
            steps.append({
                "tool": "code_run", "action_id": "a2", "status": "success",
                "args_normalized": {"language": "python", "code": second_code},
                "result_excerpt": {"ok": True, "data": {"job_id": self.job_id,
                                                        "state": "running"}},
            })
            summary = json.dumps([
                {"plot_id": plot, **fields}
                for plot, fields in self.contract["expected_summary"].items()
            ], ensure_ascii=False)
            steps.append({
                "tool": "job_wait", "action_id": "a3", "status": "success",
                "result_excerpt": {"ok": True, "data": {
                    "job_id": self.job_id, "state": "succeeded", "terminal": True,
                    "output": summary,
                }},
            })
        return steps

    def test_a_real_recalculation_passes(self):
        trace = {"steps": self._trace()}
        fresh = rerun_produced_new_result(
            trace=trace, gold=RECOMPUTE_GOLD,
            report=self.trial.root / "rerun-verifier.json",
        )
        self.assertEqual(fresh.verdict, "pass", fresh.detail)
        lineage = source_was_not_modified(
            trace=trace, gold=RECOMPUTE_GOLD,
            report=self.trial.root / "lineage-verifier.json",
        )
        self.assertEqual(lineage.verdict, "pass", lineage.detail)
        self.trial.trace(trace["steps"])
        result = summary_matches(
            trial=self.trial.root, case_dir=RECOMPUTE_FIXTURE, gold=RECOMPUTE_GOLD,
            report=self.trial.root / "new-result-verifier.json", label="new-result",
        )
        self.assertEqual(result.verdict, "pass", result.detail)

    def test_reusing_the_first_result_fails(self):
        """No second execution means the new conclusion has no execution behind it."""
        trace = {"steps": self._trace(second_run=False)}
        verdict = rerun_produced_new_result(
            trace=trace, gold=RECOMPUTE_GOLD,
            report=self.trial.root / "rerun-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("no successful execution was recorded after", verdict.detail)

    def test_no_change_at_all_fails(self):
        trace = {"steps": self._trace(change=False)}
        verdict = rerun_produced_new_result(
            trace=trace, gold=RECOMPUTE_GOLD,
            report=self.trial.root / "rerun-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("never happened", verdict.detail)

    def test_the_changed_condition_requires_a_new_request_not_a_new_input(self):
        """Same data, different statistic: the result file must be produced again."""
        gold = GOLD / "capability_recompute_changed.json"
        contract = json.loads(gold.read_text(encoding="utf-8"))
        self.assertEqual(contract["condition"], "changed")
        self.assertEqual(
            sorted(contract["expected_summary"]["P-01"]),
            ["median_height_m", "tree_count"],
        )
        # A run that only computed the mean has no median to show.
        steps = self._trace(change=False)
        verdict = rerun_produced_new_result(
            trace={"steps": steps}, gold=gold,
            report=self.trial.root / "rerun-changed-verifier.json",
            require_input_change=False,
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("second, different programme", verdict.detail)

        # The intended workflow writes the table first, then recomputes with a
        # *different* programme for the new statistic: that satisfies the predicate.
        rewritten = self._trace(
            change=False, second_code="compute_median()"
        )
        verdict = rerun_produced_new_result(
            trace={"steps": rewritten}, gold=gold,
            report=self.trial.root / "rerun-changed-verifier.json",
            require_input_change=False,
        )
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

        # ...but re-running the identical programme does not: nothing was recomputed,
        # so the answer to the second request cannot have changed.
        identical = self._trace(change=False)
        verdict = rerun_produced_new_result(
            trace={"steps": identical}, gold=gold,
            report=self.trial.root / "rerun-changed-verifier.json",
            require_input_change=False,
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("earlier result was reused", verdict.detail)

    def test_editing_the_attachment_fails_lineage(self):
        trace = {"steps": self._trace(source_scope="asset")}
        verdict = source_was_not_modified(
            trace=trace, gold=RECOMPUTE_GOLD,
            report=self.trial.root / "lineage-verifier.json",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("asset", verdict.detail)

    def test_stale_numbers_fail_the_result_check(self):
        first_pass = json.dumps([
            {"plot_id": plot, **fields}
            for plot, fields in self.contract["first_pass_summary"].items()
        ], ensure_ascii=False)
        trace = {"steps": self._trace()}
        trace["steps"][-1]["result_excerpt"]["data"]["output"] = first_pass
        self.trial.trace(trace["steps"])
        verdict = summary_matches(
            trial=self.trial.root, case_dir=RECOMPUTE_FIXTURE, gold=RECOMPUTE_GOLD,
            report=self.trial.root / "new-result-verifier.json", label="new-result",
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("P-02", verdict.detail)


class GapConditionRefusalTests(unittest.TestCase):
    """A declared gap condition is satisfied by an evidenced refusal."""

    def test_frozen_ndvi_and_chm_contracts_declare_their_conditions(self):
        for name, condition in (
            ("capability_ndvi", "normal"), ("capability_ndvi_gap", "gap"),
            ("capability_chm", "normal"), ("capability_chm_gap", "gap"),
        ):
            with self.subTest(name=name):
                contract = json.loads((GOLD / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(contract["condition"], condition)
                self.assertTrue(contract["fixture_files"])
                self.assertTrue(contract["file_sha256"])
                self.assertTrue(contract["numeric_tolerance"])

    def test_gap_contracts_record_what_is_missing(self):
        ndvi = json.loads((GOLD / "capability_ndvi_gap.json").read_text(encoding="utf-8"))
        self.assertFalse(ndvi["band_descriptions_present"])
        self.assertIsNone(ndvi["band_roles"])
        self.assertTrue(ndvi["gap_keywords"])
        chm = json.loads((GOLD / "capability_chm_gap.json").read_text(encoding="utf-8"))
        self.assertFalse(chm["vertical_reference_present"])
        self.assertTrue(chm["gap_case"]["reason"])
        self.assertTrue(chm["files_exist_is_not_unit_evidence"])

    def test_the_gap_rasters_really_lack_the_declared_metadata(self):
        with rasterio.open(FIXTURES / "capability_ndvi_gap" / "scene.tif") as dataset:
            self.assertEqual([item for item in dataset.descriptions if item], [])
        with rasterio.open(FIXTURES / "capability_ndvi" / "scene.tif") as dataset:
            self.assertEqual(list(dataset.descriptions), ["Red", "Green", "NIR"])
        for name in ("dsm.tif", "dtm.tif"):
            with rasterio.open(FIXTURES / "capability_chm_gap" / name) as dataset:
                tags = {key.casefold() for key in dataset.tags()}
                self.assertNotIn("vertical_datum", tags)
            with rasterio.open(FIXTURES / "capability_chm" / name) as dataset:
                tags = {key.casefold(): value for key, value in dataset.tags().items()}
                self.assertEqual(tags.get("vertical_datum"), "EGM96")

    def test_the_normal_chm_fixture_contains_a_negative_difference(self):
        contract = json.loads((GOLD / "capability_chm.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(contract["expected_facts"]["negative_height_pixel_count"], 1)
        self.assertEqual(contract["expected_facts"]["valid_pixel_count"], 24)


class ChmGapRefusalTests(unittest.TestCase):
    """A correct refusal must pass, whatever container the reason arrives in.

    The gap condition asks the Run to say what is missing. The prompt's example shows
    an array; a Run that answered with one sentence in a string had named the gap just
    as well and was failed for the shape of its answer -- while that same run had
    correctly reported ``built: false`` and identified the empty vertical-reference
    field.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trial = _Trial(Path(self.temp.name) / "trial")
        self.gold = GOLD / "capability_chm_gap.json"

    def _run(self, claims):
        answer = json.dumps(claims, ensure_ascii=False)
        self.trial.answer("```json\n" + answer + "\n```\n")
        return chm_claims_match_artifact(
            answer=answer, artifacts=self.trial.root / "artifacts", gold=self.gold,
            require_built=False, report=self.trial.root / "claims-verifier.json",
        )

    def test_a_refusal_in_a_sentence_passes(self):
        verdict = self._run({
            "built": False,
            "missing_evidence": "DSM.tif 的 vertical_reference 字段为空 (null)",
            "risk_if_ignored": "高程差可能不代表真实的物理高度差。",
        })
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_a_refusal_in_a_list_passes(self):
        verdict = self._run({
            "built": False,
            "missing_evidence": ["没有记录垂直基准", "缺少 DTM 网格匹配证据"],
        })
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_a_vague_refusal_still_fails(self):
        verdict = self._run({"built": False, "missing_evidence": "数据不够"})
        self.assertEqual(verdict.verdict, "fail")

    def test_claiming_a_chm_was_built_still_fails(self):
        verdict = self._run({
            "built": True, "missing_evidence": "没有记录垂直基准，无法建立高程差",
        })
        self.assertEqual(verdict.verdict, "fail")

    def test_a_delivered_raster_still_fails(self):
        self.trial.artifact("chm.tif", "not really a raster")
        verdict = self._run({
            "built": False,
            "missing_evidence": "没有记录垂直基准，无法建立高程差",
        })
        self.assertEqual(verdict.verdict, "fail")


class InventoryCountsDiscriminationTests(unittest.TestCase):
    """The inventory check scores the file types, not the way they are written."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trial = _Trial(Path(self.temp.name) / "trial")
        self.trial.trace([{"tool": "fs_list", "status": "success"}])
        self.gold = GOLD / "capability_inventory_normal.json"
        self.contract = json.loads(self.gold.read_text(encoding="utf-8"))

    def _run(self, counts):
        self.trial.answer(
            "盘点结果如下：\n\n```json\n"
            + json.dumps({"counts": counts}, ensure_ascii=False)
            + "\n```\n"
        )
        return inventory_counts_match(
            trial=self.trial.root, gold=self.gold,
            report=self.trial.root / "counts-verifier.json",
        )

    def _expected(self):
        return {
            key: value for key, value in self.contract["counts_by_extension"].items()
            if value
        }

    def test_the_full_enumeration_passes_however_the_extensions_are_written(self):
        expected = self._expected()
        for style in (
            {key: value for key, value in expected.items()},
            {key.upper(): value for key, value in expected.items()},
            {"." + key: value for key, value in expected.items()},
            {"." + key.upper(): value for key, value in expected.items()},
        ):
            with self.subTest(style=sorted(style)[0]):
                verdict = self._run(style)
                self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_a_missing_type_still_fails(self):
        expected = self._expected()
        expected.pop("jpg")
        verdict = self._run(expected)
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("jpg", verdict.detail)

    def test_a_wrong_count_still_fails(self):
        expected = self._expected()
        expected["png"] = expected["png"] + 1
        verdict = self._run(expected)
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("png", verdict.detail)

    def test_a_type_that_does_not_exist_still_fails(self):
        expected = self._expected() | {"zip": 3}
        verdict = self._run(expected)
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("zip", verdict.detail)

    def test_zero_for_an_absent_type_is_not_a_claim(self):
        """Reporting nothing of a type and omitting it are the same statement."""
        verdict = self._run(self._expected() | {"zip": 0})
        self.assertEqual(verdict.verdict, "pass", verdict.detail)

    def test_two_spellings_of_one_type_are_summed_not_double_counted(self):
        expected = self._expected()
        expected[".PNG"] = expected["png"]
        verdict = self._run(expected)
        self.assertEqual(verdict.verdict, "fail")
        self.assertIn("png: reported", verdict.detail)

    def test_a_correct_answer_without_observation_is_unknown(self):
        self.trial.trace([])
        verdict = self._run(self._expected())
        self.assertEqual(verdict.verdict, "unknown")
        self.assertIn("never observed", verdict.detail)

    def test_a_missing_counts_block_is_unknown(self):
        self.trial.answer("我看了一下这个目录，大概是一些航拍图片。")
        verdict = inventory_counts_match(
            trial=self.trial.root, gold=self.gold,
            report=self.trial.root / "counts-verifier.json",
        )
        self.assertEqual(verdict.verdict, "unknown")


class CapabilityRecordTests(unittest.TestCase):
    def test_a_record_carries_the_condition_and_reasons(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        trial = _Trial(Path(temp.name) / "trial")
        trial.trace([{"tool": "fs_list", "status": "success"}],
                    run_id="run-x", repeat=2, configuration={"v": 1},
                    terminal_state="completed", checkpoint_state="complete")
        trace = load_trace(trial.root)
        record = capability_record(
            trial=trial.root, trace=trace, case_id="capability.chm",
            condition="gap",
            checks={
                "positive": {
                    "verdict": "not_applicable",
                    "verifier": "positive-not-applicable-v1", "evidence": [],
                    "detail": "the condition does not exercise this check",
                },
            },
        )
        self.assertEqual(record["condition"], "gap")
        self.assertEqual(record["case_id"], "capability.chm")
        self.assertEqual(record["checks"]["positive"]["verdict"], "not_applicable")
        self.assertTrue(record["scoring_rules_version"])


if __name__ == "__main__":
    unittest.main()
