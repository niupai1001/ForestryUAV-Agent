from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator

from .contracts import CAPABILITY_NAMES, Boundary, Failure
from .resources import inside, version
from .store import encode, now, uid

RoleID = Literal["coordinator", "environment", "data", "code_experiment", "quality"]
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}
CURRENT_ASSIGNMENT: ContextVar[str | None] = ContextVar("forestry_assignment", default=None)


class PermissionCeiling(Boundary):
    workspace_write: bool
    source_write: Literal[False] = False
    network: Literal["none", "public_scoped"] = "none"
    parent_task_mutation: Literal[False] = False


class RoleConfig(Boundary):
    role_id: RoleID
    objective_scope: str = Field(min_length=1, max_length=1000)
    model_profile: Literal["inherit_task"]
    allowed_capabilities: list[str] = Field(min_length=1, max_length=32)
    permission_ceiling: PermissionCeiling
    context_budget: int = Field(ge=8192, le=131072)
    return_schema: Literal["Answer", "AssignmentResult"]
    assignment_limit: int = Field(ge=0, le=32)
    max_requests: int = Field(ge=1, le=100)
    max_actions: int = Field(ge=1, le=1000)
    seconds: int = Field(ge=1, le=1800)
    prompt_template: str = Field(min_length=1, max_length=4000)

    @field_validator("allowed_capabilities")
    @classmethod
    def known_capabilities(cls, value):
        if len(set(value)) != len(value) or not set(value) <= set(CAPABILITY_NAMES.values()):
            raise ValueError("角色能力须为唯一的已注册能力")
        return value


class RoleCatalog(Boundary):
    schema_version: Literal[1]
    roles: list[RoleConfig] = Field(min_length=5, max_length=5)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class AssignmentRequest(Boundary):
    objective: str = Field(min_length=1, max_length=4000)
    input_refs: list[str] = Field(default_factory=list, max_length=20)
    artifact_refs: list[str] = Field(default_factory=list, max_length=20)
    resume_assignment_id: str | None = None


class CheckItem(Boundary):
    criterion: str = Field(min_length=1, max_length=1000)
    status: Literal["passed", "failed", "unknown"]
    observation: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class AssignmentResult(Boundary):
    status: Literal["succeeded", "partial", "failed", "cancelled"]
    kind: Literal["environment_manifest", "dataset_manifest", "experiment_result", "quality_assessment"]
    summary: str = Field(min_length=1, max_length=4000)
    artifact_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=40)
    job_ids: list[str] = Field(default_factory=list, max_length=20)
    checks: list[CheckItem] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=20)

    def __str__(self):
        return encode(self.model_dump())


KINDS = {"environment": "environment_manifest", "data": "dataset_manifest",
         "code_experiment": "experiment_result", "quality": "quality_assessment"}


