"""Permission-scoped native plans; proposed progress is never task acceptance."""
from __future__ import annotations

from pydantic_ai_harness.planning import PlanItem, Planning, SqlitePlanStore

from .contracts import Failure
from .store import encode


class CompactionSafePlanning(Planning):
    """Run the native ephemeral injector after native before-hook compaction.

    Current harness compactors rebuild requests from durable ctx.messages,
    dropping wrap-stage additions. Native replacement of the final request
    remains request-only here; no history, rendering or plan CRUD is copied.
    """

    async def wrap_model_request(self, ctx, *, request_context, handler):
        return await handler(request_context)

    async def before_model_request(self, ctx, request_context):
        async def prepared(value):
            return value
        return await super().wrap_model_request(ctx, request_context=request_context, handler=prepared)


class ScopedPlanStore(SqlitePlanStore):
    def __init__(self, engine, task_id, revision, turn_id, assignment_id=None):
        self.engine, self.task_id, self.revision = engine, task_id, revision
        self.turn_id, self.assignment_id = turn_id, assignment_id
        super().__init__(str(engine.store.path), table="native_plan_items",
            session=encode([task_id, revision, turn_id, assignment_id]))

    def _set_items_sync(self, items: list[PlanItem]):
        if (len(items) > 16 or len(encode([i.model_dump(mode="json") for i in items]).encode()) > 8192 or
            any(not 1 <= len(i.id) <= 64 or not 1 <= len(i.content) <= 500 or len(i.active_form) > 120 for i in items) or
            sum(i.status == "in_progress" for i in items) > 1):
            raise Failure("SCHEMA_INVALID", "计划最多16步/8192 UTF-8字节，每步正文1–500字符，ID最多64字符，最多一步执行中")
        # Local service control writes use the same lock. Keep it across the
        # native whole-plan commit, without holding a second SQLite transaction.
        with self.engine.store.lock:
            with self.engine.store.transaction() as db:
                state = self.engine.store.read_task(db, self.task_id)
                if state["spec_revision"] != self.revision or state["pause_requested"] or state["cancellation_requested"]:
                    raise Failure("EXECUTION_STOPPED", "控制或任务修订阻止计划更新", 409)
                self.engine.store.authorize_capability(db, state, "task.plan")
                if self.assignment_id:
                    role = self.engine.roles.read(self.assignment_id, self.task_id, db)
                    if role["run_id"] != self.turn_id or "task.plan" not in role["effective_capabilities"]:
                        raise Failure("PERMISSION_DENIED", "计划不属于当前委派范围", 403)
            super()._set_items_sync(items)


def plan_schema(tool):
    """Bound the framework's real PlanItem input schema, without a parallel DTO."""
    schema = {**tool.parameters_json_schema}
    if tool.name == "write_plan":
        schema["properties"] = {**schema["properties"], "items": {**schema["properties"]["items"], "maxItems": 16}}
        definitions = dict(schema.get("$defs", {}))
        item = dict(definitions["PlanItem"])
        item["additionalProperties"] = False
        item["properties"] = {**item["properties"],
            "id": {**item["properties"]["id"], "minLength": 1, "maxLength": 64},
            "content": {**item["properties"]["content"], "minLength": 1, "maxLength": 500},
            "active_form": {**item["properties"]["active_form"], "maxLength": 120}}
        definitions["PlanItem"] = item
        schema["$defs"] = definitions
    return schema


async def visible_plan(engine, task_id):
    with engine.store.transaction() as db:
        state = engine.store.read_task(db, task_id)
        row = db.execute("SELECT id FROM turns WHERE task_id=? AND revision=? "
            "AND id NOT IN (SELECT json_extract(payload,'$.run_id') FROM assignments WHERE task_id=?) "
            "ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END,rowid DESC LIMIT 1",
            (task_id, state["spec_revision"], task_id,state.get('active_turn_id'))).fetchone()
    turn_id = row["id"] if row else None
    items = await ScopedPlanStore(engine, task_id, state["spec_revision"], turn_id).get_items() if turn_id else []
    return {"task_id": task_id, "spec_revision": state["spec_revision"], "turn_id": turn_id,
        "authority": "model_proposed", "acceptance": "not_assessed",
        "items": [item.model_dump(mode="json") for item in items]}
