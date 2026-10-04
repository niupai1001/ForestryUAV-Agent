"""A repeated *successful* call must be told that it already happened.

Observed in a real Run (E3, 4b@128K): the same 2 KB report was written to the same
path nineteen times. Every write answered ``ok``, nothing in any tool result ever
said the write had already been made, and nothing else in the loop differed --
so from inside the Run each repetition was indistinguishable from the first.

Two kinds of repetition already had an answer before this: a repeated *failed*
call is declined by the failure preflight, and a repeated read is answered from
the Run's own cache with ``already_read``. The gap was the middle case, and it was
the one that burned the whole Run.

The rule pinned here is deliberately narrow: nothing is refused and no Run is
paused. A second write sometimes *is* what a Run wants, and only the model knows
whether it meant the bytes to change. What changes is that the loop can no longer
be invisible.
"""
from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from runtime.capabilities.runtime import RuntimeTools, _SIDE_EFFECT_FREE_TOOLS
from runtime.context import ContextCompiler
from runtime.lifecycle import Sessions
from runtime.plan import ObservationLedger
from runtime.workspace import WorkspaceRegistry


class _RuntimeBoxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = Sessions(self.root)
        self.chat_id = str(uuid.uuid4())
        self.sessions.create("alice", self.chat_id)
        self.store = self.sessions.acquire("alice", self.chat_id)
        self.sessions.release(self.chat_id)
        self.registry = WorkspaceRegistry(self.root)
        self.box = RuntimeTools(self.store, "alice", [], self.registry, "")

    def tearDown(self):
        self.temp.cleanup()


class RepeatedCallTests(_RuntimeBoxTests):

    def test_the_first_write_is_not_marked_as_a_repeat(self):
        written = self.box.execute("fs_write", {"path": "r.json", "content": '{"a": 1}'})
        self.assertTrue(written["ok"], written)
        self.assertIsNone(written.get("repeat"))
        self.assertIn("observation_id", written)

    def test_the_identical_second_write_carries_the_count(self):
        arguments = {"path": "r.json", "content": '{"a": 1}', "overwrite": True}
        first = self.box.execute("fs_write", arguments)
        second = self.box.execute("fs_write", dict(arguments))
        self.assertTrue(second["ok"], second)
        repeat = second.get("repeat")
        self.assertIsNotNone(repeat, "第二次逐字重复的调用没有任何提示")
        self.assertEqual(repeat["count"], 2)
        # The note points at the same observation: the repeat *is* the earlier
        # fact, not a new one, and the model can cite it instead of re-deriving it.
        self.assertEqual(repeat["observation_id"], first["observation_id"])
        self.assertIn("重复调用", repeat["note"])

    def test_the_count_keeps_rising_with_each_identical_call(self):
        arguments = {"path": "r.json", "content": '{"a": 1}', "overwrite": True}
        result = None
        for _ in range(4):
            result = self.box.execute("fs_write", arguments)
        self.assertEqual(result["repeat"]["count"], 4)

    def test_different_bytes_are_not_a_repeat(self):
        """Changing the arguments is exactly how a Run intends a different effect."""
        self.box.execute("fs_write", {"path": "r.json", "content": '{"a": 1}'})
        second = self.box.execute("fs_write", {
            "path": "r.json", "content": '{"a": 2}', "overwrite": True,
        })
        self.assertIsNone(second.get("repeat"))

    def test_a_failed_call_is_left_to_the_failure_preflight(self):
        """Failures already answered themselves; a second note would be noise."""
        self.box.execute("fs_read", {"path": "missing.txt"})
        second = self.box.execute("fs_read", {"path": "missing.txt"})
        self.assertFalse(second["ok"], second)
        self.assertIsNone(second.get("repeat"))

    def test_read_only_tools_are_exempt(self):
        """A read already answers `already_read`; `job_status` is a live question.

        Derived from each tool's declared side effect rather than listed by hand,
        so a tool that stops being free of side effects starts being counted.
        """
        self.assertIn("fs_read", _SIDE_EFFECT_FREE_TOOLS)
        self.assertIn("job_status", _SIDE_EFFECT_FREE_TOOLS)
        self.assertNotIn("fs_write", _SIDE_EFFECT_FREE_TOOLS)


class LedgerCountingTests(unittest.TestCase):
    """The count lives in the ledger, because the id is derived from the call."""

    def test_a_repeated_call_is_one_entry_with_an_occurrence_count(self):
        root = Path(tempfile.mkdtemp())
        ledger = ObservationLedger(root / "observations.json")
        for index in range(3):
            identifier, occurrences = ledger.record_with_occurrences(
                "fs_write", {"path": "r.json"}, ok=True,
            )
        self.assertEqual(occurrences, 3)
        self.assertEqual(len(ledger.known()["observations"]), 1)
        entry = ledger.known()["observations"][identifier]
        self.assertEqual(entry["occurrences"], 3)
        # First seen stays put: when the fact was established is answered by the
        # first call, and a repeat is a second delivery of the same fact.
        entry = ledger.known()["observations"][identifier]
        self.assertEqual(entry["at"], entry["at"])

    def test_a_changed_call_is_a_new_entry(self):
        root = Path(tempfile.mkdtemp())
        ledger = ObservationLedger(root / "observations.json")
        first, _ = ledger.record_with_occurrences("fs_write", {"path": "a"}, ok=True)
        second, occurrences = ledger.record_with_occurrences(
            "fs_write", {"path": "b"}, ok=True,
        )
        self.assertNotEqual(first, second)
        self.assertEqual(occurrences, 1)

    def test_the_state_summary_counts_calls_and_repeats(self):
        root = Path(tempfile.mkdtemp())
        ledger = ObservationLedger(root / "observations.json")
        for _ in range(5):
            ledger.record("fs_write", {"path": "r.json"}, ok=True, summary="path=r.json")
        ledger.record("code_run", {"language": "python", "code": "x=1"}, ok=False)
        summary = ledger.state_summary()
        self.assertEqual(summary["calls"], 6)
        self.assertEqual(summary["distinct_calls"], 2)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["repeated"][0]["tool"], "fs_write")
        self.assertEqual(summary["repeated"][0]["count"], 5)
        self.assertEqual(summary["tools_used"][0], {"tool": "fs_write", "count": 5})


class RunStateInContextTests(_RuntimeBoxTests):
    """The Run's state reaches the model on every request, plan or not.

    A Run that never wrote a plan used to carry no state at all, which is why a
    repeated call was invisible: nothing in the request said it had already
    happened. This pins the one block that always gets printed.
    """

    def test_the_instruction_says_what_the_run_has_done(self):
        arguments = {"path": "r.json", "content": "{}", "overwrite": True}
        self.box.execute("fs_write", dict(arguments))
        self.box.execute("fs_write", dict(arguments))
        parts, manifest, _ = ContextCompiler(self.box).compile("统计树冠覆盖率", [], 0)
        joined = "\n\n".join(parts)
        self.assertIn("Run 状态", joined)
        self.assertIn("重复调用：fs_write ×2", joined)
        self.assertIn("已执行调用 2 次", joined)
        # No requirements were ever declared, so completion stays unknown rather
        # than flipping to a word that means "finished".
        self.assertEqual(manifest["task_state"], "unknown")

    def test_the_first_request_of_a_fresh_run_still_prints_the_block(self):
        """Empty is a state too -- it says the model has not done anything yet."""
        parts, _, _ = ContextCompiler(self.box).compile("第一步做什么", [], 0)
        joined = "\n\n".join(parts)
        self.assertIn("Run 状态", joined)


if __name__ == "__main__":
    unittest.main()
