"""Restart recovery, durable-job monitoring, and cancellation convergence."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os


class RecoveryCoordinatorMixin:
    async def shutdown(self) -> None:
        self.stopping = True
        for task in self.monitor_tasks.values():
            if not task.done():
                task.cancel()
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        pending = [*self.monitor_tasks.values(), *self.tasks.values()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def reconcile(self) -> None:
        # Model calls cannot resume mid-token. Durable containers remain available;
        # the run is paused with explicit job observations for a safe continuation.
        for run in self.store.list_active():
            if run["cancel_requested"]:
                self.store.set_state(run["id"], "canceling")
                self.monitor_tasks[run["id"]] = asyncio.create_task(
                    self._monitor_cancel(run["id"], run["owner"], run["chat_id"])
                )
                continue
            turn = self.store.latest_turn(run["id"])
            turn_id = turn["id"] if turn else None
            uncertain = self.store.mark_inflight_actions_uncertain(
                run["id"], turn_id
            )
            if uncertain:
                self.store.append(run["id"], {
                    "type": "actions_reconciled",
                    "state": "unknown_after_crash",
                    "count": uncertain,
                }, turn_id)
            observations = []
            for ref in self.store.jobs(run["id"]):
                try:
                    status = await self._job_operation(
                        run, "job_status", ref["job_id"]
                    )
                except Exception as exc:
                    status = {
                        "job_id": ref["job_id"],
                        "job_type": ref.get("job_type"),
                        "state": "unknown", "error": str(exc), "terminal": False,
                        "observation_error": True,
                    }
                observations.append(status)
                self.store.append(run["id"], {"type": "job_reconciled", **status}, turn_id)
            active = [
                item for item in observations
                if not item.get("terminal") and not item.get("needs_finalization")
            ]
            if active:
                self.store.set_state(run["id"], "waiting")
                if turn_id:
                    self.store.set_turn_state(turn_id, "waiting")
                self.monitor_tasks[run["id"]] = asyncio.create_task(
                    self._monitor_recovered(
                        run["id"], run["owner"], run["chat_id"], active, turn_id
                    )
                )
            elif observations:
                await self._resume_recovered(run["id"], run["owner"])
            else:
                self.store.set_state(run["id"], "paused")

    async def _monitor_recovered(
        self, run_id: str, owner: str, chat_id: str, active: list[dict],
        turn_id: str | None = None,
    ) -> None:
        offsets = {item["job_id"]: int(item.get("offset", 0)) for item in active}
        states = {item["job_id"]: item.get("state") for item in active}
        interval = max(1, int(os.getenv("RECOVERY_POLL_SECONDS", "5")))
        try:
            with self.sessions.hold(chat_id):
                while offsets and not self.store.is_cancelled(run_id):
                    for job_id in list(offsets):
                        try:
                            run = self.store.get(run_id, owner)
                            status = await self._job_operation(
                                run, "job_status", job_id,
                                offset=offsets[job_id],
                            )
                        except Exception as exc:
                            status = {
                                "job_id": job_id, "state": "unknown",
                                "terminal": False, "error": str(exc),
                                "observation_error": True,
                            }
                        changed = status.get("state") != states[job_id] or bool(status.get("output")) or status.get("terminal")
                        states[job_id] = status.get("state")
                        offsets[job_id] = int(status.get("offset", offsets[job_id]))
                        if changed:
                            self.store.append(run_id, {"type": "job_reconciled", **status}, turn_id)
                        if status.get("terminal") or status.get("needs_finalization"):
                            offsets.pop(job_id, None)
                    if offsets:
                        await asyncio.sleep(interval)
            if not offsets and not self.store.is_cancelled(run_id) and not self.stopping:
                await self._resume_recovered(run_id, owner)
        except asyncio.CancelledError:
            raise

    async def _monitor_cancel(self, run_id: str, owner: str, chat_id: str) -> None:
        interval = max(1, int(os.getenv("RECOVERY_POLL_SECONDS", "5")))
        budget = max(1, int(os.getenv("CANCEL_TIMEOUT_SECONDS", "300")))
        deadline = asyncio.get_running_loop().time() + budget
        try:
            with self.sessions.hold(chat_id):
                while not self.stopping:
                    run = self.store.get(run_id, owner)
                    refs = self.store.jobs(run_id)
                    all_terminal = True
                    for ref in refs:
                        try:
                            status = await self._job_operation(
                                run, "job_status", ref["job_id"]
                            )
                            if not status.get("terminal"):
                                status = await self._job_operation(
                                    run, "job_cancel", ref["job_id"]
                                )
                            self.store.append(run_id, {
                                "type": "job_reconciled", **status,
                            }, ref.get("turn_id"))
                            all_terminal = all_terminal and bool(status.get("terminal"))
                        except Exception as exc:
                            all_terminal = False
                            self.store.append(run_id, {
                                "type": "job_reconciled", "job_id": ref["job_id"],
                                "job_type": ref.get("job_type"), "state": "unknown",
                                "terminal": False, "observation_error": True,
                                "error": str(exc),
                            }, ref.get("turn_id"))
                    if all_terminal:
                        self.store.finish_cancel(run_id)
                        return
                    if asyncio.get_running_loop().time() >= deadline:
                        self.store.finish_cancel_incomplete(
                            run_id,
                            "Cancellation could not be confirmed before the timeout; "
                            "durable job references were preserved for later reconciliation.",
                        )
                        return
                    await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise

    async def _resume_recovered(self, run_id: str, owner: str) -> None:
        if self.store.is_cancelled(run_id) or self.stopping:
            return
        run = self.store.get(run_id, owner)
        if self.sessions.is_deleting(run["chat_id"]):
            self.store.set_state(run_id, "paused")
            return
        previous_turn = self.store.latest_turn(run_id)
        if previous_turn is None:
            self.store.set_state(run_id, "paused")
            self.store.append(run_id, {
                "type": "recovery_blocked",
                "state": "paused",
                "content": "Legacy Run has no Turn/checkpoint association; it was not replayed automatically.",
            })
            return
        checkpoint_state = await self._checkpoint_state(previous_turn["agent_run_id"])
        self.store.set_turn_state(
            previous_turn["id"], previous_turn["state"], checkpoint_state
        )
        if checkpoint_state != "complete":
            self.store.set_state(run_id, "paused")
            self.store.append(run_id, {
                "type": "recovery_blocked",
                "state": "paused",
                "checkpoint_state": checkpoint_state,
                "content": (
                    "The prior Agent turn has no settled checkpoint or has an unresolved "
                    "tool effect. Runtime did not replay it automatically."
                ),
            }, previous_turn["id"])
            return
        self.store.mark_turn_consumed(
            run_id, previous_turn["id"], previous_turn["input_end"]
        )
        self.store.append(run_id, {
            "type": "recovery_resume",
            "content": "Recovered jobs reached terminal state; the Agent will continue from persisted observations.",
        }, previous_turn["id"])
        observations = self.store.latest_job_observations(run_id)
        self.store.set_state(run_id, "paused")
        self.store.add_input(
            run_id, owner, "system",
            "Runtime 恢复后核对的后台作业事实：\n" + json.dumps(observations, ensure_ascii=False),
        )
        try:
            session_store = self.sessions.acquire(owner, run["chat_id"])
            self.sessions.release(run["chat_id"])
        except AssetError:
            self.store.set_state(run_id, "paused")
            return
        self.start(run_id, session_store)
