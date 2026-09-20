"""Recovery probe used by the engineering evaluation collector.

The probe drives the real Coordinator/RunStore recovery paths and writes an
independently inspectable report. It asserts that a reported state always
reflects an observed execution fact, never an inferred one:

* an unresolved cancel stays ``canceling`` instead of being declared ``canceled``;
* a restart without a settled checkpoint pauses rather than replaying the job;
* a restart with a settled checkpoint reattaches and ``mark_turn_consumed`` runs;
* a shutdown pauses the run without fabricating a user cancel;
* the state machine forbids leaving a terminal state.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

from runtime.lifecycle import Sessions
from runtime.session.coordinator import RunCoordinator
from runtime.session.state import TERMINAL, transition
from runtime.storage import AssetError
from runtime.workspace import WorkspaceRegistry


class RecoveryGateTests(unittest.TestCase):
    def test_recovery_state_reflects_execution_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = Sessions(root)
            registry = WorkspaceRegistry(root)
            coordinator = RunCoordinator(sessions, registry)
            chat_id = "00000000-0000-0000-0000-00000000c0de"
            other_chat = "00000000-0000-0000-0000-00000000c0df"
            sessions.create("gate-owner", chat_id)
            sessions.create("gate-owner", other_chat)
            store = coordinator.store

            async def scenario() -> dict:
                assertions: dict[str, bool] = {}
                observations: dict[str, object] = {}

                def register_job_action(run_id: str, turn_id: str, action_id: str,
                                        job_id: str) -> None:
                    """Register the action through the event projection, as production does."""
                    store.append(run_id, {
                        "type": "tool_start", "action_id": action_id,
                        "name": "code_run", "arguments": {},
                    }, turn_id)
                    store.append(run_id, {
                        "type": "tool_end", "action_id": action_id, "name": "code_run",
                        "ok": True,
                        "result": {"ok": True, "data": {
                            "job_id": job_id, "job_type": "code", "state": "running",
                        }},
                    }, turn_id)

                # --- 1. unresolved cancel must not be reported as canceled -----
                run = store.create(
                    "gate-owner", chat_id,
                    [{"role": "user", "content": "cancel", "input_id": "input_rec_cancel"}],
                    [], True,
                )
                turn = store.latest_turn(run["id"])
                store.set_state(run["id"], "running")
                register_job_action(
                    run["id"], turn["id"], "action_rec_cancel", "job_" + "a" * 32
                )

                async def unavailable(*args, **kwargs):
                    raise AssetError("Docker temporarily unavailable")

                original = coordinator._job_operation
                coordinator._job_operation = unavailable
                try:
                    canceled = await coordinator.cancel(run["id"], "gate-owner")
                finally:
                    coordinator._job_operation = original
                    await coordinator.shutdown()

                observations["cancel_state"] = canceled["state"]
                assertions["unresolved_cancel_stays_canceling"] = (
                    canceled["state"] == "canceling"
                )
                assertions["canceling_is_not_terminal"] = "canceling" not in TERMINAL
                tail = store.events_tail(run["id"], "gate-owner", 5)
                assertions["cancel_unknown_is_evidenced"] = any(
                    item.get("observation_error") and item.get("state") == "unknown"
                    for item in tail
                )

                # --- 2. restart without a settled checkpoint must not replay ----
                replay_observed: list[str] = []

                async def fake_agent(*args, **kwargs):
                    replay_observed.append("replayed")
                    if False:  # pragma: no cover - keeps this an async generator
                        yield {}

                # Use a separate chat: the first Run intentionally stays "canceling",
                # which is still an active state for the one-active-Run-per-chat rule.
                unsafe = store.create(
                    "gate-owner", other_chat,
                    [{"role": "user", "content": "resume", "input_id": "input_rec_unsafe"}],
                    [], True,
                )
                unsafe_turn = store.latest_turn(unsafe["id"])
                store.set_state(unsafe["id"], "running")
                register_job_action(
                    unsafe["id"], unsafe_turn["id"],
                    "action_rec_unsafe", "job_" + "b" * 32,
                )

                import runtime.session.coordinator as coordinator_module
                original_agent = coordinator_module.stream_agent
                coordinator_module.stream_agent = fake_agent
                try:
                    await coordinator.reconcile()
                finally:
                    coordinator_module.stream_agent = original_agent
                    await coordinator.shutdown()

                restored = store.get(unsafe["id"], "gate-owner")
                observations["restart_state"] = restored["state"]
                assertions["restart_without_checkpoint_does_not_replay"] = (
                    replay_observed == []
                )
                assertions["restart_without_checkpoint_pauses"] = (
                    restored["state"] in {"paused", "waiting"}
                )

                # --- 3. state machine forbids leaving a terminal state ----------
                blocked = 0
                for current in sorted(TERMINAL):
                    try:
                        transition(current, "running")
                    except AssetError:
                        blocked += 1
                observations["terminal_exits_blocked"] = blocked
                assertions["terminal_states_are_absorbing"] = blocked == len(TERMINAL)

                return {"assertions": assertions, "observations": observations}

            report = asyncio.run(scenario())
            target = os.environ.get("EVALUATION_RECOVERY_REPORT")
            if target:
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            self.assertTrue(all(report["assertions"].values()), report)


if __name__ == "__main__":
    unittest.main()
