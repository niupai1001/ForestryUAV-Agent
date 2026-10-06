from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent import Deps, Engine
from .config import Config
from .contracts import (
    Acceptance,
    Amendment,
    ApprovalResolution,
    Control,
    Failure,
    MemoryForget,
    MemorySave,
    Message,
    RegisterResource,
    SettingsChange,
    TaskCreate,
)
from .delivery import Delivery
from .doctor import doctor
from .models import model_identity
from .resources import Resources
from .store import Store, encode, now, uid


def create_app(config: Config, *, enable_agent: bool = True):
    store = Store(config.workspace_root / ".forestry" / "state.sqlite")
    resources = Resources(store, config.workspace_root)
    engine = Engine(config, store, resources)
    delivery = Delivery(store, resources)
    key_file = config.workspace_root / ".forestry" / "local.key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    key = key_file.read_text(encoding="utf-8").strip()

    @asynccontextmanager
    async def lifespan(app):
        engine.reconcile_startup()
        await asyncio.to_thread(delivery.recover)
        if enable_agent:
            engine.jobs.start()
        yield
        await engine.shutdown()
        await engine.jobs.close()

    app = FastAPI(title="ForestryUAV-Agent", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.resources, app.state.engine = store, resources, engine
    app.state.key = key
    app.state.delivery = delivery
    initial = {"default_model": config.default_model}

    @app.exception_handler(Failure)
    async def failure_handler(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message,
            "retryable": error.retryable, "correlation_id": uid(), "details_ref": None}}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def schema_error(request, error):
        return JSONResponse({"error": {"code": "SCHEMA_INVALID", "message": "请求不符合接口 Schema",
            "retryable": False, "details": [{"location": list(e["loc"]), "type": e["type"]}
            for e in error.errors()]}}, status_code=400)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"error": {"code": "PERMISSION_DENIED", "message": "拒绝非本地主机名"}}, 403)
        public = request.url.path in {"/", "/health", "/session", "/favicon.ico"} or request.url.path.startswith("/ui/")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": {"code": "PERMISSION_DENIED", "message": "拒绝跨来源访问"}}, 403)
        if not public:
            supplied = request.cookies.get("forestry_session") or request.headers.get("authorization", "").removeprefix("Bearer ")
            if not supplied or not hmac.compare_digest(supplied, key):
                return JSONResponse({"error": {"code": "PERMISSION_DENIED", "message": "需要本地会话认证"}}, 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not origin:
            authorization = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(authorization, key):
                return JSONResponse({"error": {"code": "PERMISSION_DENIED", "message": "浏览器修改请求需要同源 Origin"}}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/session")
    def session():
        response = JSONResponse({"authenticated": True})
        response.set_cookie("forestry_session", key, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/health")
    def health():
        # No state paths or private source metadata in unauthenticated health output.
        with store.transaction() as db:
            db.execute("SELECT 1")
        return {"application": "ready", "database": "ready", "domain": "not_implemented"}

    @app.get("/models/profiles")
    async def profiles():
        return await doctor(config)

    @app.get("/settings")
    def settings():
        return {**store.settings(initial), "models": {k: v.model_dump() for k, v in config.models.items()}}

    @app.put("/settings")
    def update_settings(change: SettingsChange):
        payload = change.model_dump()
        if change.default_model not in config.models:
            raise Failure("PRECONDITION_MISSING", "模型配置不存在", 422)
        with store.transaction() as db:
            request_payload = {"kind": "settings", **payload}
            replay = store.replay(db, change.request_id, request_payload)
            if replay is not None:
                return replay
            row = db.execute("SELECT * FROM settings WHERE id=1").fetchone()
            if row is None or row["version"] != change.expected_version:
                raise Failure("VERSION_CONFLICT", "设置版本已变化", 409)
            response = {"version": row["version"] + 1, "default_model": change.default_model}
            db.execute("UPDATE settings SET version=?,payload=? WHERE id=1",
                       (response["version"], encode({"default_model": change.default_model})))
            store.user_event(db, "settings_change", payload)
            return store.receipt(db, change.request_id, request_payload, response)

    @app.get("/resources")
    def list_resources():
        return resources.all()

    @app.get("/memory")
    def memory(offset: int = Query(0, ge=0)):
        return engine.memory.list(offset)

    @app.post("/memory")
    def save_memory(payload: MemorySave):
        return engine.memory.save(payload.model_dump())

    @app.delete("/memory/{memory_id}")
    def forget_memory(memory_id: str, payload: MemoryForget):
        return engine.memory.forget(memory_id, payload.model_dump())

    @app.post("/resources/register")
    def register(payload: RegisterResource):
        try:
            return resources.register(payload.model_dump())
        except FileNotFoundError:
            raise Failure("PRECONDITION_MISSING", "选择的本地路径不存在", 422)

    @app.get("/resources/{resource_id}/children")
    def children(resource_id: str, relative_path: str = "", cursor: int = Query(0, ge=0),
                 limit: int = Query(100, ge=1, le=200)):
        return resources.list(resource_id, relative_path, cursor, limit)

    @app.get("/tasks")
    def tasks():
        return store.tasks()

    @app.post("/tasks", status_code=202)
    async def create(payload: TaskCreate):
        # A retry returns its existing task without probing or rebinding its weights.
        with store.transaction() as db:
            prior = store.replay(db, payload.request_id, {"kind": "task.create", **payload.model_dump()})
        if prior is not None:
            return prior
        current = store.settings(initial)
        profile = payload.model_profile or current["default_model"]
        if profile not in config.models:
            raise Failure("PRECONDITION_MISSING", "模型配置不存在", 422)
        # Until domain methods exist, a hard method request is never silently replaced.
        if payload.requested_method_id is not None:
            raise Failure("PRECONDITION_MISSING", "当前阶段尚未实现该指定方法", 422)
        identity = await model_identity(config, config.models[profile]) if enable_agent else {"backend": "test_fixture"}
        state = store.create_task(payload.model_dump(), {"model_profile": profile,
            "harness_mode": payload.harness_mode, "role_catalog": engine.roles.catalog,
            "context_classification": payload.context_classification,
            "search": config.search_configuration(),
            "settings_version": current["version"], "model": config.models[profile].model_dump(),
            "model_identity": identity, "model_base_url": config.model_base_url,
            "generation": {"temperature": 0.2, "think": False, "max_output_retries": config.max_output_retries,
                           "max_model_requests_per_turn": config.max_model_requests_per_turn,
                           "max_assignments_per_task": config.max_assignments_per_task,
                           "max_unchanged_decisions": config.max_unchanged_decisions,
                           "max_model_request_seconds": config.max_model_request_seconds}})
        actual = store.task(state["task_id"])
        with store.transaction() as db:
            initial_turn_exists = db.execute("SELECT 1 FROM turns WHERE task_id=?", (state["task_id"],)).fetchone()
        if actual["status"] == "created" and enable_agent and not initial_turn_exists and state["task_id"] not in engine.queue:
            # Replays see the same durable task and do not start another live run.
            engine.submit(state["task_id"], payload.objective)
        return state

    @app.get("/tasks/{task_id}")
    def task(task_id: str):
        return store.task(task_id)

    @app.get("/tasks/{task_id}/acceptance-candidate")
    def acceptance_candidate(task_id: str):
        return delivery.candidate(task_id)

    @app.post("/tasks/{task_id}/acceptance")
    def acceptance(task_id: str, payload: Acceptance):
        return delivery.accept(task_id, payload.model_dump())

    @app.post("/tasks/{task_id}/messages")
    async def message(task_id: str, payload: Message):
        def mutate(state, db):
            if state["spec_revision"] != payload.expected_spec_revision:
                raise Failure("VERSION_CONFLICT", "规范修订已变化", 409)
            event_id = store.user_event(db, "user_message", payload.model_dump())
            state["messages"].append({"id": uid(), "role": "user", "text": payload.text,
                                      "user_event_id": event_id, "at": now()})
            if state["cancellation_requested"]:
                raise Failure("VERSION_CONFLICT", "已请求取消的任务不能接收新执行回合", 409)
            store.enqueue_turn(db, state, payload.text, payload.request_id, event_id)
            if not state["pause_requested"] and state["status"] != "paused":
                state.update(status="active" if task_id in engine.queue and not engine.queue[task_id].done() else "created", blockers=[])
        result = store.change(task_id, "user.message", mutate, payload.model_dump())
        if enable_agent:
            engine.start_queued(task_id)
        return result

    @app.post("/tasks/{task_id}/amendments")
    def amendment(task_id: str, payload: Amendment):
        def mutate(state, db):
            if state["spec_revision"] != payload.expected_spec_revision:
                raise Failure("VERSION_CONFLICT", "规范修订已变化", 409)
            if payload.requested_method_id is not None:
                raise Failure("PRECONDITION_MISSING", "指定领域方法尚未实现", 422)
            for resource_id in payload.input_refs:
                resource = db.execute("SELECT payload FROM resources WHERE id=?", (resource_id,)).fetchone()
                if not resource:
                    raise Failure("PERMISSION_DENIED", "新输入资源尚未授权", 403)
                if state["config_snapshot"].get("context_classification") == "public" and json.loads(resource["payload"])["classification"] != "public":
                    raise Failure("PERMISSION_DENIED", "公开任务不能新增私密输入", 403)
            store.user_event(db, "task_amendment", payload.model_dump())
            state["spec_revision"] += 1
            db.execute("UPDATE turns SET status='cancelled' WHERE task_id=? AND status='queued'", (task_id,))
            state.update(objective=payload.objective, input_refs=payload.input_refs,
                         requested_method_id=payload.requested_method_id, status="paused", pause_requested=True,
                         acceptance=None, blockers=[{"code": "AMENDED", "message": "规范已修改，请对账后恢复"}])
            db.execute("INSERT INTO specs VALUES(?,?,?)", (task_id, state["spec_revision"], encode(payload.model_dump())))
        result = store.change(task_id, "task.amended", mutate, payload.model_dump())
        if task_id in engine.tokens:
            engine.tokens[task_id].cancel()
        return result

    @app.post("/tasks/{task_id}/pause")
    def pause(task_id: str, payload: Control):
        def mutate(state, db):
            store.user_event(db, "task_pause", payload.model_dump())
            state["pause_requested"] = True
            if task_id not in engine.queue or engine.queue[task_id].done():
                state["status"] = "paused"
        result = store.change(task_id, "task.pause_requested", mutate, payload.model_dump(), payload.expected_state_version)
        if task_id in engine.tokens:
            engine.tokens[task_id].cancel()
        return result

    @app.post("/tasks/{task_id}/cancel")
    def cancel(task_id: str, payload: Control):
        def mutate(state, db):
            store.user_event(db, "task_cancel", payload.model_dump())
            state["cancellation_requested"] = True
            db.execute("UPDATE turns SET status='cancelled' WHERE task_id=? AND status='queued'", (task_id,))
            engine.roles.cancel_waiting(task_id, db)
            live = task_id in engine.queue and not engine.queue[task_id].done()
            if not live:
                if engine.cancel_status(db, task_id) != "cancelled":
                    state.update(status="paused", blockers=[{"code": "RECONCILIATION_REQUIRED",
                                                              "message": "未决副作用须对账后才能确认取消"}])
                else:
                    state["status"] = "cancelled"
        result = store.change(task_id, "task.cancel_requested", mutate, payload.model_dump(), payload.expected_state_version)
        if task_id in engine.tokens:
            engine.tokens[task_id].cancel()
        return result

    @app.post("/tasks/{task_id}/resume")
    async def resume(task_id: str, payload: Control):
        def mutate(state, db):
            if state["cancellation_requested"]:
                raise Failure("VERSION_CONFLICT", "已请求取消的任务不能恢复", 409)
            if db.execute("SELECT 1 FROM actions WHERE task_id=? AND status='pending'", (task_id,)).fetchone():
                raise Failure("RECONCILIATION_REQUIRED", "未决副作用须先对账", 409)
            if task_id in engine.queue and not engine.queue[task_id].done():
                raise Failure("VERSION_CONFLICT", "执行端尚未确认停止", 409)
            turn = db.execute("SELECT * FROM turns WHERE task_id=? AND revision=? AND status IN ('paused','running') AND id NOT IN (SELECT json_extract(payload,'$.run_id') FROM assignments) ORDER BY created_at DESC LIMIT 1",
                (task_id, state["spec_revision"])).fetchone()
            progress = engine.progress.latest(db, Deps(engine, task_id, turn["id"], state["spec_revision"])) if turn else None
            if progress and progress["stalled"] >= progress["limit_value"]:
                corrected = db.execute("SELECT t.id FROM turns t JOIN turn_requests r ON r.turn_id=t.id WHERE t.task_id=? AND t.revision=? AND t.status='queued' ORDER BY t.created_at,t.rowid LIMIT 1",
                    (task_id, state["spec_revision"])).fetchone()
                if not corrected:
                    raise Failure("NO_PROGRESS", "本回合无进展预算已用尽。请先补充纠正说明再恢复，或修改任务规范；直接恢复不会重置计数。", 422)
                db.execute("UPDATE turns SET status='stopped' WHERE id=?", (turn["id"],))
                store.event(db, task_id, "run.superseded", {"stopped_turn_id": turn["id"],
                    "continuation_turn_id": corrected["id"], "reason": "user_input_after_no_progress"})
            state.update(status="created", pause_requested=False, blockers=[])
            store.user_event(db, "task_resume", payload.model_dump())
        result = store.change(task_id, "task.resume_requested", mutate, payload.model_dump(), payload.expected_state_version)
        if enable_agent and (task_id not in engine.queue or engine.queue[task_id].done()):
            with store.transaction() as db:
                turn = db.execute("SELECT * FROM turns WHERE task_id=? AND revision=? AND status IN ('paused','running') AND id NOT IN (SELECT json_extract(payload,'$.run_id') FROM assignments) ORDER BY created_at DESC LIMIT 1",
                                  (task_id, result["spec_revision"])).fetchone()
            if turn:
                engine.submit(task_id, turn["prompt"], turn["id"])
            elif not engine.start_queued(task_id):
                engine.submit(task_id, result["objective"])
        return result

    @app.get("/tasks/{task_id}/events")
    async def events(request: Request, task_id: str, cursor: int = Query(0, ge=0), format: str = "sse"):
        if format == "json":
            return store.events(task_id, cursor)
        store.task(task_id)
        try:
            after = max(cursor, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise Failure("SCHEMA_INVALID", "事件游标必须为整数")
        async def stream():
            nonlocal after
            while not await request.is_disconnected():
                rows = store.events(task_id, after)
                for row in rows:
                    after = row["sequence"]
                    yield f"id: {after}\ndata: {encode(row)}\n\n"
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/tasks/{task_id}/artifacts")
    def artifacts(task_id: str):
        state = store.task(task_id)
        return [{**{k: v for k, v in resources.artifact(i).items() if k != "locator"},
            "preview_url": f"/artifacts/{i}/content", "download_url": f"/artifacts/{i}/content?download=true"}
            for i in state["artifact_ids"]]

    @app.get("/roles")
    def role_catalog():
        return engine.roles.catalog

    @app.get("/tasks/{task_id}/approvals")
    def approvals(task_id: str):
        return engine.network.list(task_id)

    @app.post("/tasks/{task_id}/approvals/{approval_id}")
    async def resolve_approval(task_id: str, approval_id: str, payload: ApprovalResolution):
        result = engine.network.resolve(task_id, approval_id, payload.model_dump())
        if enable_agent:
            engine.start_queued(task_id)
        return result

    @app.get("/roles/schemas")
    def role_schemas():
        from .roles import AssignmentRequest, AssignmentResult, RoleCatalog
        return {"AssignmentRequest": AssignmentRequest.model_json_schema(),
                "AssignmentResult": AssignmentResult.model_json_schema(), "RoleCatalog": RoleCatalog.model_json_schema()}

    @app.get("/tasks/{task_id}/assignments")
    def assignments(task_id: str):
        return engine.roles.list(task_id)

    @app.get("/tasks/{task_id}/assignments/{assignment_id}")
    def assignment(task_id: str, assignment_id: str):
        return engine.roles.read(assignment_id, task_id)

    @app.get("/tasks/{task_id}/jobs/{job_id}")
    def job(task_id: str, job_id: str):
        return engine.jobs.public(engine.jobs.read(job_id, task_id))

    @app.get("/tasks/{task_id}/jobs/{job_id}/logs")
    async def job_logs(task_id: str, job_id: str):
        return await asyncio.to_thread(engine.jobs.logs, job_id, task_id)

    @app.get("/artifacts/{artifact_id}/content")
    def content(artifact_id: str, download: bool = False):
        artifact = resources.artifact(artifact_id)
        response = FileResponse(artifact["locator"], media_type=artifact["media_type"],
                                filename=artifact["name"] if download else None)
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        return response

    frontend = Path(__file__).parent / "static"
    if frontend.exists():
        app.mount("/ui", StaticFiles(directory=frontend), name="ui")

    @app.get("/")
    def index():
        if (frontend / "index.html").exists():
            return FileResponse(frontend / "index.html")
        return Response("UI resources not built. Run the frontend build and copy dist into src/forestry_agent/static.",
                        status_code=503, media_type="text/plain")

    return app
