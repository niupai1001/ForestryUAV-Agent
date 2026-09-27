"""The built-in verifiers can now actually say yes -- and no.

Before these existed the registry was empty, so the semantic layer was always
``not_run`` and every delivery was ``delivered_unverified``. The three-state result
was correct and useless: nothing could ever reach ``verified_complete``. These tests
pin the two things that make it real:

* a product **with** a declaration is held to it -- right grid, right range, right
  encoded fraction gives ``verified_complete``; any one of them broken gives
  ``failed``;
* a product **without** a declaration is *not* invented one. No declaration means
  ``not_run`` and therefore ``delivered_unverified``, never a pass. A verifier that
  guessed "``_ndvi_`` in the filename, so the range must be [-1,1]" would be right
  until the day it was not, and nothing would say so.

Also pinned: the two fractions that are easy to confuse. How much of a grid carries
data and how much a mask selects are different numbers, and treating a fully valid
grid as "the mask selected everything" would fail every correct mask.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.verification import (
    STATE_FAILED, STATE_UNVERIFIED, STATE_VERIFIED, RunFacts, VerificationService,
    default_registry,
)
from runtime.verification.builtins import geojson_semantics, raster_semantics

GRID = {"crs": "EPSG:32650", "width": 100, "height": 100, "pixel_size": [0.5, 0.5]}
QA = {
    "crs": "EPSG:32650", "width": 100, "height": 100, "pixel_size": [0.5, 0.5],
    "bands": [{"min": -0.2, "max": 0.8, "mean": 0.3, "sampled_valid_pixels": 9000}],
    "valid_fraction_sampled": 0.9,
}
OK_CHECKS = {"file_exists": True, "server_can_read": True,
             "producer_reported_success": True}


def _artifact(semantics=None, qa=None, name="a_ndvi.tif") -> dict:
    artifact = {
        "asset_id": "as_1", "name": name, "media_type": "image/tiff",
        "artifact_kind": "file", "checks": dict(OK_CHECKS),
    }
    if qa is not None:
        artifact["product_qa"] = qa
    if semantics is not None:
        artifact["metadata"] = {"semantics": semantics}
    return artifact


def _service():
    return VerificationService(default_registry())


class DeclaredSemanticsTest(unittest.TestCase):
    def test_a_product_held_to_its_declaration_can_be_verified(self) -> None:
        artifact = _artifact({
            "quantity": "ndvi", "kind": "index", "valid_range": [-1.0, 1.0],
            "grid": dict(GRID),
        }, qa=dict(QA))
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_VERIFIED, report.reasons)
        self.assertTrue(report.verified)

    def test_a_product_off_the_declared_grid_is_failed(self) -> None:
        artifact = _artifact({
            "quantity": "ndvi", "kind": "index", "valid_range": [-1.0, 1.0],
            "grid": {**GRID, "crs": "EPSG:4326"},
        }, qa=dict(QA))
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)
        self.assertTrue(any("grid" in check.name for check in report.failed_checks()))

    def test_a_value_outside_the_declared_range_is_failed(self) -> None:
        hot = dict(QA)
        hot["bands"] = [{"min": -0.2, "max": 1.7, "mean": 0.3}]
        artifact = _artifact({
            "quantity": "ndvi", "kind": "index", "valid_range": [-1.0, 1.0],
            "grid": dict(GRID),
        }, qa=hot)
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)
        self.assertTrue(any("range" in check.name for check in report.failed_checks()))

    def test_a_claimed_fraction_the_file_contradicts_is_failed(self) -> None:
        mask_qa = {**QA, "bands": [{"min": 0.0, "max": 1.0, "mean": 0.11}]}
        artifact = _artifact({
            "quantity": "canopy_candidate_mask", "kind": "mask",
            "valid_range": [0.0, 1.0], "fractions": {"canopy": 0.62},
            "grid": dict(GRID),
        }, qa=mask_qa, name="a_canopy.tif")
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)
        self.assertTrue(any("fraction" in check.name for check in report.failed_checks()))

    def test_a_mask_whose_claim_matches_the_file_is_verified(self) -> None:
        mask_qa = {**QA, "bands": [{"min": 0.0, "max": 1.0, "mean": 0.62}]}
        artifact = _artifact({
            "quantity": "canopy_candidate_mask", "kind": "mask",
            "valid_range": [0.0, 1.0], "fractions": {"canopy": 0.62},
            "grid": dict(GRID),
        }, qa=mask_qa, name="a_canopy.tif")
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_VERIFIED, report.reasons)


class NoDeclarationTest(unittest.TestCase):
    def test_no_declaration_means_not_checked_not_passed(self) -> None:
        report = _service().evaluate(RunFacts(artifacts=[_artifact(qa=dict(QA))]))
        self.assertEqual(report.state, STATE_UNVERIFIED)
        self.assertTrue(any("declares no semantics" in reason for reason in report.reasons))

    def test_no_measured_metadata_means_not_checked(self) -> None:
        report = _service().evaluate(RunFacts(artifacts=[
            _artifact({"quantity": "ndvi", "kind": "index", "valid_range": [-1.0, 1.0]}),
        ]))
        self.assertEqual(report.state, STATE_UNVERIFIED)

    def test_the_range_is_never_inferred_from_the_filename(self) -> None:
        """`_ndvi_` in the name is not a declaration, and must not be read as one."""
        checks = raster_semantics(_artifact(qa=dict(QA), name="x_ndvi_r3_n4.tif"), "")
        self.assertTrue(checks)
        self.assertTrue(all(check.outcome == "not_run" for check in checks))

    def test_a_non_raster_is_not_this_verifiers_business(self) -> None:
        artifact = {"name": "report.csv", "media_type": "text/csv",
                    "checks": dict(OK_CHECKS)}
        self.assertEqual(raster_semantics(artifact, ""), [])


class FractionConfusionTest(unittest.TestCase):
    def test_a_fully_valid_grid_is_not_a_mask_that_selected_everything(self) -> None:
        """valid_fraction is how much carries data; the mean is how much is selected."""
        full_qa = {**QA, "valid_fraction_sampled": 1.0,
                   "bands": [{"min": 0.0, "max": 1.0, "mean": 0.31}]}
        artifact = _artifact({
            "quantity": "canopy_candidate_mask", "kind": "mask",
            "valid_range": [0.0, 1.0], "fractions": {"canopy": 0.31},
            "grid": dict(GRID),
        }, qa=full_qa, name="a_canopy.tif")
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_VERIFIED, report.reasons)

    def test_a_mask_that_selects_everything_failed_to_separate(self) -> None:
        all_qa = {**QA, "bands": [{"min": 1.0, "max": 1.0, "mean": 1.0}]}
        artifact = _artifact({
            "quantity": "canopy_candidate_mask", "kind": "mask",
            "valid_range": [0.0, 1.0], "grid": dict(GRID),
        }, qa=all_qa, name="a_canopy.tif")
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)

    def test_a_mask_that_selects_nothing_failed_to_separate(self) -> None:
        none_qa = {**QA, "bands": [{"min": 0.0, "max": 0.0, "mean": 0.0}]}
        artifact = _artifact({
            "quantity": "canopy_candidate_mask", "kind": "mask",
            "valid_range": [0.0, 1.0], "grid": dict(GRID),
        }, qa=none_qa, name="a_canopy.tif")
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)


class LabelRasterTest(unittest.TestCase):
    """A label raster is not a mask: ids, not a selected fraction."""

    def test_ids_within_the_declared_count_verify(self) -> None:
        # The tree-candidates raster declares [0, N] where N is the candidate count.
        # Its pixel mean is an average of ids and must not be read as a fraction.
        label_qa = {**QA, "bands": [{"min": 1.0, "max": 2.0, "mean": 1.4}]}
        artifact = _artifact({
            "quantity": "upper_canopy_candidate_id", "kind": "label",
            "valid_range": [0, 2], "grid": dict(GRID),
        }, qa=label_qa, name="plot_tree_candidates.tif")
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_VERIFIED, report.reasons)

    def test_an_id_above_the_declared_count_is_failed(self) -> None:
        # The mapping step renumbers ids to 1..N; an id above N in the file means the
        # file and the count are not from the same computation.
        label_qa = {**QA, "bands": [{"min": 1.0, "max": 7.0, "mean": 1.4}]}
        artifact = _artifact({
            "quantity": "upper_canopy_candidate_id", "kind": "label",
            "valid_range": [0, 2], "grid": dict(GRID),
        }, qa=label_qa, name="plot_tree_candidates.tif")
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)
        self.assertTrue(any("range" in check.name for check in report.failed_checks()))


def _write_geojson(path: Path, features: list, *, crs: str = "EPSG:4326") -> None:
    path.write_text(json.dumps({
        "type": "FeatureCollection", "features": features,
        "coordinate_reference_system": crs,
    }, ensure_ascii=False), encoding="utf-8")


def _polygon(value: float = 1.0) -> dict:
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [0.0, value], [value, value], [value, 0.0],
                             [0.0, 0.0]]],
        },
        "properties": {"candidate_id": 1},
    }


def _geojson_artifact(semantics=None, name="plot_candidate_crowns.geojson") -> dict:
    artifact = {
        "asset_id": "as_2", "name": name, "media_type": "application/geo+json",
        "artifact_kind": "file", "checks": dict(OK_CHECKS),
    }
    if semantics is not None:
        artifact["metadata"] = {"semantics": semantics}
    return artifact


class GeojsonSemanticsTest(unittest.TestCase):
    def test_a_count_the_file_confirms_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "crowns.geojson"
            _write_geojson(path, [_polygon(), _polygon(2.0)])
            artifact = _geojson_artifact({
                "quantity": "tree_candidate_crowns", "kind": "features",
                "feature_count": 2, "geometry_type": "Polygon",
            })
            artifact["path"] = str(path)
            report = _service().evaluate(RunFacts(artifacts=[artifact]))
            self.assertEqual(report.state, STATE_VERIFIED, report.reasons)

    def test_a_count_the_file_contradicts_is_failed(self) -> None:
        # "Found 5 candidate trees" with 2 polygons in the file is the drift this
        # exists to catch: the summary, the file and the model's words are three
        # copies of one number, and copies drift.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "crowns.geojson"
            _write_geojson(path, [_polygon(), _polygon(2.0)])
            artifact = _geojson_artifact({
                "quantity": "tree_candidate_crowns", "kind": "features",
                "feature_count": 5, "geometry_type": "Polygon",
            })
            artifact["path"] = str(path)
            report = _service().evaluate(RunFacts(artifacts=[artifact]))
            self.assertEqual(report.state, STATE_FAILED)
            self.assertTrue(any("count" in check.name for check in report.failed_checks()))

    def test_a_geometry_type_mismatch_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "crowns.geojson"
            _write_geojson(path, [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                "properties": {},
            }])
            artifact = _geojson_artifact({
                "quantity": "tree_candidate_crowns", "kind": "features",
                "feature_count": 1, "geometry_type": "Polygon",
            })
            artifact["path"] = str(path)
            report = _service().evaluate(RunFacts(artifacts=[artifact]))
            self.assertEqual(report.state, STATE_FAILED)
            self.assertTrue(any("geometry" in check.name for check in report.failed_checks()))

    def test_an_unparseable_file_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "crowns.geojson"
            path.write_text("{not json", encoding="utf-8")
            artifact = _geojson_artifact({
                "quantity": "tree_candidate_crowns", "kind": "features",
                "feature_count": 1,
            })
            artifact["path"] = str(path)
            checks = geojson_semantics(artifact, "")
            self.assertEqual(checks[0].outcome, "failed")
            self.assertIn("parse", checks[0].detail)

    def test_no_declaration_means_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "crowns.geojson"
            _write_geojson(path, [_polygon()])
            artifact = _geojson_artifact()
            artifact["path"] = str(path)
            report = _service().evaluate(RunFacts(artifacts=[artifact]))
            self.assertEqual(report.state, STATE_UNVERIFIED)
            self.assertTrue(any(
                "declares no semantics" in reason for reason in report.reasons))

    def test_a_file_the_verifier_cannot_reach_is_not_run(self) -> None:
        artifact = _geojson_artifact({
            "quantity": "tree_candidate_crowns", "kind": "features",
            "feature_count": 1,
        })
        checks = geojson_semantics(artifact, "")
        self.assertEqual(checks[0].outcome, "not_run")

    def test_a_non_geojson_is_not_this_verifiers_business(self) -> None:
        artifact = {"name": "crowns.csv", "media_type": "text/csv",
                    "checks": dict(OK_CHECKS)}
        self.assertEqual(geojson_semantics(artifact, ""), [])


class MixedDeliverablesTest(unittest.TestCase):
    """One Run, one raster and one GeoJSON -- both must find their own verifier."""

    def test_both_kinds_are_verified_in_one_run(self) -> None:
        # raster_semantics is registered first and covers everything, so if the
        # registry consulted only the first match the GeoJSON would be judged by a
        # verifier that cannot read it and would never hear its own.
        with tempfile.TemporaryDirectory() as temporary:
            crowns_path = Path(temporary) / "crowns.geojson"
            _write_geojson(crowns_path, [_polygon()])
            raster = _artifact({
                "quantity": "ndvi", "kind": "index", "valid_range": [-1.0, 1.0],
                "grid": dict(GRID),
            }, qa=dict(QA))
            crowns = _geojson_artifact({
                "quantity": "tree_candidate_crowns", "kind": "features",
                "feature_count": 1, "geometry_type": "Polygon",
            })
            crowns["path"] = str(crowns_path)
            report = _service().evaluate(RunFacts(artifacts=[raster, crowns]))
            self.assertEqual(report.state, STATE_VERIFIED, report.reasons)
            names = [check.name for check in report.checks]
            self.assertTrue(any("geojson" in name for name in names), names)
            self.assertTrue(any("raster" in name for name in names), names)

    def test_a_broken_geojson_fails_the_run_even_with_a_perfect_raster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            crowns_path = Path(temporary) / "crowns.geojson"
            _write_geojson(crowns_path, [_polygon(), _polygon(2.0)])
            raster = _artifact({
                "quantity": "ndvi", "kind": "index", "valid_range": [-1.0, 1.0],
                "grid": dict(GRID),
            }, qa=dict(QA))
            crowns = _geojson_artifact({
                "quantity": "tree_candidate_crowns", "kind": "features",
                "feature_count": 9, "geometry_type": "Polygon",
            })
            crowns["path"] = str(crowns_path)
            report = _service().evaluate(RunFacts(artifacts=[raster, crowns]))
            self.assertEqual(report.state, STATE_FAILED)


class BandRangeTest(unittest.TestCase):
    """A multi-quantity product is checked band by band, not by one global range.

    An inversion raster carries a leaf area index, a chlorophyll content and an RMSE
    side by side. One min/max over all of them would pass a file whose LAI band holds
    reflectance -- so the declaration is per band, and a band that cannot be matched
    is ``not_run`` rather than assumed to be fine.
    """

    @staticmethod
    def _qa() -> dict:
        return {
            "crs": "EPSG:32650", "width": 10, "height": 10,
            "pixel_size": [0.5, 0.5], "valid_fraction_sampled": 0.9,
            "bands": [
                {"index": 1, "description": "prosail_lai", "min": 0.4, "max": 5.1},
                {"index": 2, "description": "prosail_cab", "min": 12.0, "max": 61.0},
                {"index": 3, "description": "spectral_RMSE", "min": 0.01, "max": 0.4},
            ],
        }

    def _checks(self, bands, qa=None):
        artifact = _artifact(
            {"quantity": "prosail_inversion", "kind": "continuous", "bands": bands},
            qa=qa if qa is not None else self._qa(),
            name="inversion.tif",
        )
        return [check for check in raster_semantics(artifact)
                if check.name == "raster_band_range"]

    def test_each_band_is_held_to_its_own_bound(self) -> None:
        checks = self._checks({
            "prosail_lai": {"valid_range": [0.0, 8.0]},
            "prosail_cab": {"valid_range": [0.0, 80.0]},
            "spectral_RMSE": {"valid_range": [0.0, None]},
        })
        self.assertEqual(3, len(checks))
        self.assertTrue(all(check.outcome == "passed" for check in checks),
                        [check.detail for check in checks])

    def test_one_band_out_of_range_fails_only_that_band(self) -> None:
        checks = self._checks({
            "prosail_lai": {"valid_range": [0.0, 8.0]},
            "prosail_cab": {"valid_range": [0.0, 30.0]},
        })
        outcomes = {check.outcome for check in checks}
        self.assertIn("failed", outcomes)
        self.assertIn("passed", outcomes)

    def test_a_negative_rmse_fails(self) -> None:
        qa = self._qa()
        qa["bands"][2]["min"] = -0.2
        checks = self._checks({"spectral_RMSE": {"valid_range": [0.0, None]}}, qa=qa)
        self.assertEqual(["failed"], [check.outcome for check in checks])

    def test_a_declared_band_that_was_not_measured_is_not_run(self) -> None:
        checks = self._checks({"prosail_ant": {"valid_range": [0.0, 5.0]}})
        self.assertEqual(["not_run"], [check.outcome for check in checks])

    def test_bands_are_matched_by_name_not_by_position(self) -> None:
        """A product that reorders its bands must not be checked against the wrong claim."""
        qa = self._qa()
        qa["bands"] = list(reversed(qa["bands"]))
        checks = self._checks({
            "prosail_lai": {"valid_range": [0.0, 8.0]},
            "spectral_RMSE": {"valid_range": [0.0, 0.1]},
        }, qa=qa)
        outcomes = [check.outcome for check in checks]
        self.assertEqual(["passed", "failed"], outcomes,
                         [check.detail for check in checks])

    def test_a_product_without_per_band_claims_gets_no_extra_checks(self) -> None:
        artifact = _artifact(
            {"quantity": "ndvi", "kind": "index", "valid_range": [-1.0, 1.0]},
            qa=dict(QA), name="a_ndvi.tif",
        )
        self.assertEqual([], [check for check in raster_semantics(artifact)
                              if check.name == "raster_band_range"])


if __name__ == "__main__":
    unittest.main()
