"""The retrieval query is directed at what the plan still owes.

The query used to stay the Run's latest user text for the whole Run: the
*sources* were re-read on every step, but the *question* never moved, so a step
whose information need had drifted from the original request kept searching for
a request that was no longer the point. The fix is deliberately mechanical --
the plan store's unmet requirement texts are joined to the user's words on
every step -- rather than a model-written rewrite, which would spend exactly
the model requests this layer exists to save.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.context import ContextCompiler, _task_state_facts
from runtime.plan import ObservationLedger, PlanStore


def _store():
    root = Path(tempfile.mkdtemp())
    ledger = ObservationLedger(root / ".runtime" / "observations.json")
    return PlanStore(root, ledger), ledger


class _PlanBox:
    """A toolbox-shaped object whose only job is to expose the plan store."""

    def __init__(self, store):
        self._plan_store = lambda: store


class TaskStateTextTests(unittest.TestCase):
    def test_open_requirements_carry_their_text(self):
        """The id says which requirement is open; the text says what it asks."""
        store, _ = _store()
        store.revise(objective="check two directories",
                     requirements=[{"id": "r1", "text": "检查目录 A 并列出树种"},
                                   {"id": "r2", "text": "统计平均树高"}])
        facts = _task_state_facts(_PlanBox(store))
        self.assertEqual(facts["completion"], "open")
        self.assertEqual(facts["open_requirement_texts"],
                         ["检查目录 A 并列出树种", "统计平均树高"])

    def test_no_plan_still_carries_a_state_counted_from_the_ledger(self):
        """A Run without a plan is not a Run without a state.

        The old contract returned ``{}`` here, which left a model reconstructing
        "what have I already done" from its own history every step -- the
        reconstruction that fails exactly when repetition matters. What the Runtime
        can state without guessing is what it recorded: how many calls, how many
        worked, and whether any call was issued more than once.
        """
        root = Path(tempfile.mkdtemp())
        ledger = ObservationLedger(root / ".runtime" / "observations.json")
        for _ in range(3):
            ledger.record(
                "fs_write", {"path": "report.json"}, ok=True, summary="path=report.json",
            )
        facts = _task_state_facts(_PlanBox(PlanStore(root, ledger)))
        self.assertEqual(facts["source"], "ledger")
        self.assertFalse(facts["plan_present"])
        # Nothing was split into requirements, so nothing can be settled: "unknown"
        # rather than "complete", which would have been a completion ticket written
        # by a machine that cannot know what the user asked for.
        self.assertEqual(facts["completion"], "unknown")
        self.assertEqual(facts["open_requirement_texts"], [])
        self.assertEqual(facts["calls"], 3)
        self.assertEqual(facts["distinct_calls"], 1)
        self.assertEqual([item["tool"] for item in facts["repeated"]], ["fs_write"])
        self.assertEqual(facts["repeated"][0]["count"], 3)

    def test_a_plan_with_requirements_keeps_the_settled_word(self):
        """When requirements exist they own `completion`; the ledger is extra."""
        store, ledger = _store()
        ledger.record("fs_write", {"path": "r.json"}, ok=True)
        store.revise(objective="写报告", requirements=[{"id": "r1", "text": "写出报告 JSON"}])
        facts = _task_state_facts(_PlanBox(store))
        self.assertEqual(facts["source"], "plan")
        self.assertTrue(facts["plan_present"])
        self.assertEqual(facts["completion"], "open")
        self.assertEqual(facts["calls"], 1)


class DirectedQueryTests(unittest.TestCase):
    def _compiler(self, task_state) -> ContextCompiler:
        return ContextCompiler(None, task_state=task_state)

    def test_the_query_joins_the_unmet_requirements(self):
        compiler = self._compiler(lambda: {
            "completion": "open",
            "open_requirements": ["r1"],
            "open_requirement_texts": ["统计平均树高"],
        })
        self.assertEqual(
            compiler._directed_query("处理 1605 白桦样地"),
            "处理 1605 白桦样地\n统计平均树高",
        )

    def test_without_open_requirements_the_query_is_untouched(self):
        compiler = self._compiler(lambda: {})
        self.assertEqual(compiler._directed_query("原始问题"), "原始问题")

    def test_an_empty_user_query_still_carries_the_requirements(self):
        compiler = self._compiler(lambda: {
            "completion": "open",
            "open_requirement_texts": ["生成 CHM"],
        })
        self.assertEqual(compiler._directed_query("  "), "生成 CHM")

    def test_a_broken_task_state_never_breaks_the_query(self):
        def broken():
            raise RuntimeError("plan store offline")

        compiler = self._compiler(broken)
        self.assertEqual(compiler._directed_query("原始问题"), "原始问题")


if __name__ == "__main__":
    unittest.main()
