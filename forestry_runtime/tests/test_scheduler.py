"""Concurrency decided by what a call touches, and only by that.

The failure this test set exists to catch is a scheduler that is safe by accident: it
serialises everything, so no interleaving bug can ever appear, and the cost is paid on
every read-heavy Run. The opposite failure -- two writes to one file interleaved, or an
install racing a code run against the environment it is changing -- is worse and does
not show up until it corrupts something.

Both are asserted here, because "these two may run together" and "these two may not"
are different claims and each needs its own evidence. The evidence is observed
interleaving, not a declared policy: a lock that is documented but never taken is
indistinguishable from no lock.
"""
from __future__ import annotations

import asyncio
import unittest

from runtime.scheduler import (
    ENVIRONMENT_LOCK, GLOBAL_CLASSES, CallPlan, ExecutionScheduler,
)


def _plan(name: str, cls: str = "resource_read", *keys: str) -> CallPlan:
    return CallPlan(name=name, concurrency_class=cls, resource_keys=list(keys))


def _interleaved(events: list[tuple[str, str]]) -> bool:
    """Whether any call started before a different one had finished."""
    open_names: set[str] = set()
    for kind, name in events:
        if kind == "start":
            if open_names:
                return True
            open_names.add(name)
        else:
            open_names.discard(name)
    return False


def _overlaps_with(events: list[tuple[str, str]], name: str) -> bool:
    """Whether anything else ran while ``name`` was in flight."""
    open_names: set[str] = set()
    for kind, who in events:
        if kind == "start":
            if who != name and name in open_names:
                return True
            open_names.add(who)
        else:
            open_names.discard(who)
    return False


class ConflictTest(unittest.TestCase):
    def test_two_writes_to_one_resource_conflict(self) -> None:
        left = _plan("w1", "resource_write", "path:a")
        right = _plan("w2", "resource_write", "path:a")
        self.assertTrue(ExecutionScheduler().conflicts(left, right))

    def test_writes_to_different_resources_do_not_conflict(self) -> None:
        left = _plan("w1", "resource_write", "path:a")
        right = _plan("w2", "resource_write", "path:b")
        self.assertFalse(ExecutionScheduler().conflicts(left, right))

    def test_a_write_conflicts_with_a_read_of_the_same_resource(self) -> None:
        left = _plan("w", "resource_write", "path:a")
        right = _plan("r", "resource_read", "path:a")
        self.assertTrue(ExecutionScheduler().conflicts(left, right))

    def test_a_write_does_not_conflict_with_an_unrelated_read(self) -> None:
        left = _plan("w", "resource_write", "path:a")
        right = _plan("r", "resource_read", "path:z")
        self.assertFalse(ExecutionScheduler().conflicts(left, right))

    def test_pure_reads_never_conflict(self) -> None:
        scheduler = ExecutionScheduler()
        left = _plan("r1", "pure_read")
        right = _plan("r2", "pure_read")
        self.assertFalse(scheduler.conflicts(left, right))

    def test_environment_mutation_conflicts_with_everything(self) -> None:
        scheduler = ExecutionScheduler()
        install = _plan("install", "environment_mutation")
        for other in (_plan("r", "pure_read"), _plan("w", "resource_write", "path:a"),
                      _plan("x", "external_side_effect")):
            self.assertTrue(scheduler.conflicts(install, other))

    def test_the_environment_lock_is_not_a_resource_anyone_can_share(self) -> None:
        self.assertIn("environment_mutation", GLOBAL_CLASSES)
        self.assertNotEqual(ENVIRONMENT_LOCK, "")


class ExecutionTest(unittest.TestCase):
    def _run(self, plans, max_parallel_reads: int = 4) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        scheduler = ExecutionScheduler(max_parallel_reads=max_parallel_reads)

        async def call(name: str) -> str:
            events.append(("start", name))
            await asyncio.sleep(0.05)
            events.append(("end", name))
            return name

        async def main() -> None:
            await scheduler.gather([(plan, lambda p=plan: call(p.name)) for plan in plans])

        asyncio.run(main())
        return events

    def test_writes_to_one_resource_are_serialised(self) -> None:
        events = self._run([
            _plan("w1", "resource_write", "path:a"),
            _plan("w2", "resource_write", "path:a"),
        ])
        self.assertFalse(
            _interleaved(events),
            "two writes to one resource must never overlap",
        )

    def test_writes_to_different_resources_run_together(self) -> None:
        events = self._run([
            _plan("w1", "resource_write", "path:a"),
            _plan("w2", "resource_write", "path:b"),
        ])
        self.assertTrue(_interleaved(events), "independent writes should not be serialised")

    def test_reads_run_together(self) -> None:
        events = self._run([_plan("r1", "pure_read"), _plan("r2", "pure_read")])
        self.assertTrue(_interleaved(events))

    def test_the_read_semaphore_is_a_real_bound(self) -> None:
        events = self._run([_plan("r1", "pure_read"), _plan("r2", "pure_read")],
                           max_parallel_reads=1)
        self.assertFalse(_interleaved(events), "one read slot means reads serialise")

    def test_environment_mutation_excludes_everything_else(self) -> None:
        events = self._run([
            _plan("install", "environment_mutation"),
            _plan("r", "pure_read"),
            _plan("w", "resource_write", "path:elsewhere"),
        ])
        self.assertFalse(
            _overlaps_with(events, "install"),
            "an environment change must not race anything else",
        )

    def test_gather_returns_results_in_order(self) -> None:
        scheduler = ExecutionScheduler()
        plans = [_plan("r1", "pure_read"), _plan("r2", "pure_read")]

        async def main() -> list:
            return await scheduler.gather([
                (plans[0], lambda: asyncio.sleep(0, result="first")),
                (plans[1], lambda: asyncio.sleep(0, result="second")),
            ])

        self.assertEqual(asyncio.run(main()), ["first", "second"])


if __name__ == "__main__":
    unittest.main()
