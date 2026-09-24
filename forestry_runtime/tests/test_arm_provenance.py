"""Contract tests for arm provenance in the evidence.

An operator can start the container for the wrong arm and collect a whole session of
perfectly normal-looking slots that belong to the other treatment. Recording the
Runtime's switches into each slot's fingerprint is what turns that into a detected
mismatch instead of a silently contaminated result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.compare_arms import compare, load_arm  # noqa: E402
from evaluation import run_baseline  # noqa: E402
from evaluation.rules import SCORING_RULES_VERSION, collection_fingerprint  # noqa: E402
from evaluation.run_baseline import runtime_environment  # noqa: E402


class _Completed:
    """The subset of ``subprocess.CompletedProcess`` this path reads."""

    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def _trial(
    root: Path, name: str, record: dict, *, environment: dict | None,
) -> None:
    trial = root / name
    trial.mkdir(parents=True, exist_ok=True)
    (trial / "raw").mkdir(exist_ok=True)
    (trial / "record.json").write_text(json.dumps(record), encoding="utf-8")
    (trial / "trace.json").write_text(json.dumps({
        "run_id": f"{name}-run", "terminal_state": "completed", "steps": [],
    }), encoding="utf-8")
    (trial / "raw" / "events.json").write_text("[]", encoding="utf-8")
    payload: dict = {"fingerprint": "fp_test", "rules_version": SCORING_RULES_VERSION}
    if environment is not None:
        payload["runtime_environment"] = environment
    (trial / "collection.json").write_text(json.dumps(payload), encoding="utf-8")


def _record(case_id: str, condition: str, repeat: int, verdict: str) -> dict:
    return {
        "case_id": case_id, "condition": condition, "repeat": repeat,
        "status": "evaluated", "trial_id": f"{case_id}-{condition}-{repeat}",
        "checks": {"counts": {"verdict": verdict, "verifier": "t", "evidence": [],
                              "detail": verdict}},
    }


class ArmProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.arm_a = base / "arm-a"
        self.arm_b = base / "arm-b"
        self.arm_a.mkdir()
        self.arm_b.mkdir()
        for root in (self.arm_a, self.arm_b):
            (root / "configuration-agent.json").write_text(json.dumps({
                "model_digest": "same", "prompt_snapshot": "same",
                "tools_snapshot": "same", "sampling": {"temperature": 0.2},
                "budgets": {"model_requests": 32}, "environment_snapshot": "same",
            }), encoding="utf-8")

    def test_the_environment_switches_are_read_from_the_container(self):
        """The container is the only place the treatment is a fact.

        Reading the collecting shell instead recorded ``DOMAIN_GUIDES_ENABLED: ""``
        for slots the container had run as arm B, because the shell never set that
        variable -- and the contamination check then reported every slot as belonging
        to no arm while the evidence itself was correct.
        """
        container_env = (
            "PATH=/usr/bin\n"
            "REMOTE_SENSING_PLUGINS_ENABLED=false\n"
            "DOMAIN_GUIDES_ENABLED=false\n"
        )
        with patch.object(run_baseline.subprocess, "run",
                          return_value=_Completed(container_env)):
            with patch.dict(os.environ, {
                "REMOTE_SENSING_PLUGINS_ENABLED": "true",
                "DOMAIN_GUIDES_ENABLED": "true",
            }):
                self.assertEqual(runtime_environment(), {
                    "REMOTE_SENSING_PLUGINS_ENABLED": "false",
                    "DOMAIN_GUIDES_ENABLED": "false",
                })

    def test_an_unreachable_container_falls_back_to_the_process(self):
        with patch.object(run_baseline.subprocess, "run",
                          side_effect=OSError("docker is unavailable")):
            with patch.dict(os.environ, {
                "REMOTE_SENSING_PLUGINS_ENABLED": "true",
                "DOMAIN_GUIDES_ENABLED": "true",
            }):
                self.assertEqual(runtime_environment(), {
                    "REMOTE_SENSING_PLUGINS_ENABLED": "true",
                    "DOMAIN_GUIDES_ENABLED": "true",
                })

    def test_the_arm_is_recorded_but_isolated_from_the_reuse_fingerprint(self):
        """Switching arms must be *recorded*, not silently reused or invalidated.

        The switches are evidence, not identity: folding them into the fingerprint
        would make an operator's shell variables invalidate good evidence, while
        recording nothing would let a whole contaminated session look normal.
        """
        common = {
            "case_id": "capability.chm",
            "condition": "normal",
            "fixture": ROOT / "evaluation" / "fixtures" / "capability_chm",
            "configuration": {"model_digest": "same"},
        }
        plain = collection_fingerprint(**common)
        off = collection_fingerprint(**common, runtime_environment={
            "REMOTE_SENSING_PLUGINS_ENABLED": "false", "DOMAIN_GUIDES_ENABLED": "false",
        })
        on = collection_fingerprint(**common, runtime_environment={
            "REMOTE_SENSING_PLUGINS_ENABLED": "true", "DOMAIN_GUIDES_ENABLED": "true",
        })
        # The recorded value distinguishes the arms for the comparison...
        self.assertNotEqual(off, on)
        # ...while the fingerprint the runner actually uses is environment-free.
        self.assertEqual(collection_fingerprint(**common), plain)

    def test_a_root_collected_under_the_other_arm_is_flagged(self):
        _trial(self.arm_a, "capability-inventory-1",
               _record("capability.inventory", "normal", 1, "pass"),
               environment={"REMOTE_SENSING_PLUGINS_ENABLED": "true",
                            "DOMAIN_GUIDES_ENABLED": "true"})
        _trial(self.arm_b, "capability-inventory-1",
               _record("capability.inventory", "normal", 1, "pass"),
               environment={"REMOTE_SENSING_PLUGINS_ENABLED": "true",
                            "DOMAIN_GUIDES_ENABLED": "true"})
        report = compare(self.arm_a, self.arm_b)
        self.assertFalse(report["comparable"])
        self.assertTrue(report["contamination"])
        self.assertTrue(
            any("A 组" in item for item in report["contamination"]),
            report["contamination"],
        )

    def test_slots_without_a_recorded_environment_are_not_trusted(self):
        _trial(self.arm_a, "capability-inventory-1",
               _record("capability.inventory", "normal", 1, "pass"),
               environment=None)
        _trial(self.arm_b, "capability-inventory-1",
               _record("capability.inventory", "normal", 1, "pass"),
               environment={"REMOTE_SENSING_PLUGINS_ENABLED": "true",
                            "DOMAIN_GUIDES_ENABLED": "true"})
        report = compare(self.arm_a, self.arm_b)
        self.assertFalse(report["comparable"])
        self.assertTrue(
            any("未记录运行环境" in item for item in report["contamination"]),
            report["contamination"],
        )

    def test_a_correctly_separated_pair_is_comparable(self):
        _trial(self.arm_a, "capability-inventory-1",
               _record("capability.inventory", "normal", 1, "fail"),
               environment={"REMOTE_SENSING_PLUGINS_ENABLED": "false",
                            "DOMAIN_GUIDES_ENABLED": "false"})
        _trial(self.arm_b, "capability-inventory-1",
               _record("capability.inventory", "normal", 1, "pass"),
               environment={"REMOTE_SENSING_PLUGINS_ENABLED": "true",
                            "DOMAIN_GUIDES_ENABLED": "true"})
        report = compare(self.arm_a, self.arm_b)
        self.assertTrue(report["comparable"], report["contamination"])
        self.assertEqual(report["contamination"], [])
        self.assertEqual(report["totals"]["a"]["fail"], 1)
        self.assertEqual(report["totals"]["b"]["pass"], 1)
        self.assertIn("证据可对照：True", __import__(
            "evaluation.compare_arms", fromlist=["format_text"]
        ).format_text(report))

    def test_load_arm_carries_the_recorded_environment(self):
        _trial(self.arm_b, "capability-chm-1",
               _record("capability.chm", "gap", 1, "pass"),
               environment={"REMOTE_SENSING_PLUGINS_ENABLED": "true",
                            "DOMAIN_GUIDES_ENABLED": "true"})
        slots = load_arm(self.arm_b)
        slot = slots[("capability.chm", "gap", 1)]
        self.assertEqual(
            slot["runtime_environment"],
            {"REMOTE_SENSING_PLUGINS_ENABLED": "true", "DOMAIN_GUIDES_ENABLED": "true"},
        )


if __name__ == "__main__":
    unittest.main()
