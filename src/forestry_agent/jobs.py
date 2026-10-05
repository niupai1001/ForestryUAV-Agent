from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import psutil

from .config import Config
from .contracts import Failure
from .resources import Resources, version
from .store import Store, encode, now, uid

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


class Docker:
    """No shell, user Docker credentials, ambient endpoint or unbounded log buffers."""

    def __init__(self, config: Config):
        self.config = config

    def call(self, *args: str) -> dict:
        directory = self.config.workspace_root / ".forestry" / "docker"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as out, tempfile.TemporaryFile(dir=directory) as err:
            try:
                result = subprocess.run(["docker", "--config", str(directory), "--host",
                    self.config.docker_host, *args], stdout=out, stderr=err, timeout=30,
                    check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise Failure("EXECUTOR_UNAVAILABLE", str(error), 503, True) from error
            out.seek(0)
            err.seek(0)
            return {"code": result.returncode, "stdout": out.read(65536).decode(errors="replace"),
                    "stderr": err.read(4096).decode(errors="replace")}

    def inspect(self, name: str):
        result = self.call("container", "inspect", name)
        if result["code"] == 0:
            return json.loads(result["stdout"])[0]
        if "No such container" in result["stderr"] or "No such object" in result["stderr"]:
            return None
        raise Failure("EXECUTOR_UNAVAILABLE", result["stderr"], 503, True)


class Jobs:
    """Durable isolated jobs. Polling is application work and never invokes a model."""

    def __init__(self, config: Config, store: Store, resources: Resources, docker=None):
        self.config, self.store, self.resources = config, store, resources
        self.docker = docker or Docker(config)
        self.worker: asyncio.Task | None = None

    def read(self, job_id: str, task_id: str | None = None):
        with self.store.transaction() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise Failure("NOT_FOUND", "作业不存在", 404)
            job = json.loads(row["payload"])
        if task_id and job["task_id"] != task_id:
            raise Failure("PERMISSION_DENIED", "作业不属于当前任务", 403)
        return job

    def save(self, job, kind):
        job["updated_at"] = now()
        with self.store.transaction() as db:
            current = db.execute("SELECT payload FROM jobs WHERE id=?", (job["job_id"],)).fetchone()
            if current:
                job["cancel_requested"] |= json.loads(current["payload"])["cancel_requested"]
            db.execute("UPDATE jobs SET status=?,payload=?,updated_at=? WHERE id=?",
                (job["status"], encode(job), job["updated_at"], job["job_id"]))
            self.store.event(db, job["task_id"], kind, self.public(job))

    @staticmethod
    def public(job):
        return {k: v for k, v in job.items() if k not in {"code", "mounts", "directory"}}

    def submit(self, task_id: str, revision: int, action_id: str, code: str,
               input_refs: list[str], seconds: int = 300):
        if not code or len(code.encode()) > 65536:
            raise Failure("SCHEMA_INVALID", "代码须为 1–65536 字节的 Python 文本")
        if seconds < 1 or seconds > self.config.max_code_seconds:
            raise Failure("RESOURCE_EXHAUSTED", "作业时限超出配置", 422)
        mounts = []
        for ref in dict.fromkeys(input_refs):
            source = self.resources.resolve(ref, task_id=task_id)
            # Directory snapshots and link-safe bulk mounting require a later worker.
            if not source.is_file() or source.is_symlink() or "," in str(source):
                raise Failure("PRECONDITION_MISSING", "通用代码作业当前仅支持无链接的单文件输入", 422)
            mounts.append({"resource_id": ref, "source": str(source), "target": f"/inputs/{ref}",
                           "version": version(source)})
        with self.store.transaction() as db:
            existing = db.execute("SELECT payload FROM jobs WHERE action_id=?", (action_id,)).fetchone()
            if existing:
                return {"job": self.public(json.loads(existing["payload"]))}
            state = self.store.read_task(db, task_id)
            if state["spec_revision"] != revision or state["pause_requested"] or state["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "任务规范或控制状态不允许新作业", 409)
            job_id = uid()
            job = {"schema_version": 1, "job_id": job_id, "task_id": task_id,
                "action_id": action_id, "spec_revision": revision, "status": "queued",
                "container_name": "forestry-job-" + job_id, "container_id": None,
                "image_id": None, "code": code, "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "mounts": mounts, "seconds": seconds, "directory": str(self.config.workspace_root /
                    "tasks" / task_id / "jobs" / job_id), "created_at": now(), "started_at": None,
                "updated_at": now(), "cancel_requested": False, "exit_code": None,
                "artifact_ids": [], "blocker": None}
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
                (job_id, task_id, action_id, revision, "queued", encode(job), now()))
            self.store.event(db, task_id, "job.queued", self.public(job))
        return {"job": self.public(job)}

    def request_cancel(self, job_id: str, task_id: str):
        job = self.read(job_id, task_id)
        if job["status"] not in TERMINAL:
            job["cancel_requested"] = True
            self.save(job, "job.cancel_requested")
        return self.public(job)

    def matches(self, job, container):
        labels = container.get("Config", {}).get("Labels", {})
        if labels.get("forestry.job") != job["job_id"] or labels.get("forestry.task") != job["task_id"]:
            raise Failure("RECONCILIATION_REQUIRED", "同名容器身份不匹配，拒绝操作", 409)
        if job["container_id"] and container["Id"] != job["container_id"]:
            raise Failure("RECONCILIATION_REQUIRED", "容器 ID 发生变化", 409)

    def may_start(self, job):
        state = self.store.task(job["task_id"])
        current = self.read(job["job_id"])
        if current["cancel_requested"] or state["cancellation_requested"] or state["pause_requested"] or state["spec_revision"] != job["spec_revision"]:
            raise Failure("EXECUTION_STOPPED", "控制状态已变化，不启动新容器动作", 409)

    def tick(self):
        with self.store.transaction() as db:
            jobs = [json.loads(r["payload"]) for r in db.execute(
                "SELECT payload FROM jobs WHERE status NOT IN ('succeeded','failed','cancelled','timed_out') ORDER BY updated_at")]
        active = any(j["status"] in {"creating", "running", "collecting"} for j in jobs)
        for job in jobs:
            try:
                state = self.store.task(job["task_id"])
                stopping = job["cancel_requested"] or state["cancellation_requested"] or state["spec_revision"] != job["spec_revision"]
                if job["status"] == "queued":
                    if stopping:
                        job["status"] = "cancelled"
                        self.save(job, "job.terminal")
                        continue
                    if active or state["pause_requested"]:
                        continue
                    if psutil.virtual_memory().available < self.config.max_code_memory_bytes + 2**30 or shutil.disk_usage(self.config.workspace_root).free < 2**30:
                        job["blocker"] = {"code": "RESOURCE_WAIT", "message": "等待内存或磁盘资源"}
                        self.save(job, "job.resource_wait")
                        continue
                    image = self.docker.call("image", "inspect", self.config.code_image, "--format", "{{.Id}}")
                    if image["code"] != 0:
                        raise Failure("DEPENDENCY_MISSING", "隔离运行镜像未准备，不自动联网下载", 422)
                    job["image_id"] = image["stdout"].strip()
                    directory = Path(job["directory"])
                    (directory / "output").mkdir(parents=True, exist_ok=True)
                    (directory / "run.py").write_text(job["code"], encoding="utf-8")
                    for mount in job["mounts"]:
                        source = self.resources.resolve(mount["resource_id"], task_id=job["task_id"])
                        if str(source) != mount["source"] or version(source) != mount["version"]:
                            raise Failure("VERSION_CONFLICT", "排队期间输入文件已变化", 409)
                    job["status"] = "creating"
                    self.save(job, "job.create_intent")
                    active = True
                container = self.docker.inspect(job["container_name"])
                if job["status"] == "creating" and container is None:
                    if stopping:
                        job["status"] = "cancelled"
                        self.save(job, "job.terminal")
                        continue
                    self.may_start(job)
                    directory = Path(job["directory"])
                    args = ["create", "--name", job["container_name"], "--label", "forestry.job=" + job["job_id"],
                        "--label", "forestry.task=" + job["task_id"], "--network", "none", "--read-only",
                        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
                        "--memory", str(self.config.max_code_memory_bytes), "--memory-swap", str(self.config.max_code_memory_bytes),
                        "--cpus", str(self.config.max_code_cpus), "--user", "65534:65534",
                        "--tmpfs", "/tmp:rw,noexec,nosuid,size=67108864", "--log-opt", "max-size=1m",
                        "--log-opt", "max-file=2", "--workdir", "/output",
                        "--mount", f"type=bind,src={directory / 'run.py'},dst=/run.py,readonly",
                        "--mount", f"type=bind,src={directory / 'output'},dst=/output"]
                    for mount in job["mounts"]:
                        args.extend(["--mount", f"type=bind,src={mount['source']},dst={mount['target']},readonly"])
                    result = self.docker.call(*args, job["image_id"], "python", "-I", "-B", "/run.py")
                    if result["code"] != 0:
                        raise Failure("RECONCILIATION_REQUIRED", result["stderr"], 409)
                    container = self.docker.inspect(job["container_name"])
                if container is None:
                    raise Failure("RECONCILIATION_REQUIRED", "已登记容器丢失，拒绝重新执行", 409)
                self.matches(job, container)
                job["container_id"] = container["Id"]
                observed = container["State"]
                if job["status"] == "creating" and observed["Status"] == "created":
                    if stopping:
                        job["status"] = "cancelled"
                        self.save(job, "job.terminal")
                        continue
                    self.may_start(job)
                    job["started_at"] = now()
                    job["status"] = "running"
                    self.save(job, "job.start_intent")
                    result = self.docker.call("start", job["container_id"])
                    if result["code"] != 0:
                        raise Failure("RECONCILIATION_REQUIRED", result["stderr"], 409)
                    continue
                if observed["Status"] == "created" and job["status"] == "running":
                    raise Failure("RECONCILIATION_REQUIRED", "启动未确认，需人工对账，禁止盲目重启", 409)
                elapsed = (datetime.now(UTC) - datetime.fromisoformat(job["started_at"])).total_seconds() if job["started_at"] else 0
                expired = elapsed > job["seconds"]
                if observed["Running"]:
                    if stopping or expired:
                        self.docker.call("kill", job["container_id"])
                        confirmed = self.docker.inspect(job["container_name"])
                        self.matches(job, confirmed)
                        if confirmed["State"]["Running"]:
                            raise Failure("RECONCILIATION_REQUIRED", "容器仍运行，不能确认停止", 409)
                        job["status"] = "cancelled" if stopping else "timed_out"
                        job["exit_code"] = confirmed["State"]["ExitCode"]
                        self.save(job, "job.terminal")
                    continue
                job["exit_code"] = observed["ExitCode"]
                job["status"] = "cancelled" if stopping else "failed" if job["exit_code"] else "collecting"
                job["blocker"] = None
                self.save(job, "job.execution_observed")
                if job["status"] == "collecting":
                    # Keep a recoverable boundary until every output has been frozen.
                    files = []
                    for directory, folders, names in os.walk(Path(job["directory"]) / "output", followlinks=False):
                        for name in folders + names:
                            path = Path(directory) / name
                            if path.is_symlink():
                                raise Failure("PERMISSION_DENIED", "作业输出不接受符号链接", 403)
                            files.append(path)
                            if len(files) > 1000:
                                raise Failure("RESOURCE_EXHAUSTED", "作业产物数量超出上限", 422)
                    for path in files:
                        if path.is_symlink():
                            raise Failure("PERMISSION_DENIED", "作业输出不接受符号链接", 403)
                        if path.is_file():
                            artifact = self.resources.import_file(job["task_id"], job["action_id"], path,
                                "jobs/" + job["job_id"] + "/" + path.relative_to(Path(job["directory"]) / "output").as_posix())
                            if artifact["artifact_id"] not in job["artifact_ids"]:
                                job["artifact_ids"].append(artifact["artifact_id"])
                    job["status"] = "succeeded"
                    self.save(job, "job.terminal")
            except (Failure, OSError, ValueError, KeyError) as error:
                job["blocker"] = {"code": getattr(error, "code", "EXECUTOR_ERROR"), "message": str(error)[:1500]}
                self.save(job, "job.blocked")
        # A task cancellation is terminal only after every effect has settled.
        with self.store.transaction() as db:
            for row in db.execute("SELECT state FROM tasks WHERE status='paused'").fetchall():
                state = json.loads(row["state"])
                task_id = state["task_id"]
                if not state["cancellation_requested"]:
                    continue
                unresolved = db.execute("SELECT 1 FROM jobs WHERE task_id=? AND status NOT IN ('succeeded','failed','cancelled','timed_out')", (task_id,)).fetchone()
                pending = db.execute("SELECT 1 FROM actions WHERE task_id=? AND status='pending'", (task_id,)).fetchone()
                if not unresolved and not pending:
                    state.update(status="cancelled", blockers=[])
                    self.store.save_task(db, state, state["state_version"])
                    self.store.event(db, task_id, "task.terminal", {"status": "cancelled"})

    def logs(self, job_id, task_id):
        job = self.read(job_id, task_id)
        if not job["container_id"]:
            return {"text": "", "bounded": True}
        container = self.docker.inspect(job["container_name"])
        if not container:
            raise Failure("RECONCILIATION_REQUIRED", "日志容器丢失", 409)
        self.matches(job, container)
        result = self.docker.call("logs", "--tail", "200", job["container_id"])
        return {"text": result["stdout"], "stderr": result["stderr"], "bounded": True}

    async def loop(self):
        while True:
            await asyncio.to_thread(self.tick)
            await asyncio.sleep(2)

    def start(self):
        self.worker = asyncio.create_task(self.loop())

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
