"""A run may not call itself complete on the strength of having produced a file.

The failure this test set exists to catch is the one that looks like success: a
product exists, is readable, carries correct metadata -- and nobody checked whether it
means what the task asked. Everything mechanical passes, so a two-valued verdict calls
it done, and the claim cannot be defended later.

Two rules are asserted directly, because they are the ones a caller has an incentive
to break:

* a semantic verdict that failed can never yield ``verified_complete``, even when the
  caller asks for it;
* structure with no semantic truth value is ``delivered_unverified``, including when
  the verifier blew up, and including when no verifier exists at all;
* a passing check against the producer's *own declaration* is still not
  ``verified_complete`` -- self-consistency is agreement between copies, not truth.
  Only facts carrying task truth (``task_truth=True``) may reach it, which for a
  real Run is never the case: the Runtime holds no gold reference.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from runtime.agent import (
    _artifact_path, _verification_feedback, _verification_payload,
)
from runtime.storage import Store
from runtime.verification import (
    Check, RunFacts, STATE_FAILED, STATE_UNVERIFIED, STATE_VERIFIED,
    VerificationService, VerifierRegistry, clamp, fold,
)


def _artifact(**overrides) -> dict:
    artifact = {
        "asset_id": "as_1",
        "name": "canopy_mask.tif",
        "artifact_kind": "raster",
        "media_type": "image/tiff",
        "checks": {
            "file_exists": True, "server_can_read": True,
            "producer_reported_success": True, "browser_can_display": False,
        },
    }
    artifact.update(overrides)
    return artifact


def _facts(**overrides) -> RunFacts:
    base = {"artifacts": [_artifact()], "plan_completion": "complete"}
    base.update(overrides)
    return RunFacts(**base)


def _service(verdict=None) -> VerificationService:
    registry = VerifierRegistry()
    if verdict is not None:
        registry.register(
            "mask_matches_canopy",
            lambda artifact, task: Check(
                layer="semantic", name="mask_matches_canopy",
                outcome=verdict, detail="sampled 120 points",
            ),
            kinds=("raster",),
        )
    return VerificationService(registry)


class FoldTest(unittest.TestCase):
    def test_any_failed_check_demotes_the_whole_result(self) -> None:
        checks = [
            Check(layer="execution", name="a", outcome="passed"),
            Check(layer="structural", name="b", outcome="failed", detail="missing file"),
            Check(layer="semantic", name="c", outcome="passed"),
        ]
        state, reasons = fold(checks)
        self.assertEqual(state, STATE_FAILED)
        self.assertTrue(any("missing file" in reason for reason in reasons))

    def test_an_unevaluated_check_keeps_the_result_unverified(self) -> None:
        checks = [
            Check(layer="execution", name="a", outcome="passed"),
            Check(layer="semantic", name="b", outcome="not_run"),
        ]
        self.assertEqual(fold(checks)[0], STATE_UNVERIFIED)

    def test_verified_is_the_strictest_outcome_not_the_default(self) -> None:
        checks = [Check(layer=layer, name=layer, outcome="passed") for layer in
                  ("execution", "structural", "semantic")]
        self.assertEqual(fold(checks)[0], STATE_VERIFIED)

    def test_no_checks_at_all_is_not_a_pass(self) -> None:
        self.assertEqual(fold([])[0], STATE_UNVERIFIED)


class ClampTest(unittest.TestCase):
    def test_a_failed_verdict_never_becomes_verified(self) -> None:
        report = _service("failed").evaluate(_facts())
        self.assertEqual(report.state, STATE_FAILED)
        self.assertEqual(clamp(STATE_VERIFIED, report), STATE_FAILED,
                         "a failed verdict must not be upgraded to complete")

    def test_an_unverified_report_cannot_be_claimed_as_complete(self) -> None:
        report = _service().evaluate(_facts())
        self.assertEqual(report.state, STATE_UNVERIFIED)
        self.assertEqual(clamp(STATE_VERIFIED, report), STATE_UNVERIFIED)

    def test_a_verified_report_may_be_claimed_as_verified(self) -> None:
        report = _service("passed").evaluate(_facts(task_truth=True))
        self.assertEqual(report.state, STATE_VERIFIED)
        self.assertEqual(clamp(STATE_VERIFIED, report), STATE_VERIFIED)

    def test_a_failed_report_cannot_be_softened_to_unverified(self) -> None:
        report = _service().evaluate(_facts(tool_failures=["code_run"]))
        self.assertEqual(report.state, STATE_FAILED)
        self.assertEqual(clamp(STATE_UNVERIFIED, report), STATE_FAILED)


class StateTest(unittest.TestCase):
    def test_a_delivered_artifact_with_no_verifier_is_unverified(self) -> None:
        report = _service().evaluate(_facts())
        self.assertEqual(report.state, STATE_UNVERIFIED)
        self.assertTrue(any("no verifier" in reason for reason in report.reasons))

    def test_a_passing_verifier_reaches_verified_complete(self) -> None:
        report = _service("passed").evaluate(_facts(task_truth=True))
        self.assertEqual(report.state, STATE_VERIFIED)
        self.assertTrue(report.verified)

    def test_a_passing_verifier_without_task_truth_stays_unverified(self) -> None:
        """The verifier's verdict is real, but what it checked was agreement with
        the producer's declaration. Without task truth the honest state is the
        middle one, whatever the score."""
        report = _service("passed").evaluate(_facts())
        self.assertEqual(report.state, STATE_UNVERIFIED)
        self.assertTrue(any(
            "self-consistency" in reason or "task truth" in reason
            for reason in report.reasons), report.reasons)

    def test_a_failed_tool_call_makes_the_run_failed(self) -> None:
        report = _service("passed").evaluate(_facts(tool_failures=["code_run"]))
        self.assertEqual(report.state, STATE_FAILED)

    def test_a_blocked_call_makes_the_run_failed(self) -> None:
        report = _service("passed").evaluate(
            _facts(blocked_calls=["dependency_install: no new evidence"]),
        )
        self.assertEqual(report.state, STATE_FAILED)

    def test_stopping_early_makes_the_run_failed(self) -> None:
        report = _service("passed").evaluate(_facts(stop_reason="premature_stop"))
        self.assertEqual(report.state, STATE_FAILED)

    def test_open_requirements_keep_the_run_from_being_complete(self) -> None:
        report = _service("passed").evaluate(_facts(open_requirements=["req_1"]))
        self.assertEqual(report.state, STATE_FAILED)

    def test_a_blocked_plan_is_reported_as_failed(self) -> None:
        report = _service("passed").evaluate(_facts(plan_completion="blocked"))
        self.assertEqual(report.state, STATE_FAILED)

    def test_a_missing_file_is_a_structural_failure(self) -> None:
        artifact = _artifact()
        artifact["checks"]["file_exists"] = False
        report = _service("passed").evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)
        self.assertTrue(
            any(check.name == "artifact_files_exist" for check in report.failed_checks())
        )

    def test_a_product_with_no_crs_is_a_structural_failure(self) -> None:
        artifact = _artifact(product_qa={"band_count": 1, "crs": None,
                                         "valid_fraction_sampled": 0.8})
        report = _service("passed").evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)

    def test_an_empty_product_is_a_structural_failure(self) -> None:
        artifact = _artifact(product_qa={"band_count": 1, "crs": "EPSG:32650",
                                         "valid_fraction_sampled": 0.0})
        report = _service("passed").evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_FAILED)

    def test_measured_metadata_alone_does_not_verify_meaning(self) -> None:
        """A raster with a CRS and valid pixels is well-formed, not correct."""
        artifact = _artifact(product_qa={"band_count": 1, "crs": "EPSG:32650",
                                         "valid_fraction_sampled": 0.9})
        report = _service().evaluate(RunFacts(artifacts=[artifact]))
        self.assertEqual(report.state, STATE_UNVERIFIED)

    def test_no_artifact_at_all_is_a_failure(self) -> None:
        report = _service("passed").evaluate(RunFacts(artifacts=[]))
        self.assertEqual(report.state, STATE_FAILED)


class VerifierRobustnessTest(unittest.TestCase):
    def test_a_verifier_that_raises_does_not_pass(self) -> None:
        registry = VerifierRegistry()

        def explodes(artifact, task):
            raise RuntimeError("rasterio missing")

        registry.register("exploding", explodes, kinds=("raster",))
        report = VerificationService(registry).evaluate(_facts())
        self.assertEqual(report.state, STATE_UNVERIFIED,
                         "a verifier that blew up must not read as agreement")
        self.assertTrue(any("raised" in reason for reason in report.reasons))

    def test_semantic_verdicts_are_skipped_when_the_run_is_already_broken(self) -> None:
        calls = []

        def counting(artifact, task):
            calls.append(artifact)
            return Check(layer="semantic", name="counting", outcome="passed")

        registry = VerifierRegistry()
        registry.register("counting", counting, kinds=("raster",))
        service = VerificationService(registry)
        service.evaluate(_facts(tool_failures=["code_run"]))
        self.assertEqual(calls, [], "no verdict should be asked of a broken run")

    def test_a_verifier_only_speaks_for_the_kinds_it_registered(self) -> None:
        registry = VerifierRegistry()
        registry.register("text_only",
                          lambda artifact, task: Check(layer="semantic", name="t",
                                                       outcome="passed"),
                          kinds=("report",))
        report = VerificationService(registry).evaluate(_facts())
        self.assertEqual(report.state, STATE_UNVERIFIED)


class RunReportingTest(unittest.TestCase):
    """The wiring: what a finished Run actually reports about itself."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.product = Path(self.temp.name) / "canopy_mask.tif"
        self._write_geotiff(self.product)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_geotiff(path: Path) -> None:
        """A real GeoTIFF: the verifier reads the product, so bytes that only look
        like one would be reported as unreadable rather than as delivered."""
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        with rasterio.open(
            path, "w", driver="GTiff", width=8, height=8, count=1, dtype="float32",
            crs="EPSG:32650", transform=from_origin(0.0, 0.0, 1.0, 1.0),
        ) as target:
            target.write(np.full((8, 8), 0.25, dtype="float32"), 1)

    def _box(self, created, plan=None):
        class _Box:
            chat_id = "chat"

            def __init__(self):
                self.created = created

            def _plan_store(self):
                if plan is None:
                    raise AttributeError("no plan recorded")
                return type("_Store", (), {"load": staticmethod(lambda: plan)})()

        return _Box()

    @staticmethod
    def _deps(log=None, blocker=None):
        return SimpleNamespace(observation_log=log or [], pause_blocker=blocker)

    def _asset(self) -> dict:
        return {"id": "as_1", "name": "canopy_mask.tif", "path": str(self.product)}

    def test_a_delivered_product_is_reported_unverified_not_complete(self) -> None:
        payload = _verification_payload(self._box([self._asset()]), self._deps())
        self.assertIsNotNone(payload)
        self.assertEqual(payload["state"], STATE_UNVERIFIED)
        self.assertFalse(payload["verified"])

    def test_a_product_that_cannot_be_read_is_failed_not_delivered(self) -> None:
        broken = Path(self.temp.name) / "broken.tif"
        broken.write_bytes(b"II*\x00")
        payload = _verification_payload(
            self._box([{"id": "as_2", "name": "broken.tif", "path": str(broken)}]),
            self._deps(),
        )
        self.assertEqual(payload["state"], STATE_FAILED)

    def test_a_declared_product_without_task_truth_is_not_reported_complete(self) -> None:
        """End to end: a declared GeoTIFF that matches its own declaration is
        self-consistent, and the Runtime still reports ``delivered_unverified``
        because nothing compared it against the task. Reaching
        ``verified_complete`` requires truth the Runtime does not have."""
        asset = dict(self._asset())
        asset["metadata"] = {"semantics": {
            "quantity": "canopy_candidate_mask", "kind": "mask",
            "valid_range": [0.0, 1.0], "fractions": {"canopy": 0.25},
        }}
        payload = _verification_payload(self._box([asset]), self._deps())
        self.assertEqual(payload["state"], STATE_UNVERIFIED, payload["reasons"])
        self.assertFalse(payload["verified"])
        self.assertTrue(any(
            "task truth" in reason or "self-consistency" in reason
            for reason in payload["reasons"]), payload["reasons"])

    def test_a_turn_that_delivered_nothing_reports_no_verification_state(self) -> None:
        self.assertIsNone(
            _verification_payload(self._box([]), self._deps()),
            "a turn that owes no deliverable is not a failed delivery",
        )

    def test_a_failed_tool_call_is_reported_as_failed(self) -> None:
        deps = self._deps([{"tool": "code_run", "ok": False, "code": "runtime_error"}])
        payload = _verification_payload(self._box([self._asset()]), deps)
        self.assertEqual(payload["state"], STATE_FAILED)

    def test_a_failure_recovered_by_a_later_success_no_longer_fails(self) -> None:
        """The execution layer is about how the run *ended*, not every stumble:
        the same tool succeeding after a failure is the strongest fact available
        that the failure stopped mattering, so the ledger's history must not hold
        the final state hostage to it."""
        deps = self._deps([
            {"tool": "code_run", "ok": False, "code": "runtime_error"},
            {"tool": "code_run", "ok": True, "summary": "path=canopy_mask.tif"},
        ])
        payload = _verification_payload(self._box([self._asset()]), deps)
        self.assertEqual(payload["state"], STATE_UNVERIFIED, payload["reasons"])

    def test_a_declined_repeat_call_is_reported_as_failed(self) -> None:
        deps = self._deps([{"tool": "fs_read", "ok": False,
                            "code": "duplicate_failed_call"}])
        payload = _verification_payload(self._box([self._asset()]), deps)
        self.assertEqual(payload["state"], STATE_FAILED)

    def test_an_open_requirement_blocks_the_completion_claim(self) -> None:
        plan = type("_Plan", (), {
            "open_requirements": staticmethod(lambda: [{"id": "req_1"}]),
            "blocked_requirements": staticmethod(lambda: []),
            "completion_state": staticmethod(lambda: "open"),
        })()
        payload = _verification_payload(self._box([self._asset()], plan=plan), self._deps())
        self.assertEqual(payload["state"], STATE_FAILED)


