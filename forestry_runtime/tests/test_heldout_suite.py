"""Phase 4 acceptance: a held-out task set and a comparison that can attribute.

Two claims are checked here, and both are checkable without running a model:

* the task set is *fixed* -- every declared fixture exists, every expected answer is
  reproducible from the fixture that is on disk, and the set covers each category the
  comparison is supposed to say something about;
* the comparison *attributes or refuses to*. Metrics come only from collected records,
  an ungraded slot is a gap rather than a zero, and a pair of arms that differ in more
  than one axis is reported as unattributable instead of being credited to whichever
  axis the author had in mind.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from evaluation.heldout.compare import (
    aggregate,
    collect_slots,
    compare,
    case_ids,
    load_suite,
    plan,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HELDOUT = PROJECT_ROOT / "evaluation" / "heldout"
REQUIRED_CATEGORIES = {
    "rgb", "multispectral", "bands-unspecified", "missing-band", "missing-file",
    "environment-missing", "grass-confusion", "artifact-types", "non-remote-sensing",
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class HeldOutSuiteTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_suite()

    def test_every_required_category_is_present(self):
        categories = {case["category"] for case in self.suite["cases"]}
        self.assertLessEqual(REQUIRED_CATEGORIES, categories)

    def test_case_ids_are_unique_and_clustered(self):
        identifiers = case_ids(self.suite)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        clusters = {case["cluster"] for case in self.suite["cases"]}
        self.assertGreaterEqual(len(clusters), 5)

    def test_each_case_names_its_fixture_gold_and_acceptance(self):
        for case in self.suite["cases"]:
            with self.subTest(case=case["id"]):
                fixture = PROJECT_ROOT / case["fixture"]
                self.assertTrue(fixture.is_dir(), fixture)
                self.assertTrue((fixture / "prompt.txt").is_file())
                self.assertTrue((PROJECT_ROOT / case["gold"]).is_file())
                self.assertTrue(case.get("probes"))
                for name in ("artifact", "applicability", "citation", "delivery"):
                    self.assertIn(name, case["acceptance"])

    def test_the_fixed_resources_are_declared(self):
        resources = self.suite["fixed_resources"]
        for key in ("repeats", "job_cpus", "job_memory", "max_model_calls",
                    "context_tokens", "temperature"):
            self.assertIn(key, resources)
        self.assertEqual(resources["repeats"], 3)

    def test_the_three_axes_are_declared_with_their_ablation(self):
        axes = self.suite["axes"]
        self.assertEqual(set(axes), {"model", "harness", "retrieval"})
        self.assertIn("none", axes["retrieval"]["values"])
        self.assertIn("guides", axes["retrieval"]["values"])


class HeldOutFixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = HELDOUT / "fixtures"
        self.gold = HELDOUT / "gold"

    def test_the_rgb_fixture_is_three_band_and_its_gold_recomputes(self):
        import rasterio
        with rasterio.open(self.fixtures / "heldout_rgb_canopy" / "orthomosaic.tif") as src:
            self.assertEqual(src.count, 3)
            self.assertEqual(src.dtypes[0], "uint16")
            self.assertEqual(src.crs.to_string(), "EPSG:32650")
            self.assertAlmostEqual(src.res[0], 0.1)
        with rasterio.open(
            self.fixtures / "heldout_rgb_canopy" / "reference_mask.tif"
        ) as mask:
            canopy = int(np.count_nonzero(mask.read(1)))
            total = mask.width * mask.height
        gold = _read_json(self.gold / "heldout_rgb_canopy.json")
        self.assertEqual(gold["canopy_pixels"], canopy)
        self.assertEqual(gold["valid_pixels"], total)
        self.assertAlmostEqual(gold["canopy_fraction"], canopy / total)

    def test_the_multispectral_fixture_declares_its_band_roles(self):
        import rasterio
        with rasterio.open(
            self.fixtures / "heldout_multispectral_ndvi" / "multispectral.tif"
        ) as src:
            self.assertEqual(src.descriptions, ("Red", "Green", "NIR"))
            red = src.read(1).astype("float64")
            nir = src.read(3).astype("float64")
        denominator = nir + red
        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = np.where(denominator != 0, (nir - red) / denominator, np.nan)
        valid = np.isfinite(ndvi)
        gold = _read_json(self.gold / "heldout_multispectral_ndvi.json")
        self.assertEqual(gold["bands"], {"red": 1, "nir": 3})
        self.assertEqual(gold["valid_pixel_count"], int(valid.sum()))
        self.assertEqual(gold["zero_denominator_pixel_count"], int((denominator == 0).sum()))
        self.assertAlmostEqual(gold["mean"], float(np.nanmean(ndvi)), places=6)

    def test_the_missing_band_fixture_really_lacks_a_declared_nir_band(self):
        import rasterio
        with rasterio.open(
            self.fixtures / "heldout_multispectral_gap" / "red_green_only.tif"
        ) as src:
            self.assertEqual(src.count, 2)
            self.assertEqual(src.descriptions, ("Red", "Green"))
        gold = _read_json(self.gold / "heldout_multispectral_gap.json")
        self.assertEqual(gold["expected"], "refuse")

    def test_the_unspecified_band_fixture_declares_no_description(self):
        import rasterio
        with rasterio.open(
            self.fixtures / "heldout_bands_unspecified" / "unnamed.tif"
        ) as src:
            self.assertEqual(src.count, 3)
            self.assertEqual(list(src.descriptions), [None, None, None])

    def test_the_missing_file_fixture_contains_no_imagery(self):
        directory = self.fixtures / "heldout_missing_file"
        self.assertFalse(list(directory.glob("*.tif")))
        self.assertIn("orthomosaic_missing.tif", (directory / "prompt.txt").read_text(
            encoding="utf-8"
        ))

    def test_the_grass_fixture_separates_canopy_from_grass_and_soil(self):
        import rasterio
        base = self.fixtures / "heldout_grass_confusion"
        with rasterio.open(base / "grass_and_canopy.tif") as src:
            rgb = src.read().astype("float64") / 10000.0
        with rasterio.open(base / "canopy_reference.tif") as mask:
            canopy = mask.read(1).astype(bool)
        with rasterio.open(base / "grass_reference.tif") as mask:
            grass = mask.read(1).astype(bool)
        self.assertTrue(canopy.any() and grass.any())
        self.assertFalse(np.any(canopy & grass))
        # Grass and canopy share the green channel, which is what makes a green index
        # unusable here; bare soil is brighter than both.
        self.assertAlmostEqual(
            float(rgb[1][grass].mean()), float(rgb[1][canopy].mean()), places=2,
        )
        self.assertGreater(float(rgb[1][grass].mean()), float(rgb[1][canopy].mean()) * 0.9)
        soil = ~(canopy | grass)
        self.assertGreater(float(rgb[0][soil].mean()), float(rgb[0][canopy].mean()))
        gold = _read_json(self.gold / "heldout_grass_confusion.json")
        self.assertEqual(gold["canopy_pixels"], int(canopy.sum()))
        self.assertEqual(gold["grass_pixels"], int(grass.sum()))

    def test_the_artifact_type_fixture_provides_all_three_kinds(self):
        base = self.fixtures / "heldout_artifact_types"
        self.assertTrue((base / "plots.csv").is_file())
        self.assertTrue((base / "field_photo.png").is_file())
        self.assertTrue((base / "elevation.tif").is_file())
        from PIL import Image
        with Image.open(base / "field_photo.png") as image:
            self.assertEqual(image.size, (32, 32))
        import rasterio
        with rasterio.open(base / "elevation.tif") as src:
            self.assertEqual(src.count, 1)
            self.assertEqual((src.width, src.height), (64, 64))
        gold = _read_json(self.gold / "heldout_artifact_types.json")
        self.assertEqual(gold["photo_pixels"], 32 * 32)
        self.assertEqual(gold["elevation"]["width"], 64)

    def test_the_non_remote_sensing_gold_matches_its_csv(self):
        base = self.fixtures / "heldout_non_remote_sensing"
        totals: dict[str, int] = {}
        lines = (base / "sales.csv").read_text(encoding="utf-8").splitlines()[1:]
        for line in lines:
            region, _quarter, revenue = line.split(",")
            totals[region] = totals.get(region, 0) + int(revenue)
        gold = _read_json(self.gold / "heldout_non_remote_sensing.json")
        self.assertEqual(gold["region_totals"], totals)
        self.assertEqual(gold["highest"], max(totals, key=lambda name: totals[name]))

    def test_the_environment_fixture_requires_a_package_the_image_lacks(self):
        base = self.fixtures / "heldout_environment_missing"
        prompt = (base / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("geopandas", prompt)
        self.assertTrue((base / "points.csv").is_file())


class ComparisonPlannerTests(unittest.TestCase):
    def setUp(self):
        self.suite = load_suite()
        self.arms = [
            {"name": "a", "model": "model-a", "harness": "v1", "retrieval": "none"},
            {"name": "b", "model": "model-b", "harness": "v1", "retrieval": "none"},
        ]

    def test_the_plan_covers_every_case_and_repeat(self):
        matrix = plan(self.suite, self.arms)
        self.assertEqual(
            len(matrix["cells"]), len(case_ids(self.suite)) * 3 * len(self.arms),
        )
        self.assertEqual({cell["arm"] for cell in matrix["cells"]}, {"a", "b"})

    def test_an_arm_that_omits_an_axis_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            plan(self.suite, [{"name": "x", "model": "m", "harness": "v1"}])
        self.assertIn("retrieval", str(caught.exception))

    def test_an_unknown_case_is_refused(self):
        with self.assertRaises(ValueError):
            plan(self.suite, self.arms, cases=["heldout.invented"])


class ComparisonAggregationTests(unittest.TestCase):
    def _write_slot(self, root: Path, name: str, **overrides) -> None:
        trial = root / name
        trial.mkdir(parents=True, exist_ok=True)
        record = {
            "case_id": overrides.get("case", "heldout.rgb_canopy"),
            "repeat": overrides.get("repeat", 1),
            "status": overrides.get("status", "completed"),
            "configuration": overrides.get("configuration", {
                "model_digest": "model-a", "code_snapshot": "v1",
                "arm": "a", "retrieval_mode": "none",
            }),
            "checks": overrides.get("checks", {
                "artifact_grid": {"verdict": "pass"},
                "applicability_method": {"verdict": "pass"},
                "citation_evidence": {"verdict": "fail"},
                "delivery_viewable": {"verdict": "pass"},
            }),
        }
        (trial / "record.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8",
        )
        (trial / "trace.json").write_text(json.dumps({
            "steps": [{"seq": 1}, {"seq": 1}, {"seq": 2}],
            "token_usage": {"input_tokens": 1000, "output_tokens": 100},
            "configuration": record["configuration"],
        }, ensure_ascii=False), encoding="utf-8")

    def test_an_empty_root_yields_no_rows_rather_than_zeros(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(collect_slots(Path(temp)), [])
            self.assertEqual(aggregate([]), {})

    def test_unknown_verdicts_leave_the_denominator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_slot(root, "one", checks={
                "artifact_grid": {"verdict": "pass"},
                "artifact_stats": {"verdict": "unknown"},
                "applicability_method": {"verdict": "pass"},
                "citation_evidence": {"verdict": "fail"},
                "delivery_viewable": {"verdict": "not_applicable"},
            })
            report = aggregate(collect_slots(root))
            arm = report["a"]["metrics"]
            self.assertAlmostEqual(arm["artifact_accuracy"]["mean"], 1.0)
            self.assertEqual(arm["artifact_accuracy"]["graded_slots"], 1)
            self.assertAlmostEqual(arm["evidence_citation"]["mean"], 0.0)
            self.assertAlmostEqual(arm["delivery_success"]["mean"], 1.0)
            self.assertEqual(arm["loop_rate"]["mean_model_calls"], 2.0)
            self.assertEqual(arm["cost"]["mean_input_tokens"], 1000.0)

    def test_an_incomplete_run_is_not_reported_as_delivered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_slot(root, "one", status="paused")
            self._write_slot(root, "two", status="completed", repeat=2)
            report = aggregate(collect_slots(root))
            self.assertAlmostEqual(report["a"]["metrics"]["delivery_success"]["mean"], 0.5)
            self.assertEqual(
                report["a"]["metrics"]["delivery_success"]["total_slots"], 2,
            )

    def test_a_single_moved_axis_is_attributed(self):
        suite = load_suite()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_slot(root, "a1", configuration={
                "model_digest": "model-a", "code_snapshot": "v1",
                "arm": "a", "retrieval_mode": "none",
            })
            self._write_slot(root, "b1", configuration={
                "model_digest": "model-b", "code_snapshot": "v1",
                "arm": "b", "retrieval_mode": "none",
            })
            arms = aggregate(collect_slots(root))
            result = compare(arms, "a", "b", suite)
            self.assertEqual(result["axes_moved"], ["model"])
            self.assertEqual(result["attributable_axis"], "model")
            self.assertIsNotNone(result["deltas"]["artifact_accuracy"]["delta"])

    def test_two_moved_axes_are_reported_as_unattributable(self):
        suite = load_suite()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_slot(root, "a1", configuration={
                "model_digest": "model-a", "code_snapshot": "v1",
                "arm": "a", "retrieval_mode": "none",
            })
            self._write_slot(root, "b1", configuration={
                "model_digest": "model-b", "code_snapshot": "v2",
                "arm": "b", "retrieval_mode": "guides",
            })
            arms = aggregate(collect_slots(root))
            result = compare(arms, "a", "b", suite)
            self.assertEqual(sorted(result["axes_moved"]), ["harness", "model", "retrieval"])
            self.assertIsNone(result["attributable_axis"])
            self.assertIn("not identifiable", result["note"])


if __name__ == "__main__":
    unittest.main()
