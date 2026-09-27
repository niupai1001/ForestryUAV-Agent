"""The Runtime, not the model, decides whether the request has been met.

The defect this exists to prevent: a Run could finish early because "done" was never
written down anywhere the Runtime could check. A tool succeeding, a file existing, and
one branch of a two-part request being finished all read as progress, and the model --
which always believes it is finished -- had the only vote.

So what the user asked for is split into requirements, and two rules make them mean
something:

* the model cannot mark one satisfied. It may open one, and it may mark one blocked,
  but satisfaction is settled by the Runtime from the observation ledger. Letting the
  model write its own completion ticket is the premature-stop defect with a place to
  live;
* a requirement is settled only by evidence that exists. An uncited requirement can
  never settle, so "I have decided this is done" has nowhere to be recorded.

Requirements live on the existing WorkPlan rather than in a second structure: the Run
already has one recorded account of its task, and two of them would drift.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.plan import ObservationLedger, PlanError, PlanStore


def _store():
    root = Path(tempfile.mkdtemp())
    ledger = ObservationLedger(root / ".runtime" / "observations.json")
    return PlanStore(root, ledger), ledger


class RequirementTests(unittest.TestCase):
    def test_a_requirement_starts_open(self):
        store, _ = _store()
        plan = store.revise(objective="check two directories",
                            requirements=[{"id": "r1", "text": "检查目录 A"},
                                          {"id": "r2", "text": "检查目录 B"}])
        self.assertEqual(plan.completion_state(), "open")
        self.assertEqual([item["id"] for item in plan.open_requirements()], ["r1", "r2"])

    def test_the_model_cannot_mark_a_requirement_satisfied(self):
        store, _ = _store()
        with self.assertRaises(PlanError) as caught:
            store.revise(requirements=[{"id": "r1", "text": "检查目录 A",
                                        "status": "satisfied"}])
        self.assertEqual(caught.exception.failure_details["code"],
                         "plan_requirement_status_not_writable")

    def test_the_model_can_mark_a_requirement_blocked(self):
        store, _ = _store()
        plan = store.revise(requirements=[{"id": "r1", "text": "检查目录 A",
                                           "status": "blocked"}])
        self.assertEqual(plan.completion_state(), "blocked")

    def test_duplicate_ids_are_refused(self):
        store, _ = _store()
        with self.assertRaises(PlanError):
            store.revise(requirements=[{"id": "r1", "text": "A"}, {"id": "r1", "text": "B"}])

    def test_a_requirement_without_text_is_refused(self):
        store, _ = _store()
        with self.assertRaises(PlanError):
            store.revise(requirements=[{"id": "r1"}])

    def test_ids_are_assigned_when_omitted(self):
        store, _ = _store()
        plan = store.revise(requirements=[{"text": "A"}, {"text": "B"}])
        self.assertEqual([item["id"] for item in plan.requirements], ["r1", "r2"])


class SettlementTests(unittest.TestCase):
    def test_a_requirement_settles_when_its_observation_exists(self):
        store, ledger = _store()
        observation = ledger.record("fs_list", {"path": "A"}, ok=True, summary="listed A")
        store.revise(requirements=[{"id": "r1", "text": "检查目录 A",
                                    "evidence": observation}])
        settled = store.settle_requirements()
        self.assertEqual(settled.requirements[0]["status"], "satisfied")
        self.assertEqual(settled.completion_state(), "complete")

    def test_a_requirement_does_not_settle_before_its_evidence_exists(self):
        store, ledger = _store()
        store.revise(requirements=[{"id": "r1", "text": "检查目录 A",
                                    "evidence": "obs_neverproduced"}])
        settled = store.settle_requirements()
        self.assertEqual(settled.requirements[0]["status"], "open")
        self.assertEqual(settled.completion_state(), "open")

    def test_one_settled_requirement_does_not_complete_the_request(self):
        """Acceptance: finishing one branch must not finish the request."""
        store, ledger = _store()
        seen = ledger.record("fs_list", {"path": "A"}, ok=True, summary="listed A")
        store.revise(requirements=[{"id": "r1", "text": "检查目录 A", "evidence": seen},
                                   {"id": "r2", "text": "检查目录 B"}])
        settled = store.settle_requirements()
        self.assertEqual(settled.completion_state(), "open")
        self.assertEqual([item["id"] for item in settled.open_requirements()], ["r2"])

    def test_uncited_requirements_never_settle(self):
        store, _ = _store()
        store.revise(requirements=[{"id": "r1", "text": "检查目录 A"}])
        self.assertEqual(store.settle_requirements().requirements[0]["status"], "open")

    def test_a_document_body_settles_a_requirement(self):
        store, ledger = _store()
        ledger.record("knowledge_read", {"guide_id": "g"}, ok=True,
                      documents=["guide://g"])
        store.revise(requirements=[{"id": "r1", "text": "读 guide",
                                    "evidence": "guide://g"}])
        self.assertEqual(store.settle_requirements().requirements[0]["status"], "satisfied")

    def test_settling_is_idempotent(self):
        store, ledger = _store()
        seen = ledger.record("fs_list", {"path": "A"}, ok=True)
        store.revise(requirements=[{"id": "r1", "text": "A", "evidence": seen}])
        first = store.settle_requirements()
        second = store.settle_requirements()
        self.assertEqual(first.revision, second.revision)


class DurabilityTests(unittest.TestCase):
    def test_open_requirements_survive_a_reopened_store(self):
        """Acceptance: a restart or compaction must not lose what is still owed."""
        store, ledger = _store()
        store.revise(objective="two directories",
                     requirements=[{"id": "r1", "text": "A"}, {"id": "r2", "text": "B"}],
                     constraints=["source_read_only"])
        reopened = PlanStore(store.workspace, ObservationLedger(
            store.workspace / ".runtime" / "observations.json"))
        plan = reopened.load()
        self.assertEqual(len(plan.requirements), 2)
        self.assertEqual(plan.completion_state(), "open")
        self.assertEqual(plan.constraints, ["source_read_only"])


class RenderingTests(unittest.TestCase):
    def test_the_rendered_plan_says_what_is_still_open(self):
        store, _ = _store()
        plan = store.revise(requirements=[{"id": "r1", "text": "A"},
                                          {"id": "r2", "text": "B"}])
        rendered = plan.render()
        self.assertIn("Requirements", rendered)
        self.assertIn("[open] r1", rendered)
        self.assertIn("Not finished", rendered)
        self.assertIn("r2", rendered)

    def test_a_finished_request_says_so(self):
        store, ledger = _store()
        seen = ledger.record("fs_list", {"path": "A"}, ok=True)
        store.revise(requirements=[{"id": "r1", "text": "A", "evidence": seen}])
        rendered = store.settle_requirements().render()
        self.assertIn("[done] r1", rendered)
        self.assertNotIn("Not finished", rendered)

    def test_blockers_and_constraints_are_rendered(self):
        store, _ = _store()
        plan = store.revise(requirements=[{"id": "r1", "text": "A", "status": "blocked"}],
                            constraints=["source_read_only"],
                            blockers=["grant 未授权"],
                            waiting_for=["job_7"])
        rendered = plan.render()
        self.assertIn("Constraints", rendered)
        self.assertIn("Blockers", rendered)
        self.assertIn("Waiting for", rendered)
        self.assertIn("Blocked", rendered)


if __name__ == "__main__":
    unittest.main()
