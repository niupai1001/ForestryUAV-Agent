"""Verification decisions affect ranking but never appear in model context."""
from pathlib import Path
import unittest

from runtime.capabilities.domain_guides.service import DomainGuideCapability
from runtime.context import _guide_catalogue_part
from runtime.domain_guides import load_guides, match_guides
from runtime.guide_verification import extract_claims, read_ledger, verification_quality


ROOT = Path(__file__).resolve().parent.parent
GUIDES = ROOT / "knowledge" / "guides"
LEDGER = ROOT / "evaluation" / "grounded_v1" / "GUIDE_VERIFICATION.md"


class GuideVerificationTests(unittest.TestCase):
    def test_every_current_assertion_has_a_ledger_row(self):
        rows = read_ledger(LEDGER)
        guides = load_guides([GUIDES])
        self.assertGreaterEqual(len(guides), 30)
        current = [claim for guide in guides for claim in extract_claims(guide.id, guide.body)]
        self.assertGreaterEqual(len(current), 300)
        for claim in current:
            with self.subTest(claim=claim.id):
                self.assertIn(claim.id, rows)
                self.assertEqual(rows[claim.id]["guide"], claim.guide_id)
                self.assertEqual(rows[claim.id]["claim"], claim.text)
        self.assertTrue(any(row["status"] == "wrong" for row in rows.values()))

    def test_verified_rows_have_reviewable_sources(self):
        rows = read_ledger(LEDGER)
        verified = [row for row in rows.values() if row["status"] == "verified"]
        self.assertGreaterEqual(len(verified), 60)
        for row in verified:
            self.assertTrue(row["source"].startswith("https://"))
            self.assertEqual(row["action"], "保留")

    def test_changed_claim_loses_verified_credit(self):
        guide = load_guides([GUIDES])[0]
        claim = extract_claims(guide.id, guide.body)[0]
        ledger = {claim.id: {"guide": guide.id, "claim": claim.text,
                             "status": "verified", "source": "https://example.org/paper"}}
        self.assertGreater(verification_quality(guide.id, claim.text, ledger), 0.6)
        changed = "新增条件：" + claim.text
        self.assertEqual(verification_quality(guide.id, changed, ledger), 0.6)

    def test_verification_labels_stay_out_of_model_context(self):
        catalogue, _ = _guide_catalogue_part([type("Tool", (), {"name": "domain_guide"})()])
        results = DomainGuideCapability().domain_guide(query="RGB 树冠覆盖率")
        for label in ("verified", "unverified", "wrong", "verification_quality"):
            self.assertNotIn(label, catalogue)
            self.assertNotIn(label, str(results))
        self.assertEqual(match_guides("RGB 树冠覆盖率")[0].id, "rgb-canopy-cover")
        self.assertEqual(match_guides("PROSAIL 反演叶面积指数")[0].id,
                         "prosail-applicability")


if __name__ == "__main__":
    unittest.main()
