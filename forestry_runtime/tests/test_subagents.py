"""A subagent is a context boundary, and that is the only thing it is.

The failure this test set exists to catch is a "subagent" that is the parent
conversation with a different system prompt: parent history copied in, parent's tools
available, result returned as prose. That costs exactly as much context as doing the
work inline, so it buys isolation only in the name.

So the assertions are about the boundary: the child's context is built from its
contract and nothing else, its tools are the narrower of contract and policy, depth
stops at one, and what comes back is bounded so a child cannot spend the parent's
context on the parent's behalf.
"""
from __future__ import annotations

import unittest

from runtime.subagents import (
    MAX_DEPTH, POLICIES, DelegationContract, SubagentResult, SubagentRunner,
)


def _contract(**kwargs) -> DelegationContract:
    base = {
        "objective": "find where the canopy threshold is applied",
        "kind": "explore",
        "allowed_tools": ["fs_read", "fs_search"],
    }
    base.update(kwargs)
    return DelegationContract(**base)


class PermissionTest(unittest.TestCase):
    def test_a_contract_cannot_grant_more_than_its_policy(self) -> None:
        contract = _contract(allowed_tools=["fs_read", "fs_write", "code_run"])
        self.assertNotIn(
            "fs_write", contract.effective_tools(),
            "explore is read-only; a contract saying otherwise must not make it so",
        )
        self.assertNotIn("code_run", contract.effective_tools())

    def test_a_contract_may_narrow_but_not_widen(self) -> None:
        narrow = _contract(allowed_tools=["fs_read"])
        self.assertEqual(narrow.effective_tools(), ("fs_read",))

    def test_an_empty_contract_falls_back_to_the_policy_not_to_everything(self) -> None:
        contract = _contract(allowed_tools=[])
        self.assertEqual(set(contract.effective_tools()), set(POLICIES["explore"].allowed_tools))

    def test_the_environment_policy_may_change_the_environment_but_not_the_source(self) -> None:
        policy = POLICIES["environment"]
        self.assertTrue(policy.may_mutate_environment)
        self.assertFalse(
            policy.may_write_source,
            "an agent fixing the environment must not fix the code to look fixed",
        )

    def test_the_verifier_policy_cannot_write(self) -> None:
        policy = POLICIES["verifier"]
        for tool in ("fs_write", "fs_edit", "dependency_install"):
            self.assertFalse(policy.permits(tool))

    def test_the_runner_enforces_the_same_boundary(self) -> None:
        runner = SubagentRunner()
        contract = _contract(allowed_tools=["fs_read"])
        self.assertTrue(runner.permits(contract, "fs_read"))
        self.assertFalse(runner.permits(contract, "fs_write"))


class DelegationGateTest(unittest.TestCase):
    def test_depth_is_fixed_at_one(self) -> None:
        self.assertEqual(MAX_DEPTH, 1)
        allowed, reason = SubagentRunner().may_delegate(_contract(depth=1))
        self.assertFalse(allowed)
        self.assertIn("depth", reason)

    def test_the_first_level_is_allowed(self) -> None:
        allowed, reason = SubagentRunner().may_delegate(_contract(depth=0))
        self.assertTrue(allowed, reason)

    def test_an_unknown_kind_is_refused(self) -> None:
        allowed, reason = SubagentRunner().may_delegate(_contract(kind="generalist"))
        self.assertFalse(allowed)
        self.assertIn("kind", reason)

    def test_a_delegation_without_an_objective_is_refused(self) -> None:
        allowed, reason = SubagentRunner().may_delegate(_contract(objective="   "))
        self.assertFalse(allowed)
        self.assertIn("objective", reason)


class ContextIsolationTest(unittest.TestCase):
    def test_the_parent_history_is_not_carried_over(self) -> None:
        parent_history = [
            {"role": "user", "content": "the user's original, long request"},
            {"role": "assistant", "content": "a long answer that must not be copied"},
            {"role": "tool", "content": "twelve file reads of dead ends"},
        ]
        runner = SubagentRunner()
        child = runner.build_child_context(_contract())
        # Deliberately no parent_history parameter exists; if one is ever added, this
        # is the assertion that should fail loudly rather than quietly cost context.
        rendered = "\n".join(str(part) for part in child)
        for message in parent_history:
            self.assertNotIn(message["content"], rendered)

    def test_the_child_context_states_the_objective_and_the_tools(self) -> None:
        child = SubagentRunner().build_child_context(_contract())
        rendered = child[0]["content"]
        self.assertIn("find where the canopy threshold is applied", rendered)
        self.assertIn("fs_read", rendered)

    def test_the_child_context_is_bounded(self) -> None:
        child = SubagentRunner().build_child_context(
            _contract(max_model_requests=3),
            retrieved=[], project_metadata={"project": "p", "owner": "o"},
        )
        self.assertEqual(len(child), 1)
        self.assertIn("3", child[0]["content"])


class ResultBoundTest(unittest.TestCase):
    def test_a_result_is_cut_down_to_what_belongs_in_a_parent_context(self) -> None:
        result = SubagentResult(
            summary="x" * 5000,
            facts=[str(index) for index in range(200)],
            open_questions=[f"q{index}" for index in range(40)],
        )
        bounded = result.bounded()
        self.assertLessEqual(len(bounded.summary), 1200)
        self.assertLessEqual(len(bounded.facts), 20)
        self.assertLessEqual(len(bounded.open_questions), 10)

    def test_the_status_survives_bound(self) -> None:
        bounded = SubagentResult(status="blocked", summary="could not finish").bounded()
        self.assertEqual(bounded.status, "blocked")

    def test_as_dict_is_serialisable_and_bounded(self) -> None:
        payload = SubagentResult(summary="y" * 3000, facts=["a"] * 30).as_dict()
        self.assertEqual(payload["status"], "success")
        self.assertLessEqual(len(payload["facts"]), 20)
        self.assertIsInstance(payload["summary"], str)


if __name__ == "__main__":
    unittest.main()
