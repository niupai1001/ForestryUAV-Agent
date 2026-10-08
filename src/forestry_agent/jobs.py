from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

import psutil

from .config import Config
from .contracts import Failure
from .environments import execution_code
from .resources import Resources, native_path, version
from .store import Store, encode, now, uid

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


class Docker:
    """No shell, user Docker credentials, ambient endpoint or unbounded log buffers."""

    def __init__(self, config: Config):
        self.config = config

    @staticmethod
    def wsl_path(value):
        path = PureWindowsPath(value)
        if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ":":
            raise Failure("PRECONDITION_MISSING", "WSL挂载要求已授权的本地绝对盘符路径", 422)
        return f"/mnt/{path.drive[0].lower()}/" + "/".join(path.parts[1:])

    def command(self, *args):
        directory = self.config.workspace_root / ".forestry" / "docker"
        if not self.config.docker_distribution:
            return [shutil.which("docker") or "docker", "--config", str(directory), "--host", self.config.docker_host, *args]
        translated = list(args)
        for index, argument in enumerate(translated):
            if index and translated[index - 1] == "--mount":
                fields = []
                for field in argument.split(","):
                    name, separator, value = field.partition("=")
                    fields.append(name + separator + self.wsl_path(value) if name in {"src", "source"} else field)
                translated[index] = ",".join(fields)
        # No shell or exposed TCP daemon. Distribution and endpoint are owner configuration.
        return [shutil.which("wsl.exe") or "wsl.exe", "--distribution", self.config.docker_distribution,
            "--user", "root", "--exec", "/usr/bin/env", "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME=/root", "docker", "--config", self.wsl_path(directory), "--host", self.config.docker_host, *translated]

    def call(self, *args: str) -> dict:
        directory = self.config.workspace_root / ".forestry" / "docker"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as out, tempfile.TemporaryFile(dir=directory) as err:
            try:
                clean = {name: os.environ[name] for name in ["PATH", "SystemRoot", "WINDIR", "TEMP", "TMP"] if name in os.environ}
                result = subprocess.run(self.command(*args), stdout=out, stderr=err, timeout=30,
                    check=False, env=clean, cwd=self.config.workspace_root)
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
        self.cancel_assignments = None
        self.environment_resolver = None
        self.source_build_resolver = None
        self.input_artifact_validator = None

    def read(self, job_id: str, task_id: str | None = None):
        with self.store.transaction() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise Failure("NOT_FOUND", "作业不存在；job_id须取成功提交回执的job.job_id，不能使用action_id。提交失败没有创建作业，先修正失败的调用。", 404)
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
        result = {k: v for k, v in job.items() if k not in {"code", "mounts", "directory"}}
        if result.get("environment"):
            result["environment"] = {k: v for k, v in result["environment"].items() if k != "source"}
        return result

    @staticmethod
    def model_receipt(receipt):
        """Keep actionable identities without repeating persisted install recipes.

        Public API/job records and the confirmed action journal remain complete.
        This projection also applies to replayed historical tool receipts.
        """
        result = dict(receipt)
        if isinstance(result.get('job'), dict):
            job = dict(result['job'])
            job.pop('environment_recipe', None)
            job.pop('source_recipe', None)
            if isinstance(job.get('environment'), dict):
                job['environment'] = {k: v for k, v in job['environment'].items() if k != 'manifest'}
            result['job'] = job
        return result

    def submit(self, task_id: str, revision: int, action_id: str, code: str,
               input_refs: list[str], seconds: int = 300, *, environment_artifact_id=None,
               job_kind="code.execute", environment_recipe=None, input_artifact_ids=None, source_recipe=None,
               source_build_only=False):
        if job_kind not in {"code.execute", "environment.prepare"}:
            raise Failure("SCHEMA_INVALID", "未知受限作业类型")
        if source_recipe and job_kind != "environment.prepare":
            raise Failure("SCHEMA_INVALID", "源码构建仅用于依赖准备作业")
        if type(source_build_only) is not bool or (source_build_only and not source_recipe):
            raise Failure('SCHEMA_INVALID', '仅构建用途需实际源码配方')
        if not code or len(code.encode()) > 65536:
            raise Failure("SCHEMA_INVALID", "代码须为 1–65536 字节的 Python 文本")
        if seconds < 1 or seconds > self.config.max_code_seconds:
            raise Failure("RESOURCE_EXHAUSTED", "作业时限超出配置", 422)
        mounts = []
        for ref in dict.fromkeys(input_refs):
            source = self.resources.resolve(ref, task_id=task_id)
            # Directory snapshots and link-safe bulk mounting require a later worker.
            if not native_path(source).is_file() or native_path(source).is_symlink() or "," in str(source):
                raise Failure("PRECONDITION_MISSING", "通用代码作业当前仅支持无链接的单文件输入", 422)
            mounts.append({"resource_id": ref, "source": str(source), "target": f"/inputs/{ref}",
                           "version": version(source)})
        for ref in dict.fromkeys(input_artifact_ids or []):
            if job_kind != "environment.prepare" or not self.input_artifact_validator:
                raise Failure("PERMISSION_DENIED", "产物输入仅用于经核验的离线依赖准备", 403)
            artifact = self.input_artifact_validator(task_id, revision, ref)
            source = Path(artifact["locator"])
            if native_path(source).is_symlink() or "," in str(source):
                raise Failure("PRECONDITION_MISSING", "依赖产物路径不支持链接或逗号", 422)
            mounts.append({"artifact_id": ref, "source": str(source), "target": f"/inputs/{ref}", "version": version(source)})
        environment = None
        if environment_artifact_id:
            if job_kind != "code.execute" or not self.environment_resolver:
                raise Failure("PRECONDITION_MISSING", "当前执行器没有环境解析器", 422)
            environment = self.environment_resolver(task_id, revision, environment_artifact_id)
        with self.store.transaction() as db:
            existing = db.execute("SELECT payload FROM jobs WHERE action_id=?", (action_id,)).fetchone()
            if existing:
                return {"job": self.public(json.loads(existing["payload"]))}
            state = self.store.read_task(db, task_id)
            if state["spec_revision"] != revision or state["pause_requested"] or state["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "任务规范或控制状态不允许新作业", 409)
            self.store.authorize_capability(db, state, job_kind)
            if source_recipe:
                self.store.authorize_capability(db, state, "code.execute")
            job_id = uid()
            assignment = db.execute("SELECT id FROM assignments WHERE json_extract(payload,'$.run_id')=(SELECT turn_id FROM actions WHERE id=?)", (action_id,)).fetchone()
            job = {"schema_version": 1, "job_id": job_id, "task_id": task_id,
                "kind": job_kind, "image_reference": self.config.code_image,
                "tmpfs_bytes": 1024*1024*1024 if job_kind == "environment.prepare" else 67108864,
                "assignment_id": assignment["id"] if assignment else None,
                "action_id": action_id, "spec_revision": revision, "status": "queued",
                "container_name": "forestry-job-" + job_id, "container_id": None,
                "image_id": None, "code": code, "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "mounts": mounts, "seconds": seconds, "directory": str(self.config.workspace_root /
                    "tasks" / task_id / "jobs" / job_id), "created_at": now(), "started_at": None,
                "updated_at": now(), "cancel_requested": False, "exit_code": None,
                "artifact_ids": [], "blocker": None}
            if environment:
                job["environment"] = environment
                job["environment_tmpfs_bytes"] = 1024*1024*1024
            if environment_recipe is not None:
                job["environment_recipe"] = environment_recipe
            if source_recipe:
                job["source_recipe"] = source_recipe
                job["build_tmpfs_bytes"] = 1024*1024*1024
                if source_build_only:
                    job['source_build_only'] = True
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
        with self.store.transaction() as db:
            state = self.store.read_task(db, job["task_id"])
            self.store.authorize_capability(db, state, job.get("kind", "code.execute"))
            if job.get("source_recipe"):
                self.store.authorize_capability(db, state, "code.execute")
        current = self.read(job["job_id"])
        if current["cancel_requested"] or state["cancellation_requested"] or state["pause_requested"] or state["spec_revision"] != job["spec_revision"]:
            raise Failure("EXECUTION_STOPPED", "控制状态已变化，不启动新容器动作", 409)
        self.verify_inputs(job)

    def verify_inputs(self, job):
        for mount in job["mounts"]:
            if mount.get("artifact_id"):
                if not self.input_artifact_validator:
                    raise Failure("PRECONDITION_MISSING", "恢复缺少依赖产物核验器", 422)
                artifact = self.input_artifact_validator(job["task_id"], job["spec_revision"], mount["artifact_id"])
                source = Path(artifact["locator"])
            else:
                source = self.resources.resolve(mount["resource_id"], task_id=job["task_id"])
            if str(source) != mount["source"] or version(source) != mount["version"]:
                raise Failure("VERSION_CONFLICT", "排队或启动前输入文件已变化", 409)

    def tick(self):
        with self.store.transaction() as db:
            jobs = [json.loads(r["payload"]) for r in db.execute(
                "SELECT payload FROM jobs WHERE status NOT IN ('succeeded','failed','cancelled','timed_out') ORDER BY updated_at")]
        active = any(j["status"] in {"creating", "running", "collecting"} for j in jobs)
        for job in jobs:
            try:
                state = self.store.task(job["task_id"])
                with self.store.transaction() as db:
                    permitted = self.store.capability_allowed(db, state, job.get("kind", "code.execute"))
                    if job.get("source_recipe"):
                        permitted = permitted and self.store.capability_allowed(db, state, "code.execute")
                stopping = job["cancel_requested"] or state["cancellation_requested"] or state["spec_revision"] != job["spec_revision"] or not permitted
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
                    image = self.docker.call("image", "inspect", job.get("image_reference", self.config.code_image), "--format", "{{.Id}}")
                    if image["code"] != 0:
                        raise Failure("DEPENDENCY_MISSING", "隔离运行镜像未准备，不自动联网下载", 422)
                    job["image_id"] = image["stdout"].strip()
                    if job.get("environment") and job["image_id"] != job["environment"]["image_id"]:
                        raise Failure("VERSION_CONFLICT", "运行镜像与依赖准备镜像身份不一致", 409)
                    directory = Path(job["directory"])
                    native_path(directory / "output").mkdir(parents=True, exist_ok=True)
                    prepared = execution_code(job["code"], job["environment"]) if job.get("environment") else job["code"]
                    job["prepared_code_sha256"] = hashlib.sha256(prepared.encode()).hexdigest()
                    native_path(directory / "run.py").write_bytes(prepared.encode("utf-8"))
                    self.verify_inputs(job)
                    self.verify_environment(job)
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
                    self.verify_environment(job)
                    directory = Path(job["directory"])
                    args = ["create", "--name", job["container_name"], "--label", "forestry.job=" + job["job_id"],
                        "--label", "forestry.task=" + job["task_id"], "--network", "none", "--read-only",
                        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
                        "--memory", str(self.config.max_code_memory_bytes), "--memory-swap", str(self.config.max_code_memory_bytes),
                        "--cpus", str(self.config.max_code_cpus), "--user", "65534:65534",
                        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={job.get('tmpfs_bytes', 67108864)}", "--log-opt", "max-size=1m",
                        "--log-opt", "max-file=2", "--workdir", "/output",
                        "--mount", f"type=bind,src={directory / 'run.py'},dst=/run.py,readonly",
                        "--mount", f"type=bind,src={directory / 'output'},dst=/output"]
                    for mount in job["mounts"]:
                        args.extend(["--mount", f"type=bind,src={mount['source']},dst={mount['target']},readonly"])
                    if job.get("source_recipe"):
                        args.extend(["--tmpfs", f"/build:rw,exec,nosuid,nodev,mode=1777,size={job['build_tmpfs_bytes']}"])
                    if job.get("environment"):
                        # Native wheels need executable mappings on a Linux FS.
                        # Keep /tmp noexec; this separate mount is bounded, nosuid,
                        # nodev and present only for a validated dependency archive.
                        args.extend(["--tmpfs", f"/environment:rw,exec,nosuid,nodev,mode=1777,size={job['environment_tmpfs_bytes']}"])
                        args.extend(["--mount", f"type=bind,src={job['environment']['source']},dst=/environment.zip,readonly"])
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
                    self.verify_environment(job)
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
                    output_root = Path(job["directory"]) / "output"
                    walk_root = native_path(output_root)
                    for directory, folders, names in os.walk(walk_root, followlinks=False):
                        for name in folders + names:
                            path = output_root / Path(directory).relative_to(walk_root) / name
                            if native_path(path).is_symlink():
                                raise Failure("PERMISSION_DENIED", "作业输出不接受符号链接", 403)
                            files.append(path)
                            if len(files) > 1000:
                                raise Failure("RESOURCE_EXHAUSTED", "作业产物数量超出上限", 422)
                    for path in files:
                        if native_path(path).is_symlink():
                            raise Failure("PERMISSION_DENIED", "作业输出不接受符号链接", 403)
                        if native_path(path).is_file():
                            prefix = f"assignments/{job['assignment_id']}/" if job.get("assignment_id") else ""
                            artifact = self.resources.import_file(job["task_id"], job["action_id"], path,
                                prefix + "jobs/" + job["job_id"] + "/" + path.relative_to(Path(job["directory"]) / "output").as_posix())
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
                if self.cancel_assignments:
                    self.cancel_assignments(task_id, db)
                unresolved = db.execute("SELECT 1 FROM jobs WHERE task_id=? AND status NOT IN ('succeeded','failed','cancelled','timed_out')", (task_id,)).fetchone()
                pending = db.execute("SELECT 1 FROM actions WHERE task_id=? AND status='pending'", (task_id,)).fetchone()
                assignments = db.execute("SELECT 1 FROM assignments WHERE task_id=? AND status NOT IN ('succeeded','partial','failed','cancelled')", (task_id,)).fetchone()
                if not unresolved and not pending and not assignments:
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

    def verify_environment(self, job):
        if job.get("environment"):
            if not self.environment_resolver:
                raise Failure("PRECONDITION_MISSING", "当前执行器没有环境解析器", 422)
            current = self.environment_resolver(job["task_id"], job["spec_revision"], job["environment"]["artifact_id"])
            if current != job["environment"]:
                raise Failure("VERSION_CONFLICT", "排队或恢复期间依赖环境身份变化", 409)

    def observation(self, job_id, task_id):
        job = self.read(job_id, task_id)
        # Keep the actionable identity in one bounded native tool result. Full
        # recipes/manifests remain in the job record and immutable lock artifact;
        # repeating both here spills the archive ID behind a long wheel listing.
        observed = self.public(job)
        observed.pop("environment_recipe", None)
        observed.pop("source_recipe", None)
        if observed.get("environment"):
            observed["environment"] = {k: v for k, v in observed["environment"].items() if k != "manifest"}
        result = {"job": observed}
        if job["status"] in TERMINAL and job["container_id"]:
            # The model needs the actual traceback to repair generated code.
            # Keep the same job ownership and container identity checks as the UI.
            result["logs"] = self.logs(job_id, task_id)
        if job["status"] == "succeeded" and job.get("kind") == "environment.prepare" and self.environment_resolver:
            if job.get('source_build_only'):
                if not self.source_build_resolver:
                    raise Failure('VERIFICATION_FAILED', '源码构建缺少生产者核验', 422)
                result['prepared_wheels'] = self.source_build_resolver(task_id, job['spec_revision'], job['action_id'])
                return result
            archive = next((ref for ref in job["artifact_ids"] if self.resources.artifact(ref, task_id)["name"] == "environment.zip"), None)
            if not archive and self.source_build_resolver and any(self.resources.artifact(ref, task_id)['name'] == 'source-build-lock.json' for ref in job['artifact_ids']):
                result['prepared_wheels'] = self.source_build_resolver(task_id, job['spec_revision'], job['action_id'])
                return result
            if not archive:
                raise Failure("VERIFICATION_FAILED", "依赖作业未生成可复用环境归档", 422)
            environment = self.environment_resolver(task_id, job["spec_revision"], archive)
            result["prepared_environment"] = {k:v for k,v in environment.items() if k not in {"source", "manifest"}}
            result["prepared_environment"]["installed"] = environment["manifest"]["installed"]
        return result

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
