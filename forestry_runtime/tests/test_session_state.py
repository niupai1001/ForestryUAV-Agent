import unittest

from runtime.session.state import ACTIVE, PAUSABLE, TERMINAL, transition
from runtime.storage import AssetError


class RunStateTests(unittest.TestCase):
    def test_state_sets_are_disjoint_and_expected(self):
        self.assertEqual(TERMINAL, {
            "completed", "failed", "canceled", "cancel_incomplete",
        })
        self.assertEqual(ACTIVE, {"queued", "running", "waiting", "canceling"})
        self.assertEqual(PAUSABLE, {"queued", "running"})
        self.assertFalse(TERMINAL & ACTIVE)

    def test_valid_transitions_and_idempotent_state_are_allowed(self):
        for current, target in (
            ("queued", "running"), ("running", "waiting"),
            ("waiting", "paused"), ("paused", "queued"),
            ("running", "canceling"), ("canceling", "cancel_incomplete"),
            ("running", "running"),
        ):
            with self.subTest(current=current, target=target):
                transition(current, target)

    def test_illegal_transition_and_terminal_exit_are_rejected(self):
        for current, target in (
            ("queued", "completed"), ("paused", "running"),
            ("completed", "running"), ("cancel_incomplete", "queued"),
        ):
            with self.subTest(current=current, target=target):
                with self.assertRaises(AssetError):
                    transition(current, target)


if __name__ == "__main__":
    unittest.main()
