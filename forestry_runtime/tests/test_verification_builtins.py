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

import unittest

from runtime.verification import (
    STATE_FAILED, STATE_UNVERIFIED, STATE_VERIFIED, RunFacts, VerificationService,
    default_registry,
)
from runtime.verification.builtins import raster_semantics

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


if __name__ == "__main__":
    unittest.main()