class ReportShapeTest(unittest.TestCase):
    def test_the_report_names_what_to_do_next(self) -> None:
        report = _service().evaluate(_facts())
        self.assertTrue(report.next_actions)
        self.assertTrue(any("verifier" in action for action in report.next_actions))

    def test_the_report_is_serialisable_by_layer(self) -> None:
        payload = _service("passed").evaluate(_facts(task_truth=True)).as_dict()
        self.assertEqual(set(payload["layers"]), {"execution", "structural", "semantic"})
        self.assertTrue(payload["verified"])

    def test_the_rendered_state_is_named_not_softened(self) -> None:
        rendered = _service().evaluate(_facts()).render()
        self.assertIn(STATE_UNVERIFIED, rendered)
        self.assertNotIn("success", rendered.lower())


class RealProductWiringTest(unittest.TestCase):
    """A product a Run actually created, verified without anyone handing it a path.

    The bug this exists to catch was invisible for as long as tests built artifacts by
    hand: they put ``"path"`` on the artifact dict, which is the one field the store
    never sets. An asset stored by content has no ``path`` and no usable
    ``managed_path``, so reading either left the verifier with no file -- no file means
    no measurement, so every real delivery was reported ``delivered_unverified`` while
    every unit test reported ``verified_complete``.

    So this test builds the artifact the way the runtime does -- through a ``Store`` --
    and asks the real payload function what the run may claim.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "assets")
        self.product = self.root / "canopy_mask.tif"
        self._write_geotiff(self.product)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_geotiff(path: Path) -> None:
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        with rasterio.open(
            path, "w", driver="GTiff", width=8, height=8, count=1, dtype="float32",
            crs="EPSG:32650", transform=from_origin(0.0, 0.0, 1.0, 1.0),
        ) as target:
            target.write(np.full((8, 8), 0.25, dtype="float32"), 1)

    def _created(self) -> list[dict]:
        """An asset as the store returns it: no ``path`` key anywhere."""
        with self.product.open("rb") as stream:
            return [self.store.put(
                stream, "canopy_mask.tif", "alice", "image/tiff",
                metadata={
                    "semantics": {
                        "quantity": "canopy_candidate_mask", "kind": "mask",
                        "valid_range": [0.0, 1.0], "fractions": {"canopy": 0.25},
                        "grid": {"crs": "EPSG:32650", "width": 8, "height": 8,
                                 "pixel_size": [1.0, 1.0]},
                    },
                },
            )]

    def _box(self, created):
        store = self.store

        class _Box:
            chat_id = "chat"
            owner = "alice"

            def __init__(self):
                self.created = created
                self.store = store

            def _plan_store(self):
                raise AttributeError("no plan recorded")

        return _Box()

    def test_the_store_is_the_only_place_the_path_comes_from(self) -> None:
        asset = self._created()[0]
        self.assertIsNone(asset.get("path"))
        self.assertFalse(asset.get("managed_path"))
        resolved = _artifact_path(self._box([]), asset)
        self.assertTrue(resolved and Path(resolved).is_file(), resolved)

    def test_a_product_created_by_the_runtime_is_self_consistent_still_unverified(self) -> None:
        """The store resolves the real path, the declaration is held to, the file
        matches it -- and the state is still the middle one, because the Runtime
        holds no task truth. That is the honest ceiling for a real Run."""
        created = self._created()
        payload = _verification_payload(
            self._box(created), SimpleNamespace(observation_log=[], pause_blocker=None),
        )
        self.assertIsNotNone(payload, "verification produced no payload at all")
        self.assertEqual(payload["state"], STATE_UNVERIFIED, payload["reasons"])
        self.assertTrue(any(
            "self-consistency" in reason or "task truth" in reason
            for reason in payload["reasons"]), payload["reasons"])

    def test_a_claim_the_file_contradicts_is_failed_not_verified(self) -> None:
        created = self._created()
        created[0]["metadata"]["semantics"]["fractions"]["canopy"] = 0.9
        payload = _verification_payload(
            self._box(created), SimpleNamespace(observation_log=[], pause_blocker=None),
        )
        self.assertEqual(payload["state"], STATE_FAILED, payload["reasons"])


class FeedbackLoopTest(unittest.TestCase):
    """The repair half of verification: failures reach the model while it can act.

    A verdict delivered only in the terminal event is an honest report nobody can
    use -- the Run is over, there is no next request to fix anything in. These
    tests pin the per-request hook: a fixable failure is handed back with its
    actions, once per distinct failure set, and a failure the Run already
    recovered from is not reported at all.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.product = Path(self.temp.name) / "result.txt"
        self.product.write_text("ok", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _box(self):
        return SimpleNamespace(
            chat_id="chat",
            owner="alice",
            created=[{"id": "as_9", "name": "result.txt", "path": str(self.product)}],
            workspace=Path(self.temp.name),
            attachment_context=lambda: [],
            workspaces=SimpleNamespace(list_grants=lambda owner, chat_id: []),
        )

    def _deps(self, log):
        return SimpleNamespace(
            observation_log=log, pause_blocker=None, verification_reported="",
        )

    def test_an_unrecovered_failure_is_handed_back_with_actions(self) -> None:
        deps = self._deps([{"tool": "code_run", "ok": False, "code": "runtime_error"}])
        feedback = _verification_feedback(self._box(), deps)
        payload = feedback()
        self.assertIsNotNone(payload)
        self.assertIn("no_failed_tool_calls", payload["fixing"])
        self.assertTrue(payload["next_actions"], "a fix without a next action is a dead end")

    def test_the_same_failure_set_is_injected_once(self) -> None:
        deps = self._deps([{"tool": "code_run", "ok": False, "code": "runtime_error"}])
        feedback = _verification_feedback(self._box(), deps)
        self.assertIsNotNone(feedback())
        self.assertIsNone(feedback(),
                          "an unchanged report repeated every request is noise")

    def test_a_recovered_failure_is_not_reported(self) -> None:
        deps = self._deps([
            {"tool": "code_run", "ok": False, "code": "runtime_error"},
            {"tool": "code_run", "ok": True, "summary": "path=result.txt"},
        ])
        feedback = _verification_feedback(self._box(), deps)
        self.assertIsNone(feedback())

    def test_no_artifact_and_no_failure_is_silence(self) -> None:
        box = self._box()
        box.created = []
        feedback = _verification_feedback(box, self._deps([]))
        self.assertIsNone(feedback(),
                          "a run with nothing to fix owes the model no feedback")

    def test_compile_carries_the_feedback_then_deduplicates(self) -> None:
        from runtime.context import ContextCompiler

        deps = self._deps([{"tool": "code_run", "ok": False, "code": "runtime_error"}])
        box = self._box()
        compiler = ContextCompiler(box, verification=_verification_feedback(box, deps))

        def facts_of(parts):
            return next(
                json.loads(part.split("\n", 1)[1]) for part in parts
                if part.startswith("Runtime facts")
            )

        parts, manifest, _ = compiler.compile("q", [], 1)
        first = facts_of(parts)
        self.assertIn("verification", first)
        self.assertIn("no_failed_tool_calls", first["verification"]["fixing"])
        self.assertTrue(manifest["verification_feedback"])
        parts, manifest, _ = compiler.compile("q", [], 1)
        self.assertNotIn("verification", facts_of(parts))
        self.assertFalse(manifest["verification_feedback"])


if __name__ == "__main__":
    unittest.main()
