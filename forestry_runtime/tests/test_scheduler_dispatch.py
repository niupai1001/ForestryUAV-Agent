"""A call's concurrency class comes from the tool's declaration, and dispatch goes through it.

Two separate claims, and each can be true while the other is false.

The first is that the *classification* is right. Hand-written name -> class tables are
right on the day they are written and wrong the day a tool is added, so the class is
derived from the same ``side_effect`` declarations the deduplication logic already
trusts. What those declarations cannot say is which thing a particular call touches --
that is in the arguments -- so a read naming no path is ``pure_read`` while the same
tool naming one is ``resource_read``.

The second is that the classification is *used*. A scheduler that exists, is tested, and
is never consulted is indistinguishable from no scheduler, so these tests watch what
dispatch actually hands to it. One asserts the opposite too: a call that is refused
before it runs must not reach the scheduler at all, because a decision about whether to
call must never hold a lock.
"""
from __future__ import annotations

import asyncio
import unittest

from pydantic_ai import RunContext

from runtime.agent import (
    TOOL_SIDE_EFFECTS, AgentDependencies, _call_plan, _execute_tool,
)
from runtime.scheduler import ENVIRONMENT_MUTATING_TOOLS, ExecutionScheduler

try:  # the usage type moved between pydantic_ai releases
    from pydantic_ai.usage import RunUsage
except ImportError:  # pragma: no cover - older layout
    from pydantic_ai import Usage as RunUsage


