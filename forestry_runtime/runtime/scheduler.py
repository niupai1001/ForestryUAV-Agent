"""Decide which calls may run at the same time, from what they touch.

Every call is currently serial: ``parallel_tool_calls`` is off, each tool is declared
``sequential``, and the agent runs at ``max_concurrency=1``. That is safe and it is
also why four independent directory reads take four times as long as they need to --
and, worse, why the only way to express "these two are independent" was to not express
it at all.

The opposite mistake would be worse: two writes to one file interleaved, or an install
racing a code run against the environment it is changing. So concurrency is decided by
what a call touches, not by whether it looks safe:

    pure_read            -> semaphore only, runs alongside anything
    resource_read        -> semaphore, plus the resource's lock if a write holds it
    resource_write       -> exclusive lock on that resource
    environment_mutation -> one global lock; the environment is not divisible
    external_side_effect -> serialized; a side effect that may not be replayable
                            must never be issued twice at once

The model proposes calls; it does not hold locks. Letting the model acquire a lock is
how a lock gets held across a model round-trip and the Run deadlocks.

**Integration state.** The scheduler is in the tool dispatch path: ``_execute_tool``
builds a ``CallPlan`` per call from the tool's declaration and runs the call through
it. Concurrency is still *off* -- the Agent issues one call per request and
``SCHEDULER_PARALLEL_READS`` defaults to 1, so every call runs alone and behaviour is
unchanged from the serial baseline. That is deliberate: turning concurrency on is a
behavioural change with its own measurement, and the baseline this repo is graded
against was captured serially. Wiring the scheduler in first means turning it on later
is one setting, not a rewrite of the dispatch loop.

Only the call itself is scheduled. Dedup, the failure preflight and the stall guards
are decisions about *whether* to call, and they run before the scheduler is consulted:
a lock held across bookkeeping is how a scheduler becomes the reason two independent
reads wait for each other.

``environment_mutation`` is the one case worth reading the implementation for. Saying
"the environment is not divisible" is easy; making it true means an install must not
overlap a *read*, and a read does not take a resource lock that an install could wait
on. So a global-class call drains every read slot before it starts: reads already in
flight finish, no new one can begin, and the install runs alone. The alternative --
a global lock that only global-class calls take -- looks like exclusion in the code
and is not exclusion at runtime.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

ConcurrencyClass = (
    "pure_read", "resource_read", "resource_write",
    "environment_mutation", "external_side_effect",
)

#: Classes that must not overlap with anything else touching the same thing.
EXCLUSIVE_CLASSES = frozenset({"resource_write"})
#: Classes that must not overlap with anything at all.
GLOBAL_CLASSES = frozenset({"environment_mutation", "external_side_effect"})

ENVIRONMENT_LOCK = "*environment*"


@dataclass
class CallPlan:
    name: str
    concurrency_class: str = "resource_read"
    resource_keys: list[str] = field(default_factory=list)

    @property
    def is_write(self) -> bool:
        return self.concurrency_class in EXCLUSIVE_CLASSES

    @property
    def needs_global_lock(self) -> bool:
        return self.concurrency_class in GLOBAL_CLASSES


# ---------------------------------------------------------------------------
# Classifying a call
# ---------------------------------------------------------------------------
# A call's class is derived from the tool's declaration, not listed per tool name.
# A hand-written name -> class table is the kind of thing that is right on the day
# it is written and wrong the day a tool is added; the declarations already say
# whether a tool writes, and they are the contract a new tool has to satisfy anyway.
#
# What the declaration cannot say is *which* thing is touched -- that is in the
# arguments. So the class comes from the declaration and the keys come from the call,
# and a read that names no path is `pure_read` (nothing to conflict with) while the
# same tool naming a path is `resource_read`.

#: Argument fields that name something on disk.
PATH_FIELDS = ("path", "folder_path", "file_path", "target_path", "source_path")
#: Argument fields that name something identified, not located.
ID_FIELDS = ("job_id", "source_id", "artifact_id", "document_id", "result_id")

#: ``side_effect`` -> concurrency class, before arguments are considered.
SIDE_EFFECT_CLASS = {
    "none": "resource_read",
    "file_write": "resource_write",
    "durable_job": "resource_write",
    "external": "external_side_effect",
}

#: Tools whose effect outlives the file they were pointed at: they change what the
#: interpreter can import, which every later call depends on. Reads must not run
#: against a half-installed environment, so these take the global lock even though
#: their declaration only says ``durable_job``.
ENVIRONMENT_MUTATING_TOOLS = frozenset({"dependency_install", "code_run"})


def resource_keys_of(arguments: dict) -> list[str]:
    """What this call names, as conflict keys. Empty means "nothing in particular"."""
    if not isinstance(arguments, dict):
        return []
    keys = []
    for field_name in PATH_FIELDS:
        value = arguments.get(field_name)
        if isinstance(value, str) and value.strip():
            keys.append("path:" + value.strip())
    for field_name in ID_FIELDS:
        value = arguments.get(field_name)
        if isinstance(value, str) and value.strip():
            keys.append(field_name + ":" + value.strip())
    return keys


def plan_for(name: str, arguments: dict = None, *, side_effect: str = "none") -> CallPlan:
    """Classify one call from what the tool declares and what the call names."""
    arguments = arguments or {}
    if name in ENVIRONMENT_MUTATING_TOOLS:
        # The class is global; the keys are still recorded so a trace can say what
        # the mutation was aimed at.
        return CallPlan(name, "environment_mutation", resource_keys_of(arguments))
    cls = SIDE_EFFECT_CLASS.get(str(side_effect).strip().lower(), "resource_read")
    keys = resource_keys_of(arguments)
    if cls == "resource_read" and not keys:
        # Nothing named, so nothing to conflict with: safe beside anything.
        cls = "pure_read"
    if cls in EXCLUSIVE_CLASSES and not keys:
        # A write that names no path still writes *something* (the workspace is the
        # default target); an empty key set would make it conflict with nothing,
        # which turns an exclusive class into a free-for-all.
        keys = ["path:*workspace*"]
    return CallPlan(name, cls, keys)


class ExecutionScheduler:
    """Run calls concurrently where that cannot change an outcome."""

    def __init__(self, max_parallel_reads: int = 4):
        self.max_parallel_reads = max(1, int(max_parallel_reads))
        self._read_slots = asyncio.Semaphore(self.max_parallel_reads)
        self._resource_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._resource_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._resource_locks[key] = lock
        return lock

    async def run(self, plan: CallPlan, call):
        """Execute one call under whatever it needs to hold."""
        if plan.needs_global_lock:
            return await self._run_exclusive(call)
        # Every call occupies one slot, including writes: a slot is "the machine is
        # doing something", and it is what a global-class call drains to be alone.
        async with self._read_slots:
            if not plan.is_write:
                return await call()
            keys = plan.resource_keys or [ENVIRONMENT_LOCK]
            # Held for the whole write, and acquired in a stable order so two writes
            # touching the same pair cannot deadlock against each other.
            locks = [self._lock_for(key) for key in sorted(set(keys))]
            for lock in locks:
                await lock.acquire()
            try:
                return await call()
            finally:
                for lock in reversed(locks):
                    lock.release()

    async def _run_exclusive(self, call):
        """Run alone: drain every slot, then hold the global lock."""
        held = 0
        try:
            for _ in range(self.max_parallel_reads):
                await self._read_slots.acquire()
                held += 1
            async with self._global_lock:
                return await call()
        finally:
            for _ in range(held):
                self._read_slots.release()

    async def gather(self, plans: list[tuple[CallPlan, object]]) -> list:
        """Run several calls, concurrently where the classes allow it."""
        return list(await asyncio.gather(
            *(self.run(plan, call) for plan, call in plans)
        ))

    def conflicts(self, left: CallPlan, right: CallPlan) -> bool:
        """Whether two calls may not run together. Exposed for tests and trace."""
        if left.needs_global_lock or right.needs_global_lock:
            return True
        if left.is_write and right.is_write:
            return bool(set(left.resource_keys) & set(right.resource_keys))
        if left.is_write or right.is_write:
            writer = left if left.is_write else right
            reader = right if left.is_write else left
            return bool(set(writer.resource_keys) & set(reader.resource_keys))
        return False


__all__ = [
    "ENVIRONMENT_LOCK", "EXCLUSIVE_CLASSES", "GLOBAL_CLASSES",
    "ENVIRONMENT_MUTATING_TOOLS", "ID_FIELDS", "PATH_FIELDS",
    "SIDE_EFFECT_CLASS", "CallPlan", "ConcurrencyClass", "ExecutionScheduler",
    "plan_for", "resource_keys_of",
]
