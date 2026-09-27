"""A failure must be a fact about a resource, not a count of identical calls.

The four properties here are the ones the old counter could not deliver, each of them
observed as a real defect in a Run:

* a failure about a path stayed "open" while an unrelated directory was observed,
  because one global generation unlocked every recorded failure at once;
* a failure about a path that *had* been created looked identical to one that had
  not, so the retry was blocked when it was the only correct move left;
* an install that failed inside the job image -- operation started, side effect
  unknown -- was treated the same as a call refused before anything ran, so the model
  was told to "try something else" when it should have been told to reconcile;
* after a restart none of it survived, because the record lived in process memory.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.failure import (
    FailurePreflight, FailureStore, ResourceVersions,
    classify, evidence_from, resources_of, signature_of,
)
from runtime.failure.taxonomy import label_for


def _failure(code: str, stage: str = "preconditions", **extra) -> dict:
    return {
        "ok": False, "outcome_ok": False, "error": f"{code} reported",
        "failure": {"stage": stage, "code": code, **extra},
    }


def _store() -> tuple[FailureStore, Path]:
    root = Path(tempfile.mkdtemp())
    return FailureStore(root), root


class TaxonomyTests(unittest.TestCase):
    def test_known_pair_maps_to_the_framework_label(self):
        self.assertEqual(label_for("preconditions", "source_path_not_found"), "path_grounding")
        self.assertEqual(label_for("agent_control", "duplicate_failed_call"), "premature_stop")

    def test_unmapped_code_stays_unknown(self):
        """A cause is never invented to have something to write down."""
        self.assertEqual(label_for("execution", "something_new"), "algorithm_numeric")
        label, _, policy = classify({"stage": "weird", "code": "brand_new_code"})
        self.assertEqual(label, "unknown")
        self.assertEqual(policy, "model_decides")

    def test_a_started_operation_is_never_a_plain_retry(self):
        """Whatever the label says, a half-finished operation needs reconciling."""
        _, failure_class, policy = classify({
            "stage": "execution", "code": "install_failed",
            "operation_started": True, "side_effects": "partial_install",
        })
        self.assertEqual(failure_class, "side_effect_uncertain")
        self.assertEqual(policy, "reconcile_only")

    def test_permission_and_timeout_are_not_the_same_kind_of_blocked(self):
        self.assertEqual(classify({"code": "permission_denied"})[2], "requires_new_evidence")
        self.assertEqual(classify({"code": "timeout"})[2], "backoff")


class ResourceScopeTests(unittest.TestCase):
    def test_a_call_naming_no_path_is_not_about_every_path(self):
        self.assertEqual(resources_of({"packages": ["gdal"]})[0].kind, "distribution")
        self.assertEqual(resources_of({"timeout_seconds": 300}), [])

    def test_the_same_path_in_two_shapes_is_one_resource(self):
        first = resources_of({"path": "C:/data/A"})
        second = resources_of({"path": "C:\\data\\A\\"})
        self.assertEqual(first, second)

    def test_different_paths_are_different_resources(self):
        self.assertNotEqual(
            resources_of({"path": "C:/data/A"}), resources_of({"path": "C:/data/B"}),
        )

    def test_identical_arguments_give_one_strategy(self):
        left = signature_of("fs_read", {"path": "a"}, resources_of({"path": "a"}), [])
        right = signature_of("fs_read", {"path": "a"}, resources_of({"path": "a"}), [])
        self.assertEqual(left.id, right.id)

    def test_a_failure_names_the_assumption_it_falsified(self):
        evidence = evidence_from("fs_read", {"path": "C:/data/A"}, _failure("path_not_found"))
        self.assertTrue(evidence.invalid_assumptions)
        self.assertIn("exists", evidence.invalid_assumptions[0])
        self.assertEqual(evidence.retry_policy, "requires_new_evidence")


class PreflightTests(unittest.TestCase):
    def _preflight(self):
        store, _ = _store()
        return FailurePreflight(store, ResourceVersions())

    def test_the_same_deterministic_call_is_not_executed_twice(self):
        """Acceptance: deterministic same-strategy actual execution repeat = 0."""
        pre = self._preflight()
        failed = _failure("source_path_not_found", requested_path="C:/flight/A")
        self.assertIsNotNone(pre.record("fs_read", {"path": "C:/flight/A"}, failed))
        decision = pre.check("fs_read", {"path": "C:/flight/A"})
        self.assertEqual(decision.action, "decline")
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.label, "path_grounding")

    def test_an_unrelated_observation_does_not_reopen_the_failure(self):
        """Acceptance: unrelated-resource unlock = 0.

        This is the global-generation defect: observing directory B used to bump one
        counter that every recorded failure's fingerprint contained.
        """
        pre = self._preflight()
        pre.record("fs_read", {"path": "C:/flight/A"},
                   _failure("source_path_not_found", requested_path="C:/flight/A"))
        pre.observe({"path": "C:/flight/B"})
        decision = pre.check("fs_read", {"path": "C:/flight/A"})
        self.assertEqual(decision.action, "decline")

    def test_observing_the_failed_resource_reopens_it(self):
        """Acceptance: a relevant resource change must be able to re-verify."""
        pre = self._preflight()
        pre.record("fs_read", {"path": "C:/flight/A"},
                   _failure("source_path_not_found", requested_path="C:/flight/A"))
        pre.observe({"path": "C:/flight/A"})
        self.assertEqual(pre.check("fs_read", {"path": "C:/flight/A"}).action, "execute")

    def test_a_changed_argument_is_new_work(self):
        pre = self._preflight()
        pre.record("fs_read", {"path": "C:/flight/A"}, _failure("path_not_found"))
        self.assertEqual(pre.check("fs_read", {"path": "C:/flight/B"}).action, "execute")

    def test_an_uncertain_side_effect_is_reconciled_not_replayed(self):
        pre = self._preflight()
        failed = _failure("install_failed", stage="execution",
                          operation_started=True, side_effects="partial_install",
                          missing_from_manifest=["gdal"])
        pre.record("dependency_install", {"packages": ["gdal"]}, failed)
        decision = pre.check("dependency_install", {"packages": ["gdal"]})
        self.assertEqual(decision.action, "reconcile")
        self.assertTrue(decision.blocked)
        self.assertIn("already have taken effect", decision.reason)

    def test_a_transient_failure_does_not_hard_block_the_call(self):
        """A timeout is not evidence that the call is wrong, so it must not be blocked."""
        pre = self._preflight()
        pre.record("code_run", {"code": "x"}, _failure("timeout", stage="execution"))
        self.assertEqual(pre.check("code_run", {"code": "x"}).action, "execute")

    def test_records_survive_a_new_store_over_the_same_workspace(self):
        """The record outlives the process, so a resumed Run can still ask."""
        store, root = _store()
        pre = FailurePreflight(store, ResourceVersions())
        pre.record("fs_read", {"path": "C:/flight/A"}, _failure("path_not_found"))
        reopened = FailurePreflight(FailureStore(root), ResourceVersions())
        self.assertEqual(reopened.check("fs_read", {"path": "C:/flight/A"}).action, "decline")

    def test_repeats_collapse_into_one_fact(self):
        store, _ = _store()
        pre = FailurePreflight(store, ResourceVersions())
        pre.record("fs_read", {"path": "C:/flight/A"}, _failure("path_not_found"))
        pre.record("fs_read", {"path": "C:/flight/A"}, _failure("path_not_found"))
        self.assertEqual(len(store.all()), 1)
        self.assertEqual(store.all()[0].occurrences, 2)


if __name__ == "__main__":
    unittest.main()