class _Toolbox:
    """Serves any tool successfully and remembers what was asked for."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.workspace = None

    def execute(self, name, arguments, progress=None):
        self.calls.append((name, dict(arguments or {})))
        return {"ok": True, "data": {"served": name}}


class _RecordingScheduler(ExecutionScheduler):
    """Behaves exactly like the real one, and records what it was asked to run."""

    def __init__(self, max_parallel_reads: int = 1):
        super().__init__(max_parallel_reads)
        self.plans: list = []

    async def run(self, plan, call):
        self.plans.append(plan)
        return await super().run(plan, call)


def _deps(toolbox, scheduler=None) -> AgentDependencies:
    deps = AgentDependencies(
        toolbox=toolbox, cancelled=lambda: False, pause_requested=lambda: False,
    )
    if scheduler is not None:
        deps.scheduler = scheduler
    return deps


def _call(deps, name, arguments, domain=False):
    context = RunContext(deps=deps, model=None, usage=RunUsage())
    return asyncio.run(_execute_tool(context, name, arguments, domain))


class ClassificationTest(unittest.TestCase):
    def test_classes_are_read_from_declarations_not_listed_by_hand(self):
        # The point of deriving is that a new tool is covered without anyone
        # remembering to edit a table. So the table has to actually be populated.
        self.assertTrue(TOOL_SIDE_EFFECTS)
        self.assertEqual(TOOL_SIDE_EFFECTS.get("fs_read"), "none")
        self.assertEqual(TOOL_SIDE_EFFECTS.get("fs_write"), "file_write")

    def test_a_read_that_names_nothing_may_run_beside_anything(self):
        plan = _call_plan("environment_check", {})
        self.assertEqual(plan.concurrency_class, "pure_read")

    def test_the_same_read_naming_a_path_is_confined_to_that_path(self):
        plan = _call_plan("fs_read", {"path": "/workspace/plot.tif"})
        self.assertEqual(plan.concurrency_class, "resource_read")
        self.assertEqual(plan.resource_keys, ["path:/workspace/plot.tif"])

    def test_a_write_is_exclusive_even_when_the_arguments_name_no_path(self):
        # An empty key set would mean "conflicts with nothing", which silently turns
        # an exclusive class into a free-for-all. The workspace is the default target.
        plan = _call_plan("fs_write", {})
        self.assertEqual(plan.concurrency_class, "resource_write")
        self.assertTrue(plan.resource_keys)

    def test_two_writes_to_different_paths_do_not_conflict(self):
        left = _call_plan("fs_write", {"path": "/w/a.txt"})
        right = _call_plan("fs_write", {"path": "/w/b.txt"})
        self.assertFalse(ExecutionScheduler().conflicts(left, right))

    def test_a_write_conflicts_with_a_read_of_the_same_path(self):
        # The reason a read is not always `pure_read`: this is the interleaving that
        # produces a half-written file being read as if it were whole.
        left = _call_plan("fs_write", {"path": "/w/a.txt"})
        right = _call_plan("fs_read", {"path": "/w/a.txt"})
        self.assertTrue(ExecutionScheduler().conflicts(left, right))

    def test_tools_that_change_the_environment_take_the_global_lock(self):
        # Their declaration only says `durable_job`, which would let a read run against
        # a half-installed interpreter. The environment is not divisible.
        for name in ("dependency_install", "code_run"):
            plan = _call_plan(name, {"path": "/w/x"})
            self.assertEqual(plan.concurrency_class, "environment_mutation", name)
            self.assertTrue(plan.needs_global_lock, name)
        self.assertEqual({"dependency_install", "code_run"}, set(ENVIRONMENT_MUTATING_TOOLS))

    def test_an_unknown_domain_tool_is_treated_as_a_write(self):
        # Domain capabilities are not in the core declarations and most of them produce
        # a product. Guessing "read" would let two heavy raster jobs run at once.
        plan = _call_plan("raster_ndvi", {"path": "/w/a.tif"}, domain=True)
        self.assertEqual(plan.concurrency_class, "resource_write")

    def test_identifiers_are_keys_too_not_only_paths(self):
        # Two calls on the same job conflict; two on different jobs do not.
        plan = _call_plan("job_status", {"job_id": "job-7"})
        self.assertEqual(plan.resource_keys, ["job_id:job-7"])


class DispatchTest(unittest.TestCase):
    def test_every_executed_call_is_handed_to_the_scheduler(self):
        toolbox = _Toolbox()
        scheduler = _RecordingScheduler()
        deps = _deps(toolbox, scheduler)

        _call(deps, "fs_read", {"path": "/workspace/plot.tif"})

        self.assertEqual(len(scheduler.plans), 1)
        self.assertEqual(scheduler.plans[0].name, "fs_read")
        self.assertEqual(toolbox.calls, [("fs_read", {"path": "/workspace/plot.tif"})])

    def test_the_plan_dispatch_builds_matches_the_declaration(self):
        scheduler = _RecordingScheduler()
        deps = _deps(_Toolbox(), scheduler)

        _call(deps, "fs_write", {"path": "/workspace/report.md"})

        self.assertEqual(scheduler.plans[0].concurrency_class, "resource_write")
        self.assertEqual(scheduler.plans[0].resource_keys, ["path:/workspace/report.md"])

    def test_a_call_refused_before_it_runs_never_reaches_the_scheduler(self):
        # Cancellation is a decision, not a call. Holding a semaphore across it would
        # make a cancelled Run occupy the very slot it is no longer going to use.
        scheduler = _RecordingScheduler()
        deps = AgentDependencies(
            toolbox=_Toolbox(), cancelled=lambda: True, pause_requested=lambda: False,
            scheduler=scheduler,
        )

        _call(deps, "fs_read", {"path": "/workspace/plot.tif"})

        self.assertEqual(scheduler.plans, [])

    def test_the_run_gets_one_scheduler_not_one_per_call(self):
        # Locks are only locks if they are shared. A scheduler built per call would
        # exclude nothing.
        deps = _deps(_Toolbox())
        _call(deps, "fs_read", {"path": "/workspace/a.tif"})
        first = deps.scheduler
        _call(deps, "fs_read", {"path": "/workspace/b.tif"})
        self.assertIsNotNone(first)
        self.assertIs(deps.scheduler, first)

    def test_concurrency_stays_off_until_it_is_turned_on(self):
        # The Agent issues one call per request and the default is one slot, so this
        # wiring is behaviour-preserving: it puts the scheduler in the path, not in
        # the way. Recorded so that raising the setting is a deliberate act.
        self.assertEqual(ExecutionScheduler(1).max_parallel_reads, 1)


class ConcurrencyChainTest(unittest.TestCase):
    """The switch has to move all three gates, or it promises what it cannot run.

    Read slots alone are not enough: with ``parallel_tool_calls`` off the model never
    asks, and with every tool a barrier the framework serialises the calls before the
    scheduler sees a second one. Each test below fails if one gate is left behind.
    """

    def test_the_default_is_serial_everywhere(self) -> None:
        from unittest import mock

        from runtime.agent import _parallel_reads, _settings

        with mock.patch.dict("os.environ", {"SCHEDULER_PARALLEL_READS": "1"}):
            self.assertEqual(1, _parallel_reads())
            self.assertFalse(_settings()["parallel_tool_calls"])
            self.assertTrue(self._any_tool().sequential)

    def test_the_switch_opens_the_model_licence_and_the_slots(self) -> None:
        from unittest import mock

        from runtime.agent import _parallel_reads, _settings
        from runtime.scheduler import ExecutionScheduler

        with mock.patch.dict("os.environ", {"SCHEDULER_PARALLEL_READS": "4"}):
            self.assertEqual(4, _parallel_reads())
            self.assertTrue(_settings()["parallel_tool_calls"])
            self.assertEqual(4, ExecutionScheduler(_parallel_reads()).max_parallel_reads)

    def test_the_switch_releases_the_per_tool_barrier(self) -> None:
        from unittest import mock

        with mock.patch.dict("os.environ", {"SCHEDULER_PARALLEL_READS": "4"}):
            self.assertFalse(self._any_tool().sequential)

    def test_delegation_stays_a_barrier_even_when_reads_overlap(self) -> None:
        """A child runs model requests against the same workspace; it must not overlap."""
        from unittest import mock

        from runtime.agent import _delegate_tool

        with mock.patch.dict("os.environ", {"SCHEDULER_PARALLEL_READS": "4"}):
            tool = _delegate_tool() if callable(_delegate_tool) else None
        self.assertIsNotNone(tool)
        self.assertTrue(tool.sequential)

    @staticmethod
    def _any_tool():
        from runtime.agent import _tool
        from pydantic import BaseModel

        class _Args(BaseModel):
            path: str = ""

        return _tool("fs_read", _Args, "Read a file.")


if __name__ == "__main__":
    unittest.main()
