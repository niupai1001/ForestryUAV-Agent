"""Controlled-experiment readiness: arm separation and experiment bookkeeping.

The comparison is only meaningful if the two arms differ in exactly one thing. These
tests pin that down: the domain layer can be switched off without changing the
Runtime, the tools, the budgets or the prompts, and a half-prepared experiment is
reported as not ready instead of being scored as a result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.ab_experiment import (  # noqa: E402
    ARMS, CAPABILITY_CASES, COMPOSE_FILES, REPEATS, RUNS, SCENARIOS,
    arm_in_effect, check, prepare, up, up_command,
)
from evaluation.rules import SCORING_RULES_VERSION  # noqa: E402
from runtime.capabilities.domain_guides.service import (  # noqa: E402
    DomainGuideCapability, guides_enabled,
)
from runtime.context import ContextCompiler, _guide_catalogue_part  # noqa: E402


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name, description="x", parameters_json_schema={"type": "object"}
    )


class _Toolbox:
    owner = "evaluator"
    chat_id = "chat"

    class _Workspaces:
        @staticmethod
        def list_grants(owner, chat_id):
            return []

    workspaces = _Workspaces()

    @staticmethod
    def attachment_context():
        return []


class ArmSeparationTests(unittest.TestCase):
    def test_the_domain_layer_is_switchable_without_touching_the_tools(self):
        """A arm keeps the same tool set; only the guide catalogue disappears."""
        with patch.dict(os.environ, {"DOMAIN_GUIDES_ENABLED": "true"}):
            enabled_text, enabled_entries = _guide_catalogue_part([_tool("domain_guide")])
        with patch.dict(os.environ, {"DOMAIN_GUIDES_ENABLED": "false"}):
            disabled_text, disabled_entries = _guide_catalogue_part([_tool("domain_guide")])
        self.assertTrue(enabled_entries)
        self.assertEqual(disabled_entries, [])
        self.assertEqual(disabled_text, "")
        self.assertIn("Domain guide catalogue", enabled_text)

    def test_tools_are_identical_between_arms(self):
        from runtime.agent import _tools

        with patch.dict(os.environ, {
            "REMOTE_SENSING_PLUGINS_ENABLED": "true",
            "DOMAIN_GUIDES_ENABLED": "true",
        }):
            names_b = {tool.name for tool in _tools(True, "计算 NDVI")[0]}
        with patch.dict(os.environ, {
            "REMOTE_SENSING_PLUGINS_ENABLED": "true",
            "DOMAIN_GUIDES_ENABLED": "false",
        }):
            names_a = {tool.name for tool in _tools(True, "计算 NDVI")[0]}
        self.assertEqual(names_a, names_b)
        self.assertIn("domain_guide", names_a)

    def test_the_domain_plugin_itself_is_what_the_a_arm_removes(self):
        from runtime.agent import _tools

        with patch.dict(os.environ, {
            "REMOTE_SENSING_PLUGINS_ENABLED": "false",
            "DOMAIN_GUIDES_ENABLED": "false",
        }):
            names = {tool.name for tool in _tools(True, "计算 NDVI")[0]}
        self.assertNotIn("calculate_ndvi", names)
        self.assertNotIn("inspect_uav_dataset", names)
        self.assertIn("code_run", names)
        self.assertIn("fs_read", names)

    def test_the_compiler_reports_an_empty_catalogue_when_disabled(self):
        compiler = ContextCompiler(_Toolbox())
        with patch.dict(os.environ, {"DOMAIN_GUIDES_ENABLED": "false"}):
            parts, manifest, _ = compiler.compile("问题", [_tool("domain_guide")], 1)
        self.assertEqual(manifest["domain_guide_count"], 0)
        self.assertFalse(any("Domain guide catalogue" in part for part in parts))
        with patch.dict(os.environ, {"DOMAIN_GUIDES_ENABLED": "true"}):
            _, manifest_on, _ = compiler.compile("问题", [_tool("domain_guide")], 1)
        self.assertGreater(manifest_on["domain_guide_count"], 0)

    def test_the_guide_tool_says_so_instead_of_failing(self):
        capability = DomainGuideCapability()
        with patch.dict(os.environ, {"DOMAIN_GUIDES_ENABLED": "false"}):
            self.assertFalse(guides_enabled())
            result = capability.domain_guide(guide_id="forest-method-boundaries")
        self.assertFalse(result["guides_available"])
        self.assertEqual(result["matches"], [])
        self.assertIn("No domain guide library", result["note"])


class ExperimentBookkeepingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_the_plan_is_twelve_scenarios_by_three_by_two(self):
        self.assertEqual(SCENARIOS, 12)
        self.assertEqual(REPEATS, 3)
        self.assertEqual(RUNS, 72)
        self.assertEqual(len(CAPABILITY_CASES), 6)
        self.assertEqual(set(ARMS), {"a", "b"})

    def test_prepare_creates_isolated_roots_with_their_environment(self):
        result = prepare(self.root)
        self.assertEqual(result["runs"], 72)
        for arm in ("a", "b"):
            directory = self.root / f"arm-{arm}"
            self.assertTrue(directory.is_dir())
            recorded = json.loads(
                (self.root / f"arm-{arm}-plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(recorded["arm"], arm)
            self.assertEqual(recorded["environment"], ARMS[arm]["env"])
            # The bookkeeping file must not sit inside the evidence root: a stray
            # file there reads as an incomplete trial and aborts collection.
            self.assertEqual(
                [item.name for item in directory.iterdir() if item.is_file()], []
            )
        a_env = json.loads(
            (self.root / "arm-a-plan.json").read_text(encoding="utf-8")
        )
        b_env = json.loads(
            (self.root / "arm-b-plan.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(a_env["environment"], b_env["environment"])
        self.assertEqual(
            set(a_env["environment"]) | set(b_env["environment"]),
            {"REMOTE_SENSING_PLUGINS_ENABLED", "DOMAIN_GUIDES_ENABLED"},
        )

    def test_prepare_is_idempotent_and_leaves_collection_room(self):
        prepare(self.root)
        prepare(self.root)
        for arm in ("a", "b"):
            directory = self.root / f"arm-{arm}"
            self.assertEqual(
                [item.name for item in directory.iterdir() if item.is_file()], []
            )

    def test_an_unfrozen_experiment_is_not_ready(self):
        prepare(self.root)
        report = check(self.root)
        self.assertFalse(report["ready"])
        self.assertTrue(any("freeze it before collecting" in item
                            for item in report["problems"]))
        self.assertEqual(report["runs_planned"], 72)

    def test_a_frozen_experiment_reports_ready_and_detects_drift(self):
        prepare(self.root)
        shared = {
            "code_snapshot": "abc", "model_digest": "sha256:deadbeef",
            "prompt_snapshot": "system+12@abc", "tools_snapshot": "0324ccd4b6b454af",
            "dataset_version": "forestry-eval-0.1",
            "environment_snapshot": "same-image", "evaluator_version": "forestry-eval-0.1",
            "sampling": {"temperature": 0.2, "context": 32768},
            "budgets": {"model_requests": 32, "wall_seconds": 900},
        }
        for arm in ("a", "b"):
            (self.root / f"arm-{arm}" / "configuration-agent.json").write_text(
                json.dumps(shared), encoding="utf-8"
            )
        report = check(self.root)
        self.assertTrue(report["ready"], report["problems"])

        drifted = dict(shared) | {"sampling": {"temperature": 0.9, "context": 32768}}
        (self.root / "arm-b" / "configuration-agent.json").write_text(
            json.dumps(drifted), encoding="utf-8"
        )
        report = check(self.root)
        self.assertFalse(report["ready"])
        self.assertTrue(any("differ in sampling" in item for item in report["problems"]))

    def test_ready_reports_how_many_slots_each_arm_needs(self):
        prepare(self.root)
        report = check(self.root)
        for arm in ("a", "b"):
            self.assertEqual(report["arms"][arm]["slots_expected"], 36)
            self.assertEqual(report["arms"][arm]["records"], 0)

    def test_the_rules_version_identifies_the_scoring_regime(self):
        """Both arms must be scored by the same rules or the comparison is void."""
        self.assertTrue(SCORING_RULES_VERSION)
        self.assertTrue(SCORING_RULES_VERSION.startswith("forestry-rules-"))
        major = SCORING_RULES_VERSION.rsplit("-", 1)[-1].split(".")[0]
        self.assertGreaterEqual(int(major), 2)


class ContainerArmTests(unittest.TestCase):
    """The arm's environment has to reach the *container*, not just the collector.

    Both switches are read by the Runtime. Setting them in the collecting shell
    would leave the container unchanged, both arms would run with identical
    settings, and the comparison would measure nothing.
    """

    def test_the_up_command_targets_both_compose_files(self):
        command = up_command("b")
        self.assertEqual(command[:2], ["docker", "compose"])
        for name in COMPOSE_FILES:
            self.assertIn(name, command)
        self.assertEqual(command[-3:], ["up", "-d", "runtime"])

    def test_each_arm_recreates_the_container_with_its_own_environment(self):
        for arm in ("a", "b"):
            with self.subTest(arm=arm):
                result = up(arm, root=Path("."))
                self.assertEqual(result["environment"], ARMS[arm]["env"])
                self.assertEqual(result["arm"], arm)

    def test_a_container_matching_neither_arm_is_reported(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=(
                "REMOTE_SENSING_PLUGINS_ENABLED=true\n"
                "DOMAIN_GUIDES_ENABLED=true\n"
                "OTHER=value\n"
            ),
            stderr="",
        )
        with patch("evaluation.ab_experiment.subprocess.run", return_value=fake):
            detected = arm_in_effect()
        self.assertEqual(detected["arm"], "b")
        self.assertEqual(
            detected["environment"],
            {"REMOTE_SENSING_PLUGINS_ENABLED": "true", "DOMAIN_GUIDES_ENABLED": "true"},
        )

        half = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="REMOTE_SENSING_PLUGINS_ENABLED=false\nDOMAIN_GUIDES_ENABLED=true\n",
            stderr="",
        )
        with patch("evaluation.ab_experiment.subprocess.run", return_value=half):
            detected = arm_in_effect()
        self.assertIsNone(detected["arm"])

    def test_an_unreachable_container_is_reported_not_assumed(self):
        missing = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="No such object: forestry-runtime",
        )
        with patch("evaluation.ab_experiment.subprocess.run", return_value=missing):
            detected = arm_in_effect()
        self.assertFalse(detected["running"])
        self.assertIsNone(detected["arm"])

    def test_check_reports_a_mismatched_container(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="REMOTE_SENSING_PLUGINS_ENABLED=false\nDOMAIN_GUIDES_ENABLED=true\n",
            stderr="",
        )
        with patch("evaluation.ab_experiment.subprocess.run", return_value=fake):
            report = check(root)
        self.assertFalse(report["ready"])
        self.assertTrue(any("matches neither arm" in item for item in report["problems"]))


if __name__ == "__main__":
    unittest.main()
