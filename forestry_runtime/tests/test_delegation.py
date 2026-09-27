"""Delegation is wired, bounded, and off until it has been measured.

The dangerous version of this feature is not the one that fails; it is the one that
works. A subagent that can see the parent's history, or that grants itself whatever the
parent wrote in its contract, or that can delegate in turn, is not isolation -- it is
the same conversation with extra steps and a larger bill.

So the tests here split into two groups. The first pins the *cost*: delegation is off by
default, which means the default tool schema is byte-identical to what it was before
any of this existed. Adding a tool is not free, and a tool that is unmeasured should not
be in the schema.

The second pins the *boundary*: a contract cannot widen its kind's policy, and a child
that is refused is refused visibly -- with the reason -- because a parent told only
"failed" will rephrase and delegate the same thing again.
"""
from __future__ import annotations

import os
import unittest

from runtime import subagents
from runtime.agent import _tools
from runtime.subagents import (
    ENV_FLAG, MAX_SUMMARY, POLICIES, DelegateInput, DelegationContract,
    SubagentResult, SubagentRunner, fold_result,
)


class _Env:
    """Set the flag for the length of a block, and put it back."""

    def __init__(self, value: str):
        self.value = value
        self.previous: str | None = None

    def __enter__(self):
        self.previous = os.environ.get(ENV_FLAG)
        os.environ[ENV_FLAG] = self.value
        return self

    def __exit__(self, *exc):
        if self.previous is None:
            os.environ.pop(ENV_FLAG, None)
        else:
            os.environ[ENV_FLAG] = self.previous
        return False


class DefaultOffTest(unittest.TestCase):
    def test_delegation_is_off_by_default(self):
        with _Env("false"):
            self.assertFalse(subagents.enabled())
            names = [tool.name for tool in _tools(True)[0]]
            self.assertNotIn("delegate", names)

    def test_turning_it_on_adds_exactly_one_tool(self):
        with _Env("true"):
            self.assertTrue(subagents.enabled())
            tools, visible_count, schema_chars = _tools(True)
            self.assertIn("delegate", [tool.name for tool in tools])
        with _Env("false"):
            _, off_count, off_chars = _tools(True)
        # The schema is what the model sees every request, so the cost of the tool is
        # stated in the same currency the budget is measured in.
        self.assertEqual(visible_count, off_count + 1)
        self.assertGreater(schema_chars, off_chars)

    def test_the_flag_is_not_case_or_space_sensitive(self):
        with _Env(" True "):
            self.assertTrue(subagents.enabled())


class SchemaTest(unittest.TestCase):
    def test_the_objective_is_the_one_required_field(self):
        schema = DelegateInput.model_json_schema()
        self.assertIn("objective", schema["properties"])
        self.assertEqual(schema.get("required"), ["objective"])

    def test_the_description_warns_that_the_child_cannot_see_the_conversation(self):
        # A model that does not know this writes objectives that only make sense to
        # itself, and gets back a child that cannot start.
        self.assertIn("cannot see", DelegateInput.model_fields["objective"].description)


class FoldTest(unittest.TestCase):
    def test_observations_become_facts_and_failures_are_kept(self):
        observations = [
            {"tool": "fs_read", "ok": True, "summary": "3 files"},
            {"tool": "fs_search", "ok": False, "summary": "permission denied"},
        ]
        result = fold_result(status="success", output="found it",
                             observations=observations)
        self.assertEqual(result.status, "success")
        self.assertIn("fs_read: 3 files", result.facts)
        # A dropped dead end is a repeated dead end: the parent has to hear about it.
        self.assertIn("FAILED fs_search: permission denied", result.facts)

    def test_a_summary_longer_than_the_budget_is_cut(self):
        result = fold_result(output="x" * (MAX_SUMMARY + 500))
        self.assertEqual(len(result.summary), MAX_SUMMARY)

    def test_an_unknown_status_is_reported_as_failed_not_success(self):
        # Over-claiming is the failure mode this whole layer exists to prevent.
        self.assertEqual(fold_result(status="maybe").status, "failed")

    def test_artifacts_are_carried_back_as_ids(self):
        result = fold_result(artifacts=["a1", "a2"])
        self.assertEqual(result.artifact_ids, ["a1", "a2"])

    def test_the_result_is_bounded_before_the_parent_sees_it(self):
        observations = [{"tool": "fs_read", "ok": True, "summary": "y"}] * 500
        bounded = fold_result(observations=observations).bounded()
        self.assertLessEqual(len(bounded.facts), 20)


class BoundaryTest(unittest.TestCase):
    def test_a_contract_cannot_widen_its_kinds_policy(self):
        # The parent model writes the contract. If the contract were enough, the
        # permission boundary would be whatever the model asked for.
        contract = DelegationContract(
            objective="anything", kind="explore",
            allowed_tools=["fs_read", "fs_write", "code_run", "dependency_install"],
        )
        effective = set(contract.effective_tools())
        self.assertTrue(effective <= set(POLICIES["explore"].allowed_tools))
        self.assertNotIn("dependency_install", effective)

    def test_a_child_may_not_delegate(self):
        self.assertEqual(DelegationContract(objective="x", depth=1).depth, 1)
        runner = SubagentRunner()
        allowed, why = runner.may_delegate(
            DelegationContract(objective="x", depth=1)
        )
        self.assertFalse(allowed)
        self.assertIn("depth", why)

    def test_an_unknown_kind_is_refused_naming_the_kind(self):
        allowed, why = SubagentRunner().may_delegate(
            DelegationContract(objective="x", kind="researcher")
        )
        self.assertFalse(allowed)
        self.assertIn("researcher", why)

    def test_a_delegation_with_no_objective_is_refused(self):
        allowed, why = SubagentRunner().may_delegate(DelegationContract())
        self.assertFalse(allowed)
        self.assertIn("objective", why)


class ChildResultTest(unittest.TestCase):
    def test_a_refused_child_reports_blocked_with_the_reason(self):
        # No model is called here: the boundary refuses before anything runs.
        import asyncio

        from runtime.agent import _run_child

        result = asyncio.run(_run_child(
            None, DelegationContract(objective="x", kind="researcher"),
        ))
        self.assertEqual(result.status, "blocked")
        self.assertIn("researcher", result.summary)

    def test_a_refused_child_reports_blocked_without_an_objective(self):
        import asyncio

        from runtime.agent import _run_child

        result = asyncio.run(_run_child(None, DelegationContract(kind="explore")))
        self.assertEqual(result.status, "blocked")
        self.assertIn("objective", result.summary)

    def test_a_blocked_result_still_crosses_back_as_a_dict(self):
        # What the parent actually receives. A result it cannot read is a result the
        # model will guess at.
        payload = SubagentResult(status="blocked", summary="no").as_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["summary"], "no")


if __name__ == "__main__":
    unittest.main()
