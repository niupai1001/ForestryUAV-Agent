"""Phase 2 acceptance: project instructions, prompt layering, domain guides.

The behaviours under test:

* the instruction is editable, versioned, budget-bounded and never truncated;
* concurrent edits are rejected instead of silently overwriting;
* one Turn pins one revision, so a mid-Turn edit cannot change instructions that
  a running tool chain is already using;
* domain guides are discovered from a catalogue without a handful of hard-coded
  keywords, and their full text never enters the context unbidden;
* a plain greeting is not steered into file or code work by the global rules.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from runtime.agent import SYSTEM
from runtime.context import ContextCompiler, _guide_catalogue_part
from runtime.domain_guides import (
    GuideError,
    find_guide,
    guide_catalogue,
    load_guides,
    match_guides,
)
from runtime.memory import MemoryManager
from runtime.storage import AssetError

GUIDE_ROOT = Path(__file__).resolve().parent.parent / "knowledge" / "guides"


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name, description="x", parameters_json_schema={"type": "object"}
    )


class _Toolbox:
    """Minimal stand-in for RuntimeTools used by ContextCompiler."""

    def __init__(self):
        self.owner = "alice"
        self.chat_id = "chat"

        class _Workspaces:
            @staticmethod
            def list_grants(owner, chat_id):
                return []

        self.workspaces = _Workspaces()

    @staticmethod
    def attachment_context():
        return []


class ProjectInstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory = MemoryManager(self.temp.name)
        self.project = self.memory.create_project("alice", "演示项目")

    def tearDown(self):
        self.temp.cleanup()

    def test_default_instruction_exists_and_is_a_scaffold(self):
        current = self.memory.project_instruction("alice", self.project["id"])
        self.assertEqual(current["revision"], 1)
        self.assertTrue(current["is_default"])
        self.assertTrue(current["within_budget"])
        self.assertIn("项目目标", current["content"])
        # It must not read as a permission grant.
        self.assertIn("不授予任何权限", current["content"])

    def test_edit_creates_a_new_revision_and_keeps_history(self):
        updated = self.memory.set_project_instruction(
            "alice", self.project["id"], "目标：生成 CHM 并交付 GeoTIFF",
            expected_revision=1,
        )
        self.assertEqual(updated["revision"], 2)
        self.assertFalse(updated["is_default"])
        history = self.memory.instruction_history("alice", self.project["id"])
        self.assertEqual([item["revision"] for item in history], [2, 1])
        first = self.memory.instruction_version("alice", self.project["id"], 1)
        self.assertIn("项目目标", first["content"])

    def test_stale_revision_is_rejected(self):
        self.memory.set_project_instruction(
            "alice", self.project["id"], "第一版", expected_revision=1
        )
        with self.assertRaises(AssetError) as caught:
            self.memory.set_project_instruction(
                "alice", self.project["id"], "第二版", expected_revision=1
            )
        self.assertIn("modified by someone else", str(caught.exception))

    def test_over_budget_text_is_rejected_not_truncated(self):
        with patch.dict(os.environ, {"PROJECT_INSTRUCTION_BUDGET_TOKENS": "300"}):
            with self.assertRaises(AssetError) as caught:
                self.memory.set_project_instruction(
                    "alice", self.project["id"], "林地遥感" * 2000
                )
        message = str(caught.exception)
        self.assertIn("budget", message)
        self.assertIn("does not truncate", message)
        # The stored text is untouched.
        current = self.memory.project_instruction("alice", self.project["id"])
        self.assertTrue(current["is_default"])

    def test_reset_restores_default_as_a_new_revision(self):
        self.memory.set_project_instruction(
            "alice", self.project["id"], "自定义", expected_revision=1
        )
        restored = self.memory.reset_project_instruction("alice", self.project["id"])
        self.assertTrue(restored["is_default"])
        self.assertEqual(restored["revision"], 3)
        history = self.memory.instruction_history("alice", self.project["id"])
        self.assertEqual(len(history), 3)

    def test_snapshot_pins_one_revision_for_a_turn(self):
        self.memory.select_project("alice", "chat-1", self.project["id"])
        self.memory.set_project_instruction(
            "alice", self.project["id"], "第一版目标", expected_revision=1
        )
        pinned = self.memory.instruction_snapshot("alice", "chat-1")
        self.assertEqual(pinned["revision"], 2)

        # An edit mid-Turn must not change the pinned snapshot.
        self.memory.set_project_instruction(
            "alice", self.project["id"], "第二版目标", expected_revision=2
        )
        replay = self.memory.instruction_snapshot("alice", "chat-1", revision=2)
        self.assertEqual(replay["revision"], 2)
        self.assertEqual(replay["content"], "第一版目标")
        self.assertTrue(replay["pinned"])

    def test_unbound_chat_has_no_instruction_snapshot(self):
        self.assertEqual(self.memory.instruction_snapshot("alice", "chat-none"), {})

    def test_instructions_do_not_leak_between_projects(self):
        other = self.memory.create_project("alice", "另一个项目")
        self.memory.set_project_instruction(
            "alice", self.project["id"], "项目A的约定", expected_revision=1
        )
        self.assertTrue(
            self.memory.project_instruction("alice", other["id"])["is_default"]
        )


class PromptLayeringTests(unittest.TestCase):
    def setUp(self):
        self.toolbox = _Toolbox()

    def _compile(self, project: dict, tools: list) -> tuple[list[str], dict]:
        compiler = ContextCompiler(
            self.toolbox, project_context=lambda: project
        )
        parts, manifest, _ = compiler.compile(
            "问题", tools, len([ModelRequest(parts=[UserPromptPart(content="问题")])])
        )
        return parts, manifest

    def test_layers_are_ordered_stable_to_volatile(self):
        project = {
            "project_id": "project_x", "content": "目标：生成 CHM",
            "instruction_revision": 4, "instruction_tokens": 12,
            "instruction_budget_tokens": 2000,
        }
        parts, manifest = self._compile(project, [_tool("domain_guide")])
        self.assertGreaterEqual(len(parts), 3)
        self.assertIn("Project instructions", parts[0])
        self.assertIn("revision 4", parts[0])
        self.assertIn("Domain guide catalogue", parts[1])
        self.assertIn("Runtime facts", parts[-1])
        self.assertEqual(manifest["instruction_revision"], 4)
        self.assertGreater(manifest["domain_guide_count"], 0)

    def test_guide_catalogue_carries_ids_and_summaries_but_not_full_text(self):
        project = {"project_id": "p", "content": "x", "instruction_revision": 1}
        parts, _ = self._compile(project, [_tool("domain_guide")])
        catalogue = parts[1]
        for entry in guide_catalogue():
            self.assertIn(entry["id"], catalogue)
        # A distinctive sentence from a guide body must not appear.
        body_marker = find_guide("forest-method-boundaries").body.splitlines()[-1][:20]
        self.assertNotIn(body_marker, catalogue)

    def test_catalogue_is_omitted_when_the_guide_tool_is_unavailable(self):
        parts, manifest = self._compile(
            {"project_id": "p", "content": "x"}, [_tool("fs_read")]
        )
        self.assertFalse(any("Domain guide catalogue" in part for part in parts))
        self.assertEqual(manifest["domain_guide_count"], 0)

    def test_project_instruction_is_marked_as_constraint_not_permission(self):
        parts, _ = self._compile(
            {"project_id": "p", "content": "读所有目录", "instruction_revision": 2},
            [_tool("fs_read")],
        )
        self.assertIn("not permissions", parts[0])
        self.assertIn("cannot widen tool scope", parts[0])


class DomainGuideTests(unittest.TestCase):
    def test_rgb_canopy_route_is_discoverable_without_nir_or_height(self):
        query = "仅有 RGB 正射影像，提取树冠分布并计算覆盖率"
        matches = match_guides(query, limit=3)
        self.assertEqual(matches[0].id, "rgb-canopy-cover")
        content = find_guide("rgb-canopy-cover").body
        self.assertIn("NIR、DSM、DTM 不是这一路线的必要输入", content)
        self.assertIn("研究区有效面积", content)

    def test_catalogue_does_not_treat_missing_guide_as_impossibility(self):
        text, _ = _guide_catalogue_part([_tool("domain_guide")])
        self.assertIn("not an exhaustive list", text)
        self.assertIn("does not rule out another method", text)

    def test_every_guide_parses_with_required_metadata(self):
        guides = load_guides([GUIDE_ROOT])
        self.assertGreaterEqual(len(guides), 8)
        for guide in guides:
            with self.subTest(guide=guide.id):
                self.assertTrue(guide.title)
                self.assertTrue(guide.summary)
                self.assertTrue(guide.tags)
                self.assertTrue(guide.applies_to)
                self.assertTrue(guide.body)
                self.assertEqual(guide.citation, f"guide://{guide.id}@{guide.version}")

    def test_discovery_is_not_tied_to_a_fixed_keyword_list(self):
        """Unanticipated phrasings must still find the right guide."""
        cases = {
            "怎么判断像元大小是度还是米": "geospatial-product-qa",
            "树冠分水岭结果能不能当成株数": "forest-method-boundaries",
            "训练集和测试集怎么划分才不算泄漏": "supervised-classification-and-validation",
            "输入文件换了以后要不要重跑": "recomputation-and-input-lineage",
            "pip 装完了但 import 失败": "runtime-environment-and-dependencies",
            "NDVI 出现无穷大怎么办": "ndvi-and-vegetation-indices",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                ranked = match_guides(query, limit=3)
                self.assertTrue(ranked, f"no guide matched: {query}")
                self.assertIn(
                    expected, [guide.id for guide in ranked],
                    f"{expected} not ranked for: {query}",
                )

    def test_find_guide_reports_an_actionable_error(self):
        with self.assertRaises(GuideError) as caught:
            find_guide("not-a-guide")
        self.assertIn("catalogue", str(caught.exception))

    def test_catalogue_entries_are_small_enough_to_always_include(self):
        from runtime.tokens import estimate_text

        text, entries = _guide_catalogue_part([_tool("domain_guide")])
        self.assertEqual(len(entries), len(guide_catalogue()))
        # The catalogue must stay cheap; the detail lives behind the tool.
        self.assertLess(estimate_text(text), 1200)


class GlobalRulesTests(unittest.TestCase):
    def test_greeting_is_not_steered_into_file_or_code_work(self):
        """A plain greeting must not read as a pitch for file/code capability."""
        for phrase in ("面向文件、代码", "文件、代码和低空林草遥感"):
            self.assertNotIn(phrase, SYSTEM)
        # The rules must stay domain-neutral about the user's intent.
        self.assertIn("围绕用户的实际目标工作", SYSTEM)

    def test_domain_specific_rules_moved_out_of_the_global_layer(self):
        """Detailed domain rules belong in on-demand guides, not every request."""
        moved = [
            "RTK元数据", "主航线", "起飞前/起飞后参考板",
            "像元大小单位是度", "prosail", "冠层",
        ]
        for phrase in moved:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, SYSTEM)

    def test_core_execution_contract_stays_in_the_global_layer(self):
        """Rules that apply to every task must not be deferred to a guide."""
        for phrase in (
            "工具调用已返回", "后台作业已完成", "用户目标已完成",
            "job_wait", "environment_check", "blocked_by", "resource_busy",
            "不是授权",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SYSTEM)


if __name__ == "__main__":
    unittest.main()
