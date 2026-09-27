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
  the verifier blew up, and including when no verifier exists at all.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from runtime.agent import _artifact_path, _verification_payload
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
        report = _service("passed").evaluate(_facts())
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
        report = _service("passed").evaluate(_facts())
        self.assertEqual(report.state, STATE_VERIFIED)
        self.assertTrue(report.verified)

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

    def test_a_declared_product_can_reach_verified_complete(self) -> None:
        """End to end: a declared GeoTIFF can now actually be verified."""
        asset = dict(self._asset())
        asset["metadata"] = {"semantics": {
            "quantity": "canopy_candidate_mask", "kind": "mask",
            "valid_range": [0.0, 1.0], "fractions": {"canopy": 0.25},
        }}
        payload = _verification_payload(self._box([asset]), self._deps())
        self.assertEqual(payload["state"], STATE_VERIFIED, payload["reasons"])
        self.assertTrue(payload["verified"])

    def test_a_turn_that_delivered_nothing_reports_no_verification_state(self) -> None:
        self.assertIsNone(
            _verification_payload(self._box([]), self._deps()),
            "a turn that owes no deliverable is not a failed delivery",
        )

    def test_a_failed_tool_call_is_reported_as_failed(self) -> None:
        deps = self._deps([{"tool": "code_run", "ok": False, "code": "runtime_error"}])
        payload = _verification_payload(self._box([self._asset()]), deps)
        self.assertEqual(payload["state"], STATE_FAILED)

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
        payload = _service("passed").evaluate(_facts()).as_dict()
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

    def test_a_product_created_by_the_runtime_is_verified_not_just_delivered(self) -> None:
        created = self._created()
        payload = _verification_payload(
            self._box(created), SimpleNamespace(observation_log=[], pause_blocker=None),
        )
        self.assertIsNotNone(payload, "verification produced no payload at all")
        self.assertEqual(payload["state"], STATE_VERIFIED, payload["reasons"])

    def test_a_claim_the_file_contradicts_is_failed_not_verified(self) -> None:
        created = self._created()
        created[0]["metadata"]["semantics"]["fractions"]["canopy"] = 0.9
        payload = _verification_payload(
            self._box(created), SimpleNamespace(observation_log=[], pause_blocker=None),
        )
        self.assertEqual(payload["state"], STATE_FAILED, payload["reasons"])


if __name__ == "__main__":
    unittest.main()
