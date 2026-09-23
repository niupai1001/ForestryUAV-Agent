"""Background Run coordinator independent of HTTP connection lifetime."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os

from pydantic_ai_harness.step_persistence import SqliteStepStore

from ..agent import stream_agent
from ..run_store import RunStore
from ..storage import AssetError
from ..capabilities.runtime import RuntimeTools
from .state import TERMINAL


from .recovery import RecoveryCoordinatorMixin


class RunCoordinator(RecoveryCoordinatorMixin):
    def __init__(self, sessions, workspaces, memory=None):
        self.sessions = sessions
        self.workspaces = workspaces
        self.memory = memory
        self.store = RunStore(sessions.root)
        self.step_store = SqliteStepStore(
            database=self.store.root / "agent_steps.sqlite3"
        )
        self.tasks: dict[str, asyncio.Task] = {}
        self.monitor_tasks: dict[str, asyncio.Task] = {}
        self.stopping = False

    def _job_tools(self, run: dict, session_store=None) -> RuntimeTools:
        if session_store is None:
            session_store = self.sessions.acquire(run["owner"], run["chat_id"])
            self.sessions.release(run["chat_id"])
        latest_user = next((
            str(item.get("content") or "") for item in reversed(run["messages"])
            if item.get("role") == "user"
        ), "")
        return RuntimeTools(
            session_store, run["owner"], [], self.workspaces, latest_user,
            self.memory,
        )

    async def _job_operation(
        self, run: dict, operation: str, job_id: str, session_store=None,
        offset: int = 0,
    ) -> dict:
        tools = self._job_tools(run, session_store)
        arguments = {"job_id": job_id}
        if operation == "job_status":
            arguments.update(offset=offset, wait_seconds=0)
        result = await asyncio.to_thread(tools.execute, operation, arguments)
        if not result.get("ok"):
            legacy_code_job = (
                job_id.startswith("job_") and len(job_id) == 36
                and all(character in "0123456789abcdef" for character in job_id[4:])
            )
            if legacy_code_job:
                bridge_operation = "status" if operation == "job_status" else "cancel"
                data = await asyncio.to_thread(
                    self.workspaces.bridge.job, bridge_operation,
                    {"job_id": job_id, "offset": offset},
                )
                return data | {"job_type": "code"}
            raise AssetError(str(result.get("error") or "Job operation failed"))
        return result.get("data") or {}

    def create(self, session_store, owner: str, messages: list[dict], asset_ids: list[str], use_tools: bool = True) -> dict:
        for message in messages:
            input_id = str(message.get("input_id") or "")
            if input_id:
                existing = self.store.run_for_input(
                    owner, session_store.chat_id, input_id
                )
                if existing:
                    return self.public(existing)
        run = self.store.create(owner, session_store.chat_id, messages, asset_ids, use_tools)
        latest_user = next(
            (item for item in reversed(messages) if item.get("role") == "user"),
            None,
        )
        if latest_user:
            turn = self.store.latest_turn(run["id"])
            input_ids = json.loads(turn["input_ids_json"]) if turn else []
            self.store.append(run["id"], {
                "type": "user_message",
                "content": latest_user.get("content", ""),
                "input_id": str(latest_user.get("input_id") or (input_ids[0] if input_ids else "")),
            })
        self.start(run["id"], session_store)
        return self.public(run)

    def start(self, run_id: str, session_store=None) -> None:
        if run_id in self.tasks and not self.tasks[run_id].done():
            return
        run = self.store.get(run_id)
        if session_store is None:
            session_store = self.sessions.acquire(run["owner"], run["chat_id"])
            self.sessions.release(run["chat_id"])
        self.tasks[run_id] = asyncio.create_task(self._drive(run_id, session_store))

    async def _drive(self, run_id: str, session_store) -> None:
        run = self.store.get(run_id)
        try:
            turn = self.store.claim_turn(run_id)
        except AssetError as exc:
            if self.store.is_cancelled(run_id):
                return
            self.store.set_state(run_id, "failed", final={"error": str(exc)})
            self.store.append(run_id, {
                "type": "error",
                "content": f"Run coordinator could not claim a queued Turn: {exc}",
                "state": "failed",
            })
            self.store.append(run_id, {
                "type": "done", "state": "failed", "artifacts": []
            })
            return
        turn_id = turn["id"]
        self.store.set_state(run_id, "running")
        final_state = "completed"
        final_event = None
        checkpoint_state = "unknown"
        try:
            input_rows = self.store.turn_inputs(turn_id)
            messages = [
                {"role": item["role"], "content": item["content"]}
                for item in input_rows
            ]
            if not messages:  # legacy Turn created before durable inputs existed
                messages = list(run["messages"][turn["input_start"]:turn["input_end"]])
            resume_agent_run_id = None
            prior_turns = [
                item for item in self.store.turns(run_id)
                if item["ordinal"] < turn["ordinal"]
            ]
            if prior_turns:
                previous_turn = prior_turns[-1]
                previous_checkpoint = await self._checkpoint_state(
                    previous_turn["agent_run_id"]
                )
                self.store.set_turn_state(
                    previous_turn["id"], previous_turn["state"], previous_checkpoint
                )
                if previous_checkpoint == "complete":
                    self.store.mark_turn_consumed(
                        run_id, previous_turn["id"], previous_turn["input_end"]
                    )
                    resume_agent_run_id = previous_turn["agent_run_id"]
                elif previous_checkpoint == "uncertain":
                    self.store.mark_turn_consumed(
                        run_id, previous_turn["id"], previous_turn["input_end"]
                    )
                    messages.insert(0, {
                        "role": "system",
                        "content": (
                            "The preceding Agent turn stopped with an unresolved tool effect. "
                            "Do not replay that action. Inspect persisted Action/Job facts or "
                            "ask the user before attempting a new side effect."
                        ),
                    })
            self.store.bind_turn_resume(turn_id, resume_agent_run_id)
            with session_store.operation():
                async for event in stream_agent(
                    session_store, run["owner"], run["asset_ids"], messages,
                    use_tools=run["use_tools"], workspace_registry=self.workspaces,
                    cancelled=lambda: self.store.is_cancelled(run_id) or self.sessions.is_deleting(run["chat_id"]),
                    agent_run_id=turn["agent_run_id"],
                    chat_id=run["chat_id"],
                    persistence_database=self.store.root / "agent_steps.sqlite3",
                    runtime_run_id=run_id,
                    turn_id=turn_id,
                    resume_agent_run_id=resume_agent_run_id,
                    pause_requested=lambda: self.store.is_pause_requested(run_id),
                    begin_action=lambda action_id, name, arguments: self.store.begin_action(
                        run_id, turn_id, action_id, name, arguments
                    ),
                    mark_attempt_started=self.store.mark_attempt_started,
                    finish_action=lambda action_id, name, result, duration, attempt_id: self.store.finish_action(
                        run_id, turn_id, action_id, name, result, duration, attempt_id
                    ),
                    publish=lambda event: self.store.append(run_id, event, turn_id),
                    begin_step=lambda number, context, estimated: self.store.begin_step(
                        run_id, turn_id, number, context, estimated
                    ),
                    finish_step=self.store.finish_step,
                    run_facts=lambda: self.store.context_facts(run_id),
                    project_context=(
                        (lambda: self.memory.context(
                            run["owner"], run["chat_id"]
                        )) if self.memory else None
                    ),
                    memory_manager=self.memory,
                ):
                    if event.get("type") == "done" and event.get("artifacts"):
                        sealed = []
                        for artifact in event.get("artifacts") or []:
                            try:
                                sealed.append(session_store.seal(
                                    artifact["id"], run["owner"],
                                    {"status": "reported", "turn_id": turn_id},
                                ))
                            except Exception as exc:
                                sealed.append(artifact | {
                                    "verification": {
                                        "status": "seal_failed", "error": str(exc)
                                    }
                                })
                        event = dict(event, artifacts=sealed)
                    self.store.append(run_id, event, turn_id)
                    if event.get("type") == "done":
                        final_event = event
                        final_state = event.get("state") or final_state
            checkpoint_state = await self._checkpoint_state(turn["agent_run_id"])
            if final_state == "completed" or checkpoint_state == "complete":
                self.store.mark_turn_consumed(run_id, turn_id, turn["input_end"])
        except asyncio.CancelledError:
            final_state = "paused" if self.stopping and not self.store.is_cancelled(run_id) else "canceled"
            message = (
                "Runtime stopped during this Run. Completed side effects and job references are preserved; continue the Run after restart."
                if final_state == "paused" else
                "Run canceled; no further actions will be scheduled."
            )
            final_event = {"type": "done", "state": final_state, "artifacts": []}
            self.store.append(run_id, {"type": "error", "content": message, "state": final_state}, turn_id)
            self.store.append(run_id, final_event, turn_id)
        except Exception as exc:
            final_state = "failed"
            self.store.append(run_id, {"type": "error", "content": f"Run coordinator failed: {type(exc).__name__}: {exc}", "state": "failed"}, turn_id)
            final_event = {"type": "done", "state": "failed", "artifacts": []}
            self.store.append(run_id, final_event, turn_id)
        finally:
            if checkpoint_state == "unknown":
                checkpoint_state = await self._checkpoint_state(
                    turn["agent_run_id"]
                )
                if checkpoint_state == "complete":
                    self.store.mark_turn_consumed(run_id, turn_id, turn["input_end"])
            if self.store.is_cancelled(run_id):
                final_state = "canceling"
            elif self.sessions.is_deleting(run["chat_id"]):
                final_state = "paused"
            active = []
            if final_state in {"completed", "paused"}:
                for ref in self.store.jobs(run_id):
                    try:
                        status = await self._job_operation(
                            run, "job_status", ref["job_id"], session_store
                        )
                    except Exception:
                        active.append({
                            "job_id": ref["job_id"], "job_type": ref.get("job_type"),
                            "state": "unknown", "terminal": False,
                        })
                        continue
                    self.store.append(run_id, {"type": "job_reconciled", **status}, turn_id)
                    if not status.get("terminal") and not status.get("needs_finalization"):
                        active.append(status)
                if active:
                    final_state = "waiting"
            queued_turn = (
                self.store.has_queued_turn(run_id)
                and final_state != "canceled"
                and not self.stopping
                and not self.sessions.is_deleting(run["chat_id"])
            )
            self.store.set_state(
                run_id, "running" if queued_turn else final_state,
                None if queued_turn else final_event,
            )
            self.store.set_turn_state(turn_id, final_state, checkpoint_state)
            if queued_turn:
                self.tasks[run_id] = asyncio.create_task(
                    self._drive(run_id, session_store)
                )
            elif active and not self.stopping:
                self.monitor_tasks[run_id] = asyncio.create_task(
                    self._monitor_recovered(
                        run_id, run["owner"], run["chat_id"], active, turn_id
                    )
                )

    async def _checkpoint_state(self, agent_run_id: str) -> str:
        try:
            unresolved = await self.step_store.list_unresolved_tool_effects(
                run_id=agent_run_id
            )
            if unresolved:
                return "uncertain"
            snapshot = await self.step_store.latest_snapshot(
                run_id=agent_run_id, include_interrupted=True
            )
        except Exception:
            return "unknown"
        return snapshot.state if snapshot is not None else "missing"






    def get(self, run_id: str, owner: str) -> dict:
        return self.public(self.store.get(run_id, owner))

    def events(self, run_id: str, owner: str, after: int = 0) -> list[dict]:
        return self.store.events_after(run_id, owner, after)

    def event_page(
        self, run_id: str, owner: str, after: int = 0, limit: int = 500
    ) -> dict:
        return self.store.event_page(run_id, owner, after, limit)

    def turns(self, run_id: str, owner: str) -> list[dict]:
        return self.store.turns(run_id, owner)

    def continue_run(self, run_id: str, owner: str, content: str, session_store,
                     asset_ids: list[str] | None = None, input_id: str | None = None) -> dict:
        current = self.store.get(run_id, owner)
        if input_id:
            existing = self.store.run_for_input(
                owner, current["chat_id"], input_id
            )
            if existing:
                return self.public(existing)
        if current["state"] in {"queued", "running"}:
            run = self.store.queue_input(
                run_id, owner, "user", content, asset_ids, input_id
            )
            queued_turn = self.store.latest_turn(run_id)
            queued_input_ids = (
                json.loads(queued_turn["input_ids_json"]) if queued_turn else []
            )
            self.store.append(run_id, {
                "type": "user_message", "content": content, "queued": True,
                "input_id": str(
                    input_id or (queued_input_ids[0] if queued_input_ids else "")
                ),
            }, queued_turn["id"] if queued_turn else None)
            return self.public(run)
        monitor = self.monitor_tasks.get(run_id)
        if monitor and not monitor.done():
            monitor.cancel()
        run = self.store.add_input(
            run_id, owner, "user", content, asset_ids, input_id
        )
        turn = self.store.latest_turn(run_id)
        turn_input_ids = json.loads(turn["input_ids_json"]) if turn else []
        self.store.append(run_id, {
            "type": "user_message", "content": content,
            "input_id": str(input_id or (turn_input_ids[0] if turn_input_ids else "")),
        })
        self.start(run_id, session_store)
        return self.public(run)

    async def pause(self, run_id: str, owner: str) -> dict:
        return self.public(self.store.request_pause(run_id, owner))

    async def cancel(self, run_id: str, owner: str) -> dict:
        run = self.store.request_cancel(run_id, owner)
        monitor = self.monitor_tasks.get(run_id)
        if monitor and not monitor.done():
            monitor.cancel()
        all_terminal = True
        for ref in self.store.jobs(run_id):
            try:
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
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if all_terminal:
            self.store.finish_cancel(run_id)
        elif not self.stopping:
            self.monitor_tasks[run_id] = asyncio.create_task(
                self._monitor_cancel(run_id, owner, run["chat_id"])
            )
        return self.public(self.store.get(run_id, owner))

    @staticmethod
    def public(run: dict) -> dict:
        return {key: value for key, value in run.items() if key not in {"owner", "messages", "asset_ids", "use_tools", "cancel_requested", "pause_requested", "agent_input_count"}}
