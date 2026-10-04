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


class VersionPersistenceTests(unittest.TestCase):
    """The version table must survive the process, like the records do.

    A resumed Run builds a fresh preflight; a version table that reset to zero
    with it would unlock every recorded failure whose version sat above zero --
    the restart-shaped twin of the old global-generation defect.
    """

    def test_versions_survive_a_new_preflight_over_the_same_workspace(self):
        store, root = _store()
        pre = FailurePreflight(store)  # store-backed versions, written through
        pre.record("fs_read", {"path": "C:/flight/A"}, _failure("path_not_found"))
        self.assertEqual(pre.check("fs_read", {"path": "C:/flight/A"}).action, "decline")
        pre.observe({"path": "C:/flight/A"})
        self.assertEqual(pre.check("fs_read", {"path": "C:/flight/A"}).action, "execute")
        reopened = FailurePreflight(FailureStore(root))
        self.assertEqual(
            reopened.check("fs_read", {"path": "C:/flight/A"}).action, "execute",
        )

    def test_clearing_records_keeps_the_versions(self):
        store, _ = _store()
        pre = FailurePreflight(store)
        pre.record("fs_read", {"path": "C:/flight/A"}, _failure("path_not_found"))
        pre.observe({"path": "C:/flight/A"})
        store.clear()
        self.assertEqual(store.versions.version_of("path:c:\\flight\\a"), "1")


class EnvironmentFactTests(unittest.TestCase):
    """A proven environment fact must move the resource it proved, not just ``*``."""

    def test_environment_check_modules_are_structured_resources(self):
        refs = resources_of({"modules": ["sklearn"]})
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].kind, "module")
        self.assertEqual(refs[0].key, "sklearn")

    def test_an_import_check_reopens_module_failures(self):
        """Acceptance: after a verified install, the blocked import runs."""
        pre = FailurePreflight(FailureStore(Path(tempfile.mkdtemp())))
        failed = _failure("unverified_module", stage="preconditions",
                          unverified_modules=["sklearn"])
        pre.record("code_run", {"language": "python", "code": "import sklearn"}, failed)
        self.assertEqual(pre.check("code_run", {"language": "python", "code": "import sklearn"}).action, "decline")
        pre.observe_success(
            "environment_check", {"modules": ["sklearn"]},
            {"data": {"importable": ["sklearn"]}},
        )
        self.assertEqual(
            pre.check("code_run", {"language": "python", "code": "import sklearn"}).action,
            "execute",
        )

    def test_a_verified_install_reopens_distribution_failures(self):
        pre = FailurePreflight(FailureStore(Path(tempfile.mkdtemp())))
        failed = _failure("dependency_missing", stage="preconditions",
                          missing_from_manifest=["gdal"])
        pre.record("dependency_install", {"packages": ["gdal"]}, failed)
        self.assertEqual(
            pre.check("dependency_install", {"packages": ["gdal"]}).action, "decline",
        )
        pre.observe_success(
            "dependency_install", {"packages": ["gdal"]},
            {"data": {"verification": {"succeeded": True, "installed_packages": ["gdal"]}}},
        )
        self.assertEqual(
            pre.check("dependency_install", {"packages": ["gdal"]}).action, "execute",
        )


class SignatureScopeTests(unittest.TestCase):
    """Authored content is not part of a strategy; the resource it touches is."""

    def test_a_line_number_change_does_not_bypass_the_block(self):
        """The audit's repro: same failure, script edited cosmetically, still blocked."""
        pre = FailurePreflight(FailureStore(Path(tempfile.mkdtemp())))
        failed = _failure("unverified_module", stage="preconditions",
                          unverified_modules=["sklearn"])
        first = {"language": "python", "code": "import sklearn\nmodel = sklearn.svm.SVC()"}
        pre.record("code_run", first, failed)
        self.assertEqual(pre.check("code_run", first).action, "decline")
        edited = {"language": "python",
                  "code": "\nimport sklearn  # attempt 2\nmodel = sklearn.svm.SVC()"}
        self.assertEqual(pre.check("code_run", edited).action, "decline")

    def test_an_unrelated_code_run_is_not_blocked_by_an_anchored_failure(self):
        """Dropping the code text must not let one failed script block every script."""
        pre = FailurePreflight(FailureStore(Path(tempfile.mkdtemp())))
        failed = _failure("unverified_module", stage="preconditions",
                          unverified_modules=["sklearn"])
        pre.record("code_run", {"language": "python", "code": "import sklearn"}, failed)
        other = {"language": "python", "code": "print('hello')"}
        self.assertEqual(pre.check("code_run", other).action, "execute")

    def test_a_generic_failure_only_blocks_the_identical_call(self):
        """A failure about nothing in particular anchors on byte-identity."""
        pre = FailurePreflight(FailureStore(Path(tempfile.mkdtemp())))
        failed = _failure("weird_new_thing", stage="execution")
        args = {"language": "python", "code": "x = 1 / 0"}
        pre.record("code_run", args, failed)
        # One free retry: the first failure is information, and model_decides means
        # the model, not the Runtime, concludes from it.
        self.assertEqual(pre.check("code_run", args).action, "execute")
        pre.record("code_run", args, failed)  # folds into the same record
        self.assertEqual(pre.check("code_run", args).action, "decline")
        other = {"language": "python", "code": "y = 2 + 2"}
        self.assertEqual(pre.check("code_run", other).action, "execute")


class BoundedRetryTests(unittest.TestCase):
    """``backoff`` and ``model_decides`` are bounded, not infinite passes."""

    def test_model_decides_gets_one_free_retry(self):
        pre = FailurePreflight(FailureStore(Path(tempfile.mkdtemp())))
        failed = _failure("weird_new_thing", stage="execution")
        pre.record("code_run", {"code": "x"}, failed)
        self.assertEqual(pre.check("code_run", {"code": "x"}).action, "execute")
        pre.record("code_run", {"code": "x"}, failed)
        decision = pre.check("code_run", {"code": "x"})
        self.assertEqual(decision.action, "decline")
        self.assertIn("2 times", decision.reason)

    def test_backoff_is_bounded(self):
        """A timeout may be retried twice; past that it is not transient any more."""
        pre = FailurePreflight(FailureStore(Path(tempfile.mkdtemp())))
        failed = _failure("timeout", stage="execution")
        pre.record("code_run", {"code": "x"}, failed)
        self.assertEqual(pre.check("code_run", {"code": "x"}).action, "execute")
        pre.record("code_run", {"code": "x"}, failed)
        self.assertEqual(pre.check("code_run", {"code": "x"}).action, "execute")
        pre.record("code_run", {"code": "x"}, failed)
        decision = pre.check("code_run", {"code": "x"})
        self.assertEqual(decision.action, "decline")
        self.assertIn("transient", decision.reason)


if __name__ == "__main__":
    unittest.main()
