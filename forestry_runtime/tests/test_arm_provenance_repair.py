"""Arm provenance must come from the container, not from the collecting shell.

`compare_arms` refuses to compare evidence roots whose slots disagree with the arm
they were collected under, which is how a real contamination was caught. That check
is only as good as the value recorded at collection time: reading the switches from
the collecting process's environment recorded ``DOMAIN_GUIDES_ENABLED: ""`` for a
slot the container had demonstrably run as arm B, because the shell never set that
variable. The container is the only place where the treatment is a fact.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation import run_baseline  # noqa: E402
from evaluation.record_arm_provenance import repair  # noqa: E402

ARM_B = {"REMOTE_SENSING_PLUGINS_ENABLED": "true", "DOMAIN_GUIDES_ENABLED": "true"}


class _Completed:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def _slot(root: Path, name: str, environment) -> Path:
    slot = root / name
    (slot / "raw").mkdir(parents=True)
    (slot / "collection.json").write_text(
        json.dumps({"case_id": "capability.chm", "runtime_environment": environment},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return slot


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_the_container_is_the_source_of_truth(self):
        with patch.object(run_baseline.subprocess, "run", return_value=_Completed(
            "PATH=/usr/bin\nREMOTE_SENSING_PLUGINS_ENABLED=true\nDOMAIN_GUIDES_ENABLED=true\n"
        )):
            with patch.dict("os.environ", {"DOMAIN_GUIDES_ENABLED": ""}):
                self.assertEqual(run_baseline.runtime_environment(), ARM_B)

    def test_an_unreachable_container_falls_back_to_the_environment(self):
        with patch.object(run_baseline.subprocess, "run",
                          side_effect=OSError("docker is not installed")):
            with patch.dict("os.environ", ARM_B):
                self.assertEqual(run_baseline.runtime_environment(), ARM_B)


class ProvenanceRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _repair(self, *, container=ARM_B, apply=True):
        with patch("evaluation.record_arm_provenance.runtime_environment",
                   return_value=container):
            return repair(self.root, container="forestry-runtime", apply=apply)

    def test_an_unset_switch_is_filled_from_the_container(self):
        slot = _slot(self.root, "capability-chm-1",
                     {"REMOTE_SENSING_PLUGINS_ENABLED": "true", "DOMAIN_GUIDES_ENABLED": ""})
        report = self._repair()
        self.assertEqual(report["repaired"], ["capability-chm-1"])
        payload = json.loads((slot / "collection.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["runtime_environment"], ARM_B)

    def test_a_dry_run_changes_nothing(self):
        slot = _slot(self.root, "capability-chm-1",
                     {"REMOTE_SENSING_PLUGINS_ENABLED": "true", "DOMAIN_GUIDES_ENABLED": ""})
        report = self._repair(apply=False)
        self.assertEqual(report["repaired"], ["capability-chm-1"])
        payload = json.loads((slot / "collection.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["runtime_environment"]["DOMAIN_GUIDES_ENABLED"], "")

    def test_a_contradiction_is_reported_and_left_alone(self):
        """A slot that disagrees with the container is the finding, not the bug."""
        slot = _slot(self.root, "capability-chm-1",
                     {"REMOTE_SENSING_PLUGINS_ENABLED": "false", "DOMAIN_GUIDES_ENABLED": "false"})
        report = self._repair()
        self.assertEqual(report["repaired"], [])
        self.assertEqual(len(report["skipped"]), 1)
        self.assertIn("contradicts", report["skipped"][0]["reason"])
        payload = json.loads((slot / "collection.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["runtime_environment"]["REMOTE_SENSING_PLUGINS_ENABLED"], "false")

    def test_an_already_correct_slot_is_untouched(self):
        _slot(self.root, "capability-chm-2", dict(ARM_B))
        report = self._repair()
        self.assertEqual(report["repaired"], [])
        self.assertEqual(report["already_correct"], 1)

    def test_an_unreadable_container_refuses_to_guess(self):
        _slot(self.root, "capability-chm-1",
              {"REMOTE_SENSING_PLUGINS_ENABLED": "true", "DOMAIN_GUIDES_ENABLED": ""})
        report = self._repair(container={"REMOTE_SENSING_PLUGINS_ENABLED": "true",
                                         "DOMAIN_GUIDES_ENABLED": ""})
        self.assertIn("refused", report)
        self.assertEqual(report["repaired"], [])


if __name__ == "__main__":
    unittest.main()
