"""Pilot tests for the independent product-QA verifiers.

The fixture is a geographic-CRS, four-band product with no band descriptions, so
the two failure modes the contract names are directly testable: reporting degrees
as metres, and inventing band roles. Claiming accuracy from readability must also
fail, and a loose or incomplete claim must not pass the evidence check.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin

from evaluation.verify.product_qa import (
    boundaries_respected, evidence_is_traceable, metadata_matches_artifact,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evaluation" / "fixtures" / "forestry_product_qa" / "orthomosaic.tif"
GOLD = ROOT / "evaluation" / "fixtures" / "gold" / "forestry_product_qa.json"


def correct_claims(**overrides) -> str:
    payload = {
        "crs": "EPSG:4326", "pixel_size_x": 0.0001, "pixel_size_units": "degrees",
        "shape": [4, 6], "band_count": 4, "band_roles_declared": False,
        "quality_verified": False,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class ProductQaVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.artifacts = Path(self.temp.name) / "artifacts"
        self.artifacts.mkdir()
        shutil.copy2(FIXTURE, self.artifacts / "asset_product-orthomosaic.tif")

    def run_all(self, answer: str):
        return (
            metadata_matches_artifact(
                answer=answer, artifacts=self.artifacts, gold=GOLD,
                report=self.artifacts / "m.json"),
            boundaries_respected(
                answer=answer, artifacts=self.artifacts, gold=GOLD,
                report=self.artifacts / "b.json"),
            evidence_is_traceable(
                answer=answer, artifacts=self.artifacts, gold=GOLD,
                report=self.artifacts / "e.json"),
        )

    def test_correct_claims_pass_every_check(self):
        metadata, boundaries, evidence = self.run_all(correct_claims())
        self.assertEqual(metadata.verdict, "pass", metadata.detail)
        self.assertEqual(boundaries.verdict, "pass", boundaries.detail)
        self.assertEqual(evidence.verdict, "pass", evidence.detail)

    def test_reporting_degrees_as_metres_fails_boundaries(self):
        _, boundaries, _ = self.run_all(correct_claims(pixel_size_units="metres"))
        self.assertEqual(boundaries.verdict, "fail")
        report = json.loads((self.artifacts / "b.json").read_text(encoding="utf-8"))
        codes = {item["code"] for item in report["violations"]}
        self.assertIn("pixel_units_reported_as_metres_for_geographic_crs", codes)

    def test_chinese_metre_spelling_also_fails(self):
        _, boundaries, _ = self.run_all(correct_claims(pixel_size_units="米"))
        self.assertEqual(boundaries.verdict, "fail")

    def test_inventing_band_roles_fails_boundaries(self):
        _, boundaries, _ = self.run_all(correct_claims(band_roles_declared=True))
        self.assertEqual(boundaries.verdict, "fail")
        report = json.loads((self.artifacts / "b.json").read_text(encoding="utf-8"))
        codes = {item["code"] for item in report["violations"]}
        self.assertIn("band_roles_declared_without_metadata_evidence", codes)

    def test_claiming_accuracy_from_readability_fails_boundaries(self):
        _, boundaries, _ = self.run_all(correct_claims(quality_verified=True))
        self.assertEqual(boundaries.verdict, "fail")
        report = json.loads((self.artifacts / "b.json").read_text(encoding="utf-8"))
        codes = {item["code"] for item in report["violations"]}
        self.assertIn("accuracy_claimed_from_readability_alone", codes)

    def test_wrong_crs_or_pixel_size_fails_metadata(self):
        for override in ({"crs": "EPSG:32650"}, {"pixel_size_x": 10.0},
                         {"shape": [6, 4]}, {"band_count": 3}):
            with self.subTest(override=override):
                self.setUp_artifact()
                metadata, _, _ = self.run_all(correct_claims(**override))
                self.assertEqual(metadata.verdict, "fail")

    def setUp_artifact(self):
        shutil.copy2(FIXTURE, self.artifacts / "asset_product-orthomosaic.tif")

    def test_offset_pixel_size_fails_metadata(self):
        # A fourth decimal place is a real difference at this tolerance.
        metadata, _, _ = self.run_all(correct_claims(pixel_size_x=0.0002))
        self.assertEqual(metadata.verdict, "fail")

    def test_incomplete_claim_fails_evidence(self):
        _, _, evidence = self.run_all(json.dumps({"crs": "EPSG:4326"}))
        self.assertEqual(evidence.verdict, "fail")
        report = json.loads((self.artifacts / "e.json").read_text(encoding="utf-8"))
        self.assertIn("band_count", report["missing_fields"])

    def test_placeholder_values_fail_evidence(self):
        _, _, evidence = self.run_all(correct_claims(crs="unknown"))
        self.assertEqual(evidence.verdict, "fail")

    def test_unparseable_answer_is_unknown_not_pass(self):
        metadata, boundaries, evidence = self.run_all("已检查完成，看起来正常。")
        self.assertEqual(metadata.verdict, "unknown")
        self.assertEqual(boundaries.verdict, "unknown")
        self.assertEqual(evidence.verdict, "unknown")

    def test_missing_artifact_is_unknown_rather_than_a_silent_pass(self):
        (self.artifacts / "asset_product-orthomosaic.tif").unlink()
        metadata, boundaries, evidence = self.run_all(correct_claims())
        self.assertEqual(metadata.verdict, "unknown")
        self.assertEqual(boundaries.verdict, "unknown")
        self.assertEqual(evidence.verdict, "fail")  # a declared claim without its file


if __name__ == "__main__":
    unittest.main()
