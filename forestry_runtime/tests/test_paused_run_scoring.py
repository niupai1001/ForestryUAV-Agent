"""Contract tests for the paused-Run scoring rule.

A Run the Runtime stops deliberately -- the repeated-failure guard rail, an
exhausted model-call budget, or a pause request -- is an Agent outcome. Scoring it
as ``infra_error`` kept the most instructive failure mode in the denominator as
"untested" instead of recording that the Agent got stuck.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.verify.base import status_for_terminal  # noqa: E402


class PausedRunScoringTests(unittest.TestCase):
    def test_a_paused_run_is_a_verified_failure(self):
        for state in ("paused",):
            with self.subTest(state=state):
                self.assertEqual(status_for_terminal(state), "crash")

    def test_the_other_terminal_states_keep_their_meaning(self):
        self.assertEqual(status_for_terminal("completed"), "evaluated")
        self.assertEqual(status_for_terminal("failed"), "crash")
        self.assertEqual(status_for_terminal("canceled"), "crash")
        self.assertEqual(status_for_terminal("cancel_incomplete"), "crash")
        self.assertEqual(status_for_terminal("timeout"), "timeout")

    def test_unreachable_or_still_running_stays_unknown(self):
        """Infrastructure problems must not be charged to the Agent."""
        for state in (None, "", "running", "queued", "waiting", "canceling"):
            with self.subTest(state=state):
                self.assertEqual(status_for_terminal(state), "infra_error")

    def test_a_case_that_expects_another_terminal_state_still_matches(self):
        self.assertEqual(status_for_terminal("paused", "paused"), "evaluated")


if __name__ == "__main__":
    unittest.main()