class Roles:
    """Application assignment facts; native Harness owns each child model loop."""

    def __init__(self, engine):
        self.engine, self.store = engine, engine.store
        path = engine.config.roles_path or Path(__file__).with_name("roles.yaml")
        if path.stat().st_size > 128 * 1024:
            raise Failure("RESOURCE_EXHAUSTED", "角色目录超出128 KiB", 422)
        catalog = RoleCatalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if {r.role_id for r in catalog.roles} != set(RoleID.__args__):
            raise Failure("SCHEMA_INVALID", "角色目录必须包含五个唯一通用角色")
        if any(r.return_schema != ("Answer" if r.role_id == "coordinator" else "AssignmentResult") for r in catalog.roles):
            raise Failure("SCHEMA_INVALID", "协调者使用Answer，子角色使用AssignmentResult")
        self.catalog = catalog.model_dump(exclude={"sha256"})
        self.catalog["sha256"] = hashlib.sha256(encode(self.catalog).encode()).hexdigest()
        if catalog.sha256 and catalog.sha256 != self.catalog["sha256"]:
            raise Failure("VERIFICATION_FAILED", "角色目录指纹与定义不一致", 422)

    def configurations(self, state):
        catalog = state["config_snapshot"].get("role_catalog")
        if not catalog:
            return {}
        return {r["role_id"]: RoleConfig.model_validate(r) for r in catalog["roles"]}

    def read(self, assignment_id, task_id=None, db=None):
        if db is None:
            with self.store.transaction() as connection:
                return self.read(assignment_id, task_id, connection)
        row = db.execute("SELECT payload FROM assignments WHERE id=?", (assignment_id,)).fetchone()
        if not row:
            raise Failure("NOT_FOUND", "委派不存在", 404)
        value = json.loads(row["payload"])
        if task_id is not None and value["task_id"] != task_id:
            raise Failure("PERMISSION_DENIED", "委派不属于当前任务", 403)
        return value

    def save(self, db, value):
        db.execute("UPDATE assignments SET status=?,payload=? WHERE id=?",
                   (value["status"], encode(value), value["assignment_id"]))

    def list(self, task_id):
        self.store.task(task_id)
        with self.store.transaction() as db:
            return [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM assignments WHERE task_id=? ORDER BY rowid", (task_id,))]

    def prepare(self, deps, action_id, role_id, request):
        with self.store.transaction() as db:
            state = self.store.read_task(db, deps.task_id)
            role = self.configurations(state).get(role_id)
            parent = self.configurations(state).get("coordinator")
            if deps.assignment_id or role_id == "coordinator" or role is None:
                raise Failure("PERMISSION_DENIED", "仅根协调者可以委派已配置的子角色", 403)
            if state["config_snapshot"].get("harness_mode", "H1") != "H2":
                raise Failure("PERMISSION_DENIED", "当前配方禁用角色委派", 403)
            if state["spec_revision"] != deps.revision or state["pause_requested"] or state["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "当前任务不能创建新委派", 409)
            self.store.authorize_capability(db, state, "role.delegate")
            prior = db.execute("SELECT payload FROM assignments WHERE action_id=?", (action_id,)).fetchone()
            if prior:
                return {"assignment_id": json.loads(prior["payload"])["assignment_id"]}
            if not set(request.input_refs) <= set(state["input_refs"]) or not set(request.artifact_refs) <= set(state["artifact_ids"]):
                raise Failure("PERMISSION_DENIED", "委派请求超出父任务授权范围", 403)
            if request.resume_assignment_id:
                previous = self.read(request.resume_assignment_id, deps.task_id, db)
                expected = {**request.model_dump(), "resume_assignment_id": None}
                if previous["spec_revision"] != deps.revision or previous["role_id"] != role_id or previous["request"] != expected:
                    raise Failure("VERSION_CONFLICT", "恢复委派不能改变原有目标、输入或角色", 409)
                if previous["status"] != "waiting":
                    raise Failure("RECONCILIATION_REQUIRED", "只能恢复明确等待的委派", 409)
                # Keep original parent provenance while charging every resumed turn that uses this run.
                previous["budget_parent_run_id"] = deps.turn_id
                self.save(db, previous)
                return {"assignment_id": previous["assignment_id"], "status": "waiting", "namespace": previous["namespace"]}
            total = db.execute("SELECT COUNT(*) FROM assignments WHERE task_id=?", (deps.task_id,)).fetchone()[0]
            count = db.execute("SELECT COUNT(*) FROM assignments WHERE task_id=? AND role_id=?", (deps.task_id, role_id)).fetchone()[0]
            total_limit = state["config_snapshot"].get("generation", {}).get("max_assignments_per_task", self.engine.config.max_assignments_per_task)
            if total >= total_limit or count >= role.assignment_limit:
                raise Failure("RESOURCE_EXHAUSTED", "跨运行累计委派预算已用尽", 422)
            sources = []
            for ref in request.input_refs:
                resource = json.loads(db.execute("SELECT payload FROM resources WHERE id=?", (ref,)).fetchone()[0])
                sources.append({"resource_id": ref, "version": version(Path(resource["locator"]))})
            assignment_id = uid()
            value = {"schema_version": 1, "assignment_id": assignment_id, "task_id": deps.task_id,
                "parent_run_id": deps.turn_id, "budget_parent_run_id": deps.turn_id, "spec_revision": deps.revision, "role_id": role_id,
                "action_id": action_id, "run_id": uid(), "namespace": f"{deps.task_id}/{assignment_id}",
                "request": request.model_dump(), "effective_input_refs": request.input_refs,
                "effective_artifact_refs": request.artifact_refs, "input_versions": sources,
                "effective_capabilities": [cap for cap in role.allowed_capabilities if cap in parent.allowed_capabilities
                    and self.store.capability_allowed(db, state, cap)], "role_snapshot": role.model_dump(),
                "permission_ceiling": {**role.permission_ceiling.model_dump(), "workspace_write": role.permission_ceiling.workspace_write and parent.permission_ceiling.workspace_write},
                "status": "queued", "model_requests": 0, "actions": 0, "created_at": now(),
                "deadline_at": None, "result": None, "blocker": None}
            db.execute("INSERT INTO assignments VALUES(?,?,?,?,?,?,?)", (assignment_id, deps.task_id,
                deps.turn_id, role_id, action_id, "queued", encode(value)))
            self.store.event(db, deps.task_id, "assignment.prepared", value)
            return {"assignment_id": assignment_id, "status": "queued", "namespace": value["namespace"]}

    def authorize(self, db, deps, kind, args, *, check_budget=True):
        if kind == "environment.prepare" and args.get("source_refs"):
            self.authorize(db, deps, "code.execute", args, check_budget=False)
        state = self.store.read_task(db, deps.task_id)
        self.store.authorize_capability(db, state, kind)
        if not deps.assignment_id:
            role = self.configurations(state).get("coordinator")
            if role and (kind not in role.allowed_capabilities or (kind in {"fs.write_workspace", "code.execute", "environment.prepare", "environment.acquire"} and not role.permission_ceiling.workspace_write)):
                raise Failure("PERMISSION_DENIED", "能力超出协调者配置上限", 403)
            if role and kind in {"web.search", "web.read", "environment.acquire"} and role.permission_ceiling.network == "none":
                raise Failure("PERMISSION_DENIED", "协调者配置禁止网络请求", 403)
            if check_budget and role and db.execute("SELECT COUNT(*) FROM actions WHERE turn_id=?", (deps.turn_id,)).fetchone()[0] >= role.max_actions:
                raise Failure("RESOURCE_EXHAUSTED", "协调者回合动作预算已用尽", 422)
            return
        value = self.read(deps.assignment_id, deps.task_id, db)
        if value["status"] != "running" or value["spec_revision"] != deps.revision:
            raise Failure("EXECUTION_STOPPED", "委派未运行或修订已变化", 409)
        if value["deadline_at"] and datetime.fromisoformat(value["deadline_at"]) <= datetime.now(UTC):
            raise Failure("RESOURCE_EXHAUSTED", "委派总时限已用尽", 422)
        if kind not in value["effective_capabilities"]:
            raise Failure("PERMISSION_DENIED", "能力超出角色权限交集", 403)
        if kind in {"fs.write_workspace", "code.execute", "environment.prepare", "environment.acquire"} and not value["permission_ceiling"]["workspace_write"]:
            raise Failure("PERMISSION_DENIED", "角色上限禁止工作区写入", 403)
        if args.get("resource_id") and args["resource_id"] not in value["effective_input_refs"]:
            raise Failure("PERMISSION_DENIED", "资源超出委派范围", 403)
        if "input_refs" in args and not set(args["input_refs"]) <= set(value["effective_input_refs"]):
            raise Failure("PERMISSION_DENIED", "代码输入超出委派范围", 403)
        if "artifact_id" in args and args["artifact_id"] not in self.artifacts(value, db):
            raise Failure("PERMISSION_DENIED", "产物超出委派范围", 403)
        if args.get("environment_artifact_id") and args["environment_artifact_id"] not in self.artifacts(value, db):
            raise Failure("PERMISSION_DENIED", "依赖环境超出委派产物范围", 403)
        if args.get("wheel_artifact_ids") and not set(args["wheel_artifact_ids"]) <= set(self.artifacts(value, db)):
            raise Failure("PERMISSION_DENIED", "依赖wheel超出委派产物范围", 403)
        if args.get("job_id"):
            owned = db.execute("SELECT 1 FROM jobs j JOIN actions a ON j.action_id=a.id WHERE j.id=? AND a.turn_id=?", (args["job_id"], value["run_id"])).fetchone()
            if not owned:
                raise Failure("PERMISSION_DENIED", "作业不属于当前委派", 403)
        if args.get("action_id") and not db.execute("SELECT 1 FROM actions WHERE id=? AND turn_id=?", (args["action_id"], value["run_id"])).fetchone():
            raise Failure("PERMISSION_DENIED", "动作不属于当前委派", 403)
        if check_budget and value["actions"] >= value["role_snapshot"]["max_actions"]:
            raise Failure("RESOURCE_EXHAUSTED", "委派累计动作预算已用尽", 422)

    def artifacts(self, value, db=None):
        if db is None:
            with self.store.transaction() as connection:
                return self.artifacts(value, connection)
        own = [r["id"] for r in db.execute("SELECT f.id FROM artifacts f JOIN actions a ON json_extract(f.payload,'$.producer_action_id')=a.id WHERE a.turn_id=?", (value["run_id"],))]
        return [*value["effective_artifact_refs"], *own]

    def charge_action(self, db, deps):
        if deps.assignment_id:
            value = self.read(deps.assignment_id, deps.task_id, db)
            value["actions"] += 1
            self.save(db, value)

    def before_request(self, db, deps):
        assignment = self.read(deps.assignment_id, deps.task_id, db) if deps.assignment_id else None
        parent_run = assignment.get("budget_parent_run_id", assignment["parent_run_id"]) if assignment else deps.turn_id
        count = db.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='model.request_started' AND (json_extract(payload,'$.turn_id')=? OR json_extract(payload,'$.turn_id') IN (SELECT json_extract(payload,'$.run_id') FROM assignments WHERE parent_run_id=? OR json_extract(payload,'$.budget_parent_run_id')=?))", (deps.task_id, parent_run, parent_run, parent_run)).fetchone()[0]
        state = self.store.read_task(db, deps.task_id)
        limit = state["config_snapshot"].get("generation", {}).get("max_model_requests_per_turn", self.engine.config.max_model_requests_per_turn)
        coordinator = self.configurations(state).get("coordinator")
        if coordinator:
            limit = min(limit, coordinator.max_requests)
        if count >= limit:
            raise Failure("RESOURCE_EXHAUSTED", "父回合与所属委派的跨恢复累计请求预算已用尽", 422)
        if not deps.assignment_id:
            return
        value = self.read(deps.assignment_id, deps.task_id, db)
        if value["status"] != "running" or value["model_requests"] >= value["role_snapshot"]["max_requests"]:
            raise Failure("RESOURCE_EXHAUSTED", "委派累计模型预算已用尽或尚未运行", 422)
        if value["deadline_at"] and datetime.fromisoformat(value["deadline_at"]) <= datetime.now(UTC):
            raise Failure("RESOURCE_EXHAUSTED", "委派总时限已用尽", 422)
        value["model_requests"] += 1
        self.save(db, value)

    def start(self, value):
        observed = {source["resource_id"]: version(self.engine.resources.resolve(source["resource_id"], task_id=value["task_id"])) for source in value["input_versions"]}
        with self.store.transaction() as db:
            state = self.store.read_task(db, value["task_id"])
            if state["spec_revision"] != value["spec_revision"] or state["pause_requested"] or state["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "父任务控制或修订阻止启动委派", 409)
            current = self.read(value["assignment_id"], db=db)
            if current["status"] not in {"queued", "waiting"}:
                raise Failure("RECONCILIATION_REQUIRED", "委派不能重复启动", 409)
            for source in current["input_versions"]:
                if observed[source["resource_id"]] != source["version"]:
                    raise Failure("VERSION_CONFLICT", "委派输入在启动前发生变化", 409)
            if not current["deadline_at"]:
                current["deadline_at"] = (datetime.now(UTC) + timedelta(seconds=current["role_snapshot"]["seconds"])).isoformat()
            current["status"], current["blocker"] = "running", None
            self.save(db, current)
            self.store.event(db, value["task_id"], "assignment.started", {"assignment_id": value["assignment_id"], "run_id": current["run_id"], "role_id": current["role_id"]})
            return current

    def stop(self, assignment_id, status, result=None, blocker=None):
        with self.store.transaction() as db:
            value = self.read(assignment_id, db=db)
            value.update(status=status, result=result, blocker=blocker)
            self.save(db, value)
            self.store.event(db, value["task_id"], "assignment.settled", {"assignment_id": assignment_id,
                "role_id": value["role_id"], "status": status, "result": result, "blocker": blocker})

    def output_path(self, deps, relative):
        if not deps.assignment_id:
            return relative
        base = self.engine.resources.task_root(deps.task_id) / "assignments" / deps.assignment_id
        inside(base, relative)
        if not relative or Path(relative).is_absolute():
            raise Failure("PERMISSION_DENIED", "委派写入须为独立命名空间内相对路径", 403)
        return f"assignments/{deps.assignment_id}/{relative}"

    def validate_result(self, value, result):
        if result.kind != KINDS[value["role_id"]]:
            raise Failure("VERIFICATION_FAILED", "角色返回种类不匹配", 422)
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM actions WHERE turn_id=? AND status='pending'", (value["run_id"],)).fetchone():
                raise Failure("RECONCILIATION_REQUIRED", "委派副作用尚未对账，不能返回终态", 409)
            allowed = set(self.artifacts(value, db))
            for ref in result.artifact_ids:
                if ref not in allowed:
                    raise Failure("PERMISSION_DENIED", "返回引用了委派范围外的产物", 403)
            evidence = {r["id"] for r in db.execute("SELECT id FROM actions WHERE turn_id=? AND status='confirmed'", (value["run_id"],))} | allowed | set(value["effective_input_refs"])
            claimed = set(result.evidence_refs) | {ref for check in result.checks for ref in check.evidence_refs}
            if not claimed <= evidence:
                raise Failure("VERIFICATION_FAILED", "证据引用不是该委派的真实输入、产物或已确认动作", 422)
            for job_id in result.job_ids:
                if not db.execute("SELECT 1 FROM jobs j JOIN actions a ON j.action_id=a.id WHERE j.id=? AND a.turn_id=?", (job_id, value["run_id"])).fetchone():
                    raise Failure("PERMISSION_DENIED", "返回了其他委派的作业", 403)
            if db.execute("SELECT 1 FROM jobs j JOIN actions a ON j.action_id=a.id WHERE a.turn_id=? AND j.status NOT IN ('succeeded','failed','cancelled','timed_out')", (value["run_id"],)).fetchone():
                raise Failure("RECONCILIATION_REQUIRED", "委派作业未确认结束，须先等待或停止", 409)
        for ref in result.artifact_ids:
            self.engine.resources.artifact(ref, value["task_id"])

    def reconcile(self):
        with self.store.transaction() as db:
            for row in db.execute("SELECT payload FROM assignments WHERE status IN ('queued','running')").fetchall():
                value = json.loads(row["payload"])
                value.update(status="waiting", blocker={"code": "SERVICE_RESTARTED", "message": "服务重启；恢复自己的检查点，不能复制父历史或盲目重做副作用"})
                self.save(db, value)

    def cancel_waiting(self, task_id, db=None):
        if db is None:
            with self.store.transaction() as connection:
                return self.cancel_waiting(task_id, connection)
        for row in db.execute("SELECT payload FROM assignments WHERE task_id=? AND status IN ('queued','waiting')", (task_id,)).fetchall():
            value = json.loads(row["payload"])
            pending = db.execute("SELECT 1 FROM actions WHERE turn_id=? AND status='pending'", (value["run_id"],)).fetchone()
            jobs = db.execute("SELECT 1 FROM jobs j JOIN actions a ON j.action_id=a.id WHERE a.turn_id=? AND j.status NOT IN ('succeeded','failed','cancelled','timed_out')", (value["run_id"],)).fetchone()
            if not pending and not jobs:
                value.update(status="cancelled", blocker=None)
                self.save(db, value)
                self.store.event(db, task_id, "assignment.cancelled", {"assignment_id": value["assignment_id"]})
