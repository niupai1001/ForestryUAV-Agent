"""A repeated read must not hand back the same body again.

Observed in a real Run: the model read one domain guide 124 times with byte-identical
arguments, received byte-identical content every time, and spent 129 model calls
before the budget stopped it. The failure guard did not fire because every call
*succeeded* -- and a successful call that returns the same bytes reads to the model as
progress, so it asks again.

These tests pin the rule: a read-only call whose arguments and evidence generation are
unchanged is answered from the record with a marker. A read that could have changed,
or that differs at all, is served normally.
"""
from __future__ import annotations

import asyncio
import json
import unittest

from pydantic_ai import RunContext

from runtime.agent import AgentDependencies, PURE_READ_TOOLS, _execute_tool

try:  # the usage type moved between pydantic_ai releases
    from pydantic_ai.usage import RunUsage
except ImportError:  # pragma: no cover - older layout
    from pydantic_ai import Usage as RunUsage

GUIDE = "# 指南正文\n\n" + ("内容。" * 400)


class _ReadToolbox:
    """Serves one read tool and counts how many times it was really executed."""

    def __init__(self, tool: str, payload: dict):
        self.tool = tool
        self.payload = payload
        self.executions = 0

    def execute(self, name, arguments, progress=None):
        self.executions += 1
        return {"ok": True, "data": dict(self.payload)}


def _deps(toolbox) -> AgentDependencies:
    return AgentDependencies(
        toolbox=toolbox, cancelled=lambda: False, pause_requested=lambda: False,
    )


def _context(deps) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _call(deps, name, arguments, domain=False):
    return asyncio.run(_execute_tool(_context(deps), name, arguments, domain))


class RepeatedReadTests(unittest.TestCase):
    def test_the_second_identical_read_is_answered_from_the_record(self):
        toolbox = _ReadToolbox("domain_guide", {
            "citation": "guide://rgb-canopy-cover@1", "content": GUIDE,
        })
        deps = _deps(toolbox)

        first = _call(deps, "domain_guide", {"guide_id": "rgb-canopy-cover"})
        self.assertTrue(first["ok"])
        self.assertEqual(first["data"]["content"], GUIDE)
        self.assertEqual(toolbox.executions, 1)

        second = _call(deps, "domain_guide", {"guide_id": "rgb-canopy-cover"})
        # The toolbox is not consulted again: nothing it could return has changed.
        self.assertEqual(toolbox.executions, 1)
        self.assertTrue(second["data"].get("already_read"))
        self.assertEqual(second["data"]["repeat"], 2)
        # And crucially the body is not repeated, because re-sending it is what made
        # the repeated call look like progress.
        self.assertNotIn("content", second["data"])

    def test_the_marker_names_what_was_already_read(self):
        toolbox = _ReadToolbox("domain_guide", {
            "citation": "guide://rgb-canopy-cover@1", "content": GUIDE,
        })
        deps = _deps(toolbox)
        _call(deps, "domain_guide", {"guide_id": "rgb-canopy-cover"})
        second = _call(deps, "domain_guide", {"guide_id": "rgb-canopy-cover"})
        self.assertIn("rgb-canopy-cover", json.dumps(second["data"], ensure_ascii=False))

    def test_a_different_read_is_served_normally(self):
        toolbox = _ReadToolbox("domain_guide", {"content": GUIDE})
        deps = _deps(toolbox)
        _call(deps, "domain_guide", {"guide_id": "rgb-canopy-cover"})
        other = _call(deps, "domain_guide", {"guide_id": "forest-method-boundaries"})
        self.assertEqual(toolbox.executions, 2)
        self.assertNotIn("already_read", other["data"])

    def test_new_evidence_reopens_the_read(self):
        """A read repeated after the workspace changed is new work, not a repeat."""
        toolbox = _ReadToolbox("fs_read", {"text": "a"})
        deps = _deps(toolbox)
        _call(deps, "fs_read", {"path": "notes.txt"})
        # A write happened, so the same read may legitimately return something else.
        deps.evidence_generation += 1
        again = _call(deps, "fs_read", {"path": "notes.txt"})
        self.assertEqual(toolbox.executions, 2)
        self.assertNotIn("already_read", again["data"])

    def test_a_new_environment_reopens_the_read(self):
        toolbox = _ReadToolbox("environment_check", {"modules": []})
        deps = _deps(toolbox)
        _call(deps, "environment_check", {"modules": ["rasterio"]})
        # An install landed; the same question now has a different answer.
        deps.environment_generation += 1
        again = _call(deps, "environment_check", {"modules": ["rasterio"]})
        self.assertEqual(toolbox.executions, 2)
        self.assertNotIn("already_read", again["data"])

    def test_a_write_tool_is_never_answered_from_the_record(self):
        """Side effects must still happen: the guard covers reads only."""
        for tool in ("code_run", "fs_write", "artifacts_preview", "dependency_install"):
            with self.subTest(tool=tool):
                self.assertNotIn(tool, PURE_READ_TOOLS)

    def test_repeated_reads_end_the_turn_with_an_explanation(self):
        """A stuck model is stopped, and the reason names what it repeated."""
        toolbox = _ReadToolbox("domain_guide", {"content": GUIDE})
        deps = _deps(toolbox)
        for _ in range(5):
            _call(deps, "domain_guide", {"guide_id": "rgb-canopy-cover"})
        self.assertIsNotNone(deps.pause_reason)
        self.assertEqual(deps.pause_blocker, "repeated_read")
        self.assertIn("重复", deps.pause_reason)

    def test_the_guard_covers_every_pure_read_tool(self):
        """The set is derived from the declarations, so it cannot drift."""
        self.assertIn("domain_guide", PURE_READ_TOOLS)
        self.assertIn("knowledge_search", PURE_READ_TOOLS)
        self.assertIn("job_status", PURE_READ_TOOLS)

    def test_a_failed_read_is_not_recorded_as_served(self):
        class _Failing:
            def execute(self, name, arguments, progress=None):
                return {"ok": False, "error": "no such guide",
                        "failure": {"stage": "preconditions", "code": "AssetError",
                                    "reason": "not_found",
                                    "requested_path": "missing"}}

        deps = _deps(_Failing())
        _call(deps, "domain_guide", {"guide_id": "nope"})
        self.assertEqual(deps.read_results, {})


if __name__ == "__main__":
    unittest.main()
