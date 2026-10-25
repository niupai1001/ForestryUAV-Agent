from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from pydantic import Field

from .admission import assess, execution_profile, limited_code
from .config import Config
from .contracts import Boundary, Failure, ResourceRequest
from .environments import execution_code
from .resources import Resources, native_path, version
from .store import Store, encode, now, uid

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


class CleanupRequest(Boundary):
    request_id: str = Field(min_length=1,max_length=200)
    max_jobs: int = Field(default=100,ge=1,le=200)


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
            value = {"code": result.returncode}
            for name, stream, limit in (("stdout", out, 65536), ("stderr", err, 4096)):
                if args and args[0] == "logs":
                    # Tracebacks end with the actionable exception. A bounded
                    # prefix can hide it behind a long generated-code frame.
                    size = stream.seek(0, os.SEEK_END)
                    stream.seek(max(0, size - limit))
                    value[name + "_truncated"] = size > limit
                value[name] = stream.read(limit).decode(errors="replace")
            if args and args[0] == "logs":
                value["window"] = "tail"
            return value

    def inspect(self, name: str):
        # Full inspect repeats every bind mount in multiple fields. A flight with
        # many readonly inputs can exceed the bounded stdout buffer and corrupt
        # JSON. Ask Docker for identity/state/limits, independent of mount count.
        projection = ('{"Id":{{json .Id}},"Image":{{json .Image}},"State":{{json .State}},'
            '"Config":{"Labels":{{json .Config.Labels}}},"HostConfig":{'
            '"NetworkMode":{{json .HostConfig.NetworkMode}},"ReadonlyRootfs":{{json .HostConfig.ReadonlyRootfs}},'
            '"CapDrop":{{json .HostConfig.CapDrop}},"Memory":{{json .HostConfig.Memory}},'
            '"MemorySwap":{{json .HostConfig.MemorySwap}},"NanoCpus":{{json .HostConfig.NanoCpus}},'
            '"PidsLimit":{{json .HostConfig.PidsLimit}}}}')
        result = self.call("container", "inspect", name, "--format", projection)
        if result["code"] == 0:
            return json.loads(result["stdout"])
        if "No such container" in result["stderr"] or "No such object" in result["stderr"]:
            return None
        raise Failure("EXECUTOR_UNAVAILABLE", result["stderr"], 503, True)


class Jobs:
    """Durable isolated jobs. Polling is application work and never invokes a model."""

    def __init__(self, config: Config, store: Store, resources: Resources, docker=None, *, resource_probe=None):
        self.config, self.store, self.resources = config, store, resources
        self.docker = docker or Docker(config)
        self.resource_probe = resource_probe or execution_profile
        self.worker: asyncio.Task | None = None
        self.cancel_assignments = None
        self.settle_obsolete_assignments = None
        self.environment_resolver = None
        self.source_build_resolver = None
        self.source_requirements_resolver = None
        self.input_artifact_validator = None
        self.cleanup_lock = threading.RLock()

    def read(self, job_id: str, task_id: str | None = None):
        with self.store.transaction() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise Failure("NOT_FOUND", "作业不存在；job_id须取成功提交回执的job.job_id，不能使用action_id。提交失败没有创建作业，先修正失败的调用。", 404)
            job = json.loads(row["payload"])
        if task_id and job["task_id"] != task_id:
            raise Failure("PERMISSION_DENIED", "作业不属于当前任务", 403)
        return job

    def list(self, task_id, offset=0):
        self.store.task(task_id)
        with self.store.transaction() as db:
            rows = db.execute('SELECT payload FROM jobs WHERE task_id=? ORDER BY updated_at DESC,id LIMIT 21 OFFSET ?',
                              (task_id, offset)).fetchall()
        return {'jobs': [self.public(json.loads(row['payload'])) for row in rows[:20]],
                'next': offset + min(len(rows), 20), 'has_more': len(rows) > 20}

    def save(self, job, kind):
        job["updated_at"] = now()
        with self.store.transaction() as db:
            current = db.execute("SELECT payload FROM jobs WHERE id=?", (job["job_id"],)).fetchone()
            if current:
                job["cancel_requested"] |= json.loads(current["payload"])["cancel_requested"]
            db.execute("UPDATE jobs SET status=?,payload=?,updated_at=? WHERE id=?",
                (job["status"], encode(job), job["updated_at"], job["job_id"]))
            payload = self.public(job)
            if kind == "job.terminal":
                # 终态通知按事件身份去重：同一个作业的同一个终态只通知一次，
                # 重复的结算、迟到回调和重启对账都不会再次唤醒会话。
                key = f"{job['job_id']}:{job['status']}"
                if db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='job.terminal' "
                        "AND json_extract(payload,'$.notification_key')=? LIMIT 1",
                        (job["task_id"], key)).fetchone():
                    return
                payload = {**payload, "notification_key": key}
            self.store.event(db, job["task_id"], kind, payload)

    @staticmethod
    def readable_name(kind, runtime_profile=None, source_inspection=None):
        """作业对用户的可读名称；类型与用途分开，不用内部镜像名代替。"""
        if kind == "environment.prepare":
            return "源码检查" if source_inspection else "环境准备"
        return {"scientific": "科学计算", "imagery": "影像处理",
                "orthomosaic": "正射拼接"}.get(runtime_profile, "代码作业")

    #: 一个作业对外的登记项：名称、类型、来源消息、逻辑活动、调用/尝试、状态、时间、
    #: 结果与恢复关系。旧记录缺项一律标未知，不猜历史事实。
    REGISTERED = ("job_id", "readable_name", "kind", "status", "request_turn_id", "call_id",
                  "attempt_id", "logical_activity", "created_at", "started_at", "updated_at",
                  "seconds", "resumed_from")

    @classmethod
    def job_ref(cls, job, *, full=False):
        ref = {key: job.get(key) for key in cls.REGISTERED}
        ref["artifact_count"] = len(job.get("artifact_ids") or [])
        ref["log_already_saved"] = bool((job.get("cleanup") or {}).get("logs_sha256"))
        missing = [key for key in cls.REGISTERED if key not in job]
        if missing:
            ref["unknown_fields"] = missing
        if full:
            ref["blocker"] = job.get("blocker")
            ref["exit_code"] = job.get("exit_code")
        return ref

    @classmethod
    def ref_summary(cls, job):
        """模型回执里的最小引用：能定位和称呼这个作业，不重复完整登记项。"""
        return {key: job.get(key) for key in ("job_id", "readable_name", "status", "request_turn_id")}

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
        if isinstance(result.get('build_requirements'),dict):
            result['build_requirements'] = {k:v for k,v in result['build_requirements'].items() if k != 'projects'}
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
               source_build_only=False, source_inspection=None, runtime_profile=None, reuse_equivalent=False):
        domain_profile = runtime_profile in {"scientific", "imagery", "orthomosaic"}
        if type(reuse_equivalent) is not bool or (reuse_equivalent and not domain_profile):
            raise Failure("SCHEMA_INVALID", "相同配方复用仅用于固定科学作业")
        if runtime_profile not in {None, "scientific", "imagery", "orthomosaic"}:
            raise Failure("SCHEMA_INVALID", "未知执行环境配置")
        if runtime_profile and (job_kind != "code.execute" or environment_artifact_id or source_recipe):
            raise Failure("SCHEMA_INVALID", "科学环境不能混用通用依赖准备或环境产物")
        if job_kind not in {"code.execute", "environment.prepare"}:
            raise Failure("SCHEMA_INVALID", "未知受限作业类型")
        if source_recipe and job_kind != "environment.prepare":
            raise Failure("SCHEMA_INVALID", "源码构建仅用于依赖准备作业")
        if type(source_build_only) is not bool or (source_build_only and not source_recipe):
            raise Failure('SCHEMA_INVALID', '仅构建用途需实际源码配方')
        if source_inspection is not None and (source_inspection not in ('static','dynamic') or
            not source_recipe or source_build_only or job_kind != 'environment.prepare'):
            raise Failure('SCHEMA_INVALID','源码检查用途需源码准备作业且与仅构建互斥')
        if not code or len(code.encode()) > 65536:
            raise Failure("SCHEMA_INVALID", "代码须为 1–65536 字节的 Python 文本")
        if seconds < 1 or seconds > self.config.max_code_seconds:
            raise Failure("RESOURCE_EXHAUSTED", "作业时限超出配置", 422)
        mounts = []
        if domain_profile:
            input_refs, scientific_artifacts = self.resources.partition_refs(input_refs, task_id)
            input_artifact_ids = list(dict.fromkeys([*(input_artifact_ids or []), *scientific_artifacts]))
        for ref in dict.fromkeys(input_refs):
            source = self.resources.resolve(ref, task_id=task_id)
            # Directory snapshots and link-safe bulk mounting require a later worker.
            if not native_path(source).is_file() or native_path(source).is_symlink() or "," in str(source):
                raise Failure("PRECONDITION_MISSING", "通用代码作业当前仅支持无链接的单文件输入。目录授权用于浏览，不能挂载到代码作业；请用户将实际文件分别登记为只读资源，并通过任务规范修订更换输入。不要重试同一个目录标识。文件路径也不能含逗号。", 422)
            mounts.append({"resource_id": ref, "source": str(source), "target": f"/inputs/{ref}",
                           "version": version(source)})
        for ref in dict.fromkeys(input_artifact_ids or []):
            if domain_profile:
                self.resources.partition_refs([ref], task_id)
                artifact = self.resources.artifact(ref, task_id)
            elif job_kind == 'environment.prepare' and self.input_artifact_validator:
                artifact = self.input_artifact_validator(task_id, revision, ref)
            else:
                raise Failure("PERMISSION_DENIED", "产物输入仅用于经核验的离线依赖准备", 403)
            source = Path(artifact["locator"])
            if native_path(source).is_symlink() or "," in str(source):
                raise Failure("PRECONDITION_MISSING", "依赖产物路径不支持链接或逗号", 422)
            mounts.append({"artifact_id": ref, "source": str(source), "target": f"/inputs/{ref}",
                           "version": version(source), **({'sha256': artifact['sha256']} if runtime_profile else {})})
        environment = None
        if environment_artifact_id:
            if job_kind != "code.execute" or not self.environment_resolver:
                raise Failure("PRECONDITION_MISSING", "当前执行器没有环境解析器", 422)
            environment = self.environment_resolver(task_id, revision, environment_artifact_id)
        with self.store.transaction() as db:
            existing = db.execute("SELECT payload FROM jobs WHERE action_id=?", (action_id,)).fetchone()
            if existing:
                prior = json.loads(existing["payload"])
                return {"job": self.public(prior), "job_ref": self.ref_summary(prior)}
            state = self.store.read_task(db, task_id)
            if state["spec_revision"] != revision or state["pause_requested"] or state["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "任务规范或控制状态不允许新作业", 409)
            self.store.authorize_capability(db, state, job_kind)
            image_reference = self.config.code_image
            if domain_profile:
                image_key = {"scientific": "science_image", "imagery": "imagery_image", "orthomosaic": "orthomosaic_image"}[runtime_profile]
                image_reference = state["config_snapshot"].get(image_key)
                if not getattr(self.config, image_key) or not image_reference:
                    raise Failure("PRECONDITION_MISSING", "任务未冻结可用科学镜像；请明确配置并创建新任务", 422)
            if reuse_equivalent:
                equivalent = db.execute("SELECT payload FROM jobs WHERE task_id=? AND revision=? "
                    "AND json_extract(payload,'$.kind')='code.execute' "
                    "AND json_extract(payload,'$.image_reference')=? AND json_extract(payload,'$.code_sha256')=?",
                    (task_id, revision, image_reference, hashlib.sha256(code.encode()).hexdigest())).fetchone()
                if equivalent:
                    original = json.loads(equivalent["payload"])
                    if original["mounts"] != mounts:
                        raise Failure("VERSION_CONFLICT", "科学输入版本已变化，请明确保存任务修订；不复用旧结果或自动重算", 409)
                    return {"job": self.public(original), "reused_from_job": original["job_id"],
                            "original_producer_action_id": original["action_id"],
                            "job_ref": self.ref_summary(original)}
            if source_recipe:
                self.store.authorize_capability(db, state, "code.execute")
            job_id = uid()
            assignment = db.execute("SELECT id FROM assignments WHERE json_extract(payload,'$.run_id')=(SELECT turn_id FROM actions WHERE id=?)", (action_id,)).fetchone()
            # 来源消息与调用/尝试从动作记录复制：作业要能回答「是谁、在哪条消息上
            # 发起的」，不能让用户回头找 action_id。
            source = db.execute("SELECT a.turn_id,a.call_id,(SELECT id FROM attempts WHERE action_id=a.id "
                "ORDER BY rowid DESC LIMIT 1) AS attempt_id FROM actions a WHERE a.id=?", (action_id,)).fetchone()
            job = {"schema_version": 1, "job_id": job_id, "task_id": task_id,
                "kind": job_kind, "image_reference": image_reference,
                "tmpfs_bytes": 1024*1024*1024 if job_kind == "environment.prepare" else 67108864,
                "assignment_id": assignment["id"] if assignment else None,
                "action_id": action_id, "spec_revision": revision, "status": "queued",
                "container_name": "forestry-job-" + job_id, "container_id": None,
                "image_id": None, "code": code, "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "mounts": mounts, "seconds": seconds, "directory": str(self.config.workspace_root /
                    "tasks" / task_id / "jobs" / job_id), "created_at": now(), "started_at": None,
                "updated_at": now(), "cancel_requested": False, "exit_code": None,
                "artifact_ids": [], "blocker": None,
                "request_turn_id": source["turn_id"] if source else None,
                "call_id": source["call_id"] if source else None,
                "attempt_id": source["attempt_id"] if source else None,
                "logical_activity": assignment["id"] if assignment else (source["turn_id"] if source else None),
                "readable_name": self.readable_name(job_kind, runtime_profile, source_inspection),
                "resumed_from": None, "registered_at": now()}
            if environment:
                job["environment"] = environment
                job["environment_tmpfs_bytes"] = 1024*1024*1024
            if runtime_profile:
                job['runtime_profile'] = runtime_profile
            if environment_recipe is not None:
                job["environment_recipe"] = environment_recipe
            if source_recipe:
                job["source_recipe"] = source_recipe
                job["build_tmpfs_bytes"] = 1024*1024*1024
                if source_build_only:
                    job['source_build_only'] = True
                if source_inspection:
                    job['source_inspection'] = source_inspection
            job['resource_request'] = self.resource_request(job, state)
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)",
                (job_id, task_id, action_id, revision, "queued", encode(job), now()))
            self.store.event(db, task_id, "job.queued", self.public(job))
        return {"job": self.public(job), "job_ref": self.ref_summary(job)}

    def resource_request(self, job, state):
        limits = state['config_snapshot'].get('execution_limits', self.config.execution_limits())
        temporary = sum(job.get(key, 0) for key in ('tmpfs_bytes', 'environment_tmpfs_bytes', 'build_tmpfs_bytes'))
        return ResourceRequest.model_validate({**limits, 'temporary_bytes': temporary}).model_dump()

    def wait_resources(self, job, admission):
        blocker = {'code': 'RESOURCE_WAIT', 'message': '等待资源：' + ', '.join(admission['missing'])}
        previous = job.get('admission', {})
        # Sensor timestamps and ordinary free-space fluctuations are not new work.
        changed = any(previous.get(key) != admission.get(key) for key in ('decision', 'missing', 'required', 'policy'))
        if changed or job.get('blocker') != blocker:
            job['admission'], job['blocker'] = admission, blocker
            self.save(job, 'job.resource_wait')

    def outputs(self, job):
        output_root = Path(job['directory']) / 'output'
        walk_root = native_path(output_root)
        if walk_root.is_symlink():
            raise Failure('PERMISSION_DENIED', '作业输出不接受符号链接', 403)
        files, total, count = [], 0, 0
        def walk_error(error):
            raise error
        for directory, folders, names in os.walk(walk_root, followlinks=False, onerror=walk_error):
            for name in folders + names:
                path = output_root / Path(directory).relative_to(walk_root) / name
                count += 1
                if count > 1000:
                    raise Failure('RESOURCE_EXHAUSTED', '作业产物数量超出上限', 422)
                if native_path(path).is_symlink():
                    raise Failure('PERMISSION_DENIED', '作业输出不接受符号链接', 403)
                if native_path(path).is_file():
                    files.append(path)
                    total += native_path(path).stat().st_size
                elif not native_path(path).is_dir():
                    raise Failure('PERMISSION_DENIED', '作业输出仅接受普通文件或目录', 403)
        return files, total

    def output_violation(self, job, *, check_disk=True):
        try:
            _, total = self.outputs(job)
            if total > job['resource_request']['output_bytes']:
                return {'code': 'RESOURCE_EXHAUSTED', 'message': '作业总输出超过预算',
                        'output_bytes': total, 'limit_bytes': job['resource_request']['output_bytes']}
            free = shutil.disk_usage(self.config.workspace_root).free if check_disk else None
            if check_disk and free < self.config.code_disk_reserve_bytes:
                return {'code': 'RESOURCE_EXHAUSTED', 'message': '工作盘空闲空间已低于保留量',
                        'disk_free_bytes': free, 'reserve_bytes': self.config.code_disk_reserve_bytes}
        except Failure as error:
            return {'code': error.code, 'message': str(error)}
        return None

    def request_cancel(self, job_id: str, task_id: str):
        job = self.read(job_id, task_id)
        if job["status"] not in TERMINAL:
            job["cancel_requested"] = True
            self.save(job, "job.cancel_requested")
        return self.public(job)

    def active(self, task_id):
        """任务下尚未到达终态的作业；取消整体活动时按它逐个登记取消意图。"""
        with self.store.transaction() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM jobs WHERE task_id=? AND status NOT IN ('succeeded','failed','cancelled','timed_out') "
                "ORDER BY updated_at", (task_id,))]

    def cancel_active(self, task_id, reason="用户取消活动"):
        """给所属作业登记取消意图；实际停止仍由执行器确认后才记终态。"""
        results = []
        for job in self.active(task_id):
            try:
                results.append(self.request_cancel(job["job_id"], task_id))
            except Failure as error:
                results.append({"job_id": job["job_id"], "error": {"code": error.code, "message": str(error)},
                                "reason": reason})
        return results

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
                if job.get('runtime_profile') in {'scientific', 'imagery', 'orthomosaic'}:
                    artifact = self.resources.artifact(mount['artifact_id'], job['task_id'])
                    if artifact['sha256'] != mount['sha256']:
                        raise Failure('VERSION_CONFLICT', '科学输入产物校验和已变化', 409)
                elif self.input_artifact_validator:
                    artifact = self.input_artifact_validator(job["task_id"], job["spec_revision"], mount["artifact_id"])
                else:
                    raise Failure("PRECONDITION_MISSING", "恢复缺少依赖产物核验器", 422)
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
                profile = None
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
                    if 'resource_request' not in job:
                        job['resource_request'] = self.resource_request(job, state)
                        self.save(job, 'job.resource_request_frozen')
                    profile = self.resource_probe(self.config)
                    admission = assess(job['resource_request'], profile, self.config)
                    if admission['decision'] == 'wait':
                        self.wait_resources(job, admission)
                        continue
                    job['admission'], job['blocker'] = admission, None
                    image = self.docker.call("image", "inspect", job.get("image_reference", self.config.code_image), "--format", "{{.Id}}")
                    if image["code"] != 0:
                        raise Failure("DEPENDENCY_MISSING", "隔离运行镜像未准备，不自动联网下载", 422)
                    job["image_id"] = image["stdout"].strip()
                    if job.get("environment") and job["image_id"] != job["environment"]["image_id"]:
                        raise Failure("VERSION_CONFLICT", "运行镜像与依赖准备镜像身份不一致", 409)
                    directory = Path(job["directory"])
                    native_path(directory / "output").mkdir(parents=True, exist_ok=True)
                    prepared = execution_code(job["code"], job["environment"]) if job.get("environment") else job["code"]
                    prepared = limited_code(prepared, job['resource_request']['output_bytes'])
                    job["prepared_code_sha256"] = hashlib.sha256(prepared.encode()).hexdigest()
                    native_path(directory / "run.py").write_bytes(prepared.encode("utf-8"))
                    self.verify_inputs(job)
                    self.verify_environment(job)
                    job["status"] = "creating"
                    self.save(job, "job.create_intent")
                    active = True
                container = self.docker.inspect(job["container_name"])
                if 'resource_request' not in job:
                    # Historical running jobs keep their actual container limits.
                    # Do not invent or impose a new output limit on an old run.
                    job['resource_request'] = self.resource_request(job, state)
                    job['resource_request']['output_bytes'] = None
                    job['legacy_container_limits_unverified'] = True
                    self.save(job, 'job.legacy_resource_snapshot')
                if job['status'] == 'creating' and not stopping and not state['pause_requested']:
                    admission = assess(job['resource_request'], profile or self.resource_probe(self.config), self.config)
                    if admission['decision'] == 'wait':
                        self.wait_resources(job, admission)
                        continue
                    job['admission'], job['blocker'] = admission, None
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
                        "--memory", str(job['resource_request']['ram_bytes']), "--memory-swap", str(job['resource_request']['ram_bytes']),
                        "--cpus", str(job.get('admission', {}).get('effective_cpu_threads', job['resource_request']['cpu_threads'])), "--user", "65534:65534",
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
                    if job.get('runtime_profile') in {'imagery', 'orthomosaic'}:
                        args.extend(["--entrypoint", "python3" if job['runtime_profile'] == 'orthomosaic' else "python"])
                        result = self.docker.call(*args, job["image_id"], "-I", "-B", "/run.py")
                    else:
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
                    violation = job.get('resource_stop_requested')
                    if not stopping and not expired and not violation and job['resource_request']['output_bytes'] is not None:
                        violation = self.output_violation(job)
                        if violation:
                            job['resource_stop_requested'] = violation
                            self.save(job, 'job.resource_stop_intent')
                    if stopping or expired or violation:
                        self.docker.call("kill", job["container_id"])
                        confirmed = self.docker.inspect(job["container_name"])
                        if confirmed is None:
                            raise Failure('RECONCILIATION_REQUIRED', '停止后容器身份无法核验', 409)
                        self.matches(job, confirmed)
                        if confirmed["State"]["Running"]:
                            raise Failure("RECONCILIATION_REQUIRED", "容器仍运行，不能确认停止", 409)
                        job["status"] = "cancelled" if stopping else "timed_out" if expired else "failed"
                        job['blocker'] = violation if job['status'] == 'failed' else None
                        job["exit_code"] = confirmed["State"]["ExitCode"]
                        self.save(job, "job.terminal")
                    continue
                job["exit_code"] = observed["ExitCode"]
                violation = job.get('resource_stop_requested')
                if not stopping and not violation and job['resource_request']['output_bytes'] is not None:
                    violation = self.output_violation(job, check_disk=False)
                outcome = "cancelled" if stopping else "failed" if job["exit_code"] or violation else "succeeded"
                # A nonzero exit can follow a completed calculation. Preserve
                # its bounded files for inspection without changing the failed
                # execution into success. Unsafe/over-budget and cancelled
                # outputs retain the existing no-publication behavior.
                status = "collecting" if outcome == "succeeded" or (outcome == "failed" and not violation) else outcome
                if status == "collecting":
                    job["collection_outcome"] = outcome
                if job['status'] != status:
                    job['status'], job['blocker'] = status, violation if status == 'failed' else None
                    self.save(job, "job.execution_observed")
                if job["status"] == "collecting":
                    # Keep a recoverable boundary until every output has been frozen.
                    files, total = self.outputs(job)
                    with self.store.transaction() as db:
                        sealed = {json.loads(row['payload'])['relative_path'] for row in db.execute(
                            'SELECT payload FROM artifacts WHERE task_id=? AND json_extract(payload,\'$.producer_action_id\')=?',
                            (job['task_id'], job['action_id']))}
                    prefix = f"assignments/{job['assignment_id']}/" if job.get("assignment_id") else ""
                    def relative(path, prefix=prefix, job=job):
                        return prefix + 'jobs/' + job['job_id'] + '/' + path.relative_to(Path(job['directory']) / 'output').as_posix()
                    remaining = sum(native_path(path).stat().st_size for path in files if relative(path) not in sealed)
                    free = shutil.disk_usage(self.config.workspace_root).free
                    required = remaining + self.config.code_disk_reserve_bytes + 16 * 1024 * 1024
                    if free < required:
                        self.wait_resources(job, {'decision': 'wait', 'missing': ['artifact_copy_disk'],
                            'required': {'disk_bytes': required}, 'profile': {'observed_at': now(), 'workspace_disk_free': free},
                            'policy': {'disk_reserve_bytes': self.config.code_disk_reserve_bytes}})
                        continue
                    job['blocker'] = None
                    for path in files:
                        if native_path(path).is_symlink():
                            raise Failure("PERMISSION_DENIED", "作业输出不接受符号链接", 403)
                        if native_path(path).is_file():
                            artifact = self.resources.import_file(job["task_id"], job["action_id"], path,
                                relative(path))
                            if artifact["artifact_id"] not in job["artifact_ids"]:
                                job["artifact_ids"].append(artifact["artifact_id"])
                    job["status"] = job["collection_outcome"]
                    job["partial_outputs"] = job["status"] == "failed" and bool(job["artifact_ids"])
                    job['output_bytes'] = total
                    self.save(job, "job.terminal")
            except (Failure, OSError, ValueError, KeyError) as error:
                job["blocker"] = {"code": getattr(error, "code", "EXECUTOR_ERROR"), "message": str(error)[:1500]}
                self.save(job, "job.blocked")
        # A task cancellation is terminal only after every effect has settled.
        with self.store.transaction() as db:
            if self.settle_obsolete_assignments:
                for row in db.execute("SELECT t.id FROM tasks t WHERE EXISTS (SELECT 1 FROM assignments a WHERE a.task_id=t.id AND a.status IN ('queued','waiting') AND json_extract(a.payload,'$.spec_revision') != json_extract(t.state,'$.spec_revision'))").fetchall():
                    self.settle_obsolete_assignments(row["id"], db)
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
        # A bounded maintenance pass; it neither submits jobs nor invokes a model.
        if not active:
            with self.store.transaction() as db:
                row=db.execute("SELECT payload FROM jobs WHERE status IN ('succeeded','failed','cancelled','timed_out') "
                    "AND json_extract(payload,'$.cleanup.done') IS NOT 1 "
                    "AND (json_extract(payload,'$.cleanup.retry_after') IS NULL OR json_extract(payload,'$.cleanup.retry_after')<?) "
                    "ORDER BY updated_at LIMIT 1",(now(),)).fetchone()
            if row:
                try:self.cleanup_terminal(json.loads(row[0]))
                except (Failure,OSError,ValueError,KeyError) as error:
                    job=self.read(json.loads(row[0])['job_id'])
                    from datetime import timedelta
                    job['cleanup']={**job.get('cleanup',{}),'error':str(error)[:500],
                        'retry_after':(datetime.now(UTC)+timedelta(minutes=5)).isoformat()}
                    self.save(job,'job.cleanup_pending')

    def cleanup_terminal(self, job, *, discard=False):
        """Archive bounded logs, verify sealed outputs, remove only our stopped container."""
        with self.cleanup_lock:
            job=self.read(job['job_id'])
            if job['status'] not in TERMINAL or job.get('cleanup',{}).get('done'):return False
            expected=self.config.workspace_root.resolve()/'tasks'/job['task_id']/'jobs'/job['job_id']
            directory=Path(job['directory'])
            if directory!=expected or directory.resolve()!=expected:
                raise Failure('PERMISSION_DENIED','作业清理目录不符合归属，未执行删除',409)
            for ref in (job.get('artifact_ids',[]) if not discard else []):
                artifact=self.resources.artifact(ref,job['task_id'])
                if artifact['producer_action_id']!=job['action_id']:
                    raise Failure('RECONCILIATION_REQUIRED','产物生产身份不匹配，保留作业目录',409)
            cleanup=job.get('cleanup',{})
            container=self.docker.inspect(job['container_name'])
            if container:
                self.matches(job,container)
                if container['State']['Running']:
                    raise Failure('RECONCILIATION_REQUIRED','终态记录对应容器仍在运行，拒绝清理',409)
                if not discard and not cleanup.get('logs_sha256'):
                    captured=self.docker.call('logs','--tail','200',container['Id'])
                    if captured['code']!=0:
                        raise Failure('EXECUTOR_UNAVAILABLE','无法归档容器日志，保留容器',503,True)
                    logs={'text':captured['stdout'],'stderr':captured['stderr'],'bounded':True,
                        'window':captured.get('window','tail'),'stdout_truncated':captured.get('stdout_truncated',False),
                        'stderr_truncated':captured.get('stderr_truncated',False)}
                    data=encode(logs).encode()
                    native_path(directory).mkdir(parents=True,exist_ok=True)
                    native_path(directory/'logs.json').write_bytes(data)
                    cleanup={**cleanup,'logs_sha256':hashlib.sha256(data).hexdigest(),'container_id':container['Id']}
                    job['cleanup']=cleanup
                    self.save(job,'job.cleanup_intent')
                removed=self.docker.call('rm',container['Id'])
                if removed['code']!=0:
                    raise Failure('EXECUTOR_UNAVAILABLE','容器清理未确认，将稍后重试',503,True)
            elif not discard and job.get('container_id') and not cleanup.get('logs_sha256'):
                raise Failure('RECONCILIATION_REQUIRED','容器丢失且未归档日志，不将其标记为已清理',409)
            output=directory/'output'
            if output.resolve()!=output or not output.resolve().is_relative_to(expected):
                raise Failure('PERMISSION_DENIED','作业输出目录发生重定向，未执行递归清理',409)
            # Worker 自己的日志（例如 ODM 的 processing.log）不在容器标准输出里，
            # 必须在删除可再生产物前先保存，否则「删掉目录」等于销毁唯一诊断材料。
            if not discard and not cleanup.get('output_logs_sha256'):
                worker = self.worker_logs(output)
                if worker['files']:
                    data=encode(worker).encode()
                    native_path(directory).mkdir(parents=True,exist_ok=True)
                    native_path(directory/'output-logs.json').write_bytes(data)
                    cleanup={**cleanup,'output_logs_sha256':hashlib.sha256(data).hexdigest(),
                        'output_log_files':[item['name'] for item in worker['files']]}
                    job['cleanup']=cleanup
                    self.save(job,'job.output_logs_archived')
            used_as_source=any(Path(source['locator']).is_relative_to(output) for source in self.resources.all())
            if output.exists() and not used_as_source:shutil.rmtree(native_path(output, force=True))
            job['cleanup']={**cleanup,'done':True,'completed_at':now(),'output_retained_as_source':used_as_source}
            self.save(job,'job.cleaned')
            return True

    def worker_logs(self, output, *, limit_files=8, limit_bytes=131072):
        """收集 Worker 写在输出目录里的日志，供删除目录前保存。

        只读普通文件、不接受链接、有界（文件数/总字节）；内容截断如实标注，不把
        截断后的文本当成完整日志。
        """
        result = {'files': [], 'bounded': True, 'limit_files': limit_files, 'limit_bytes': limit_bytes}
        if not output.exists():
            return result
        root = native_path(output)
        total = 0
        for directory, folders, names in os.walk(root, followlinks=False):
            folders.sort()
            for name in sorted(names):
                if not (name.endswith('.log') or name.endswith('.txt')):
                    continue
                path = Path(directory) / name
                if native_path(path).is_symlink() or not native_path(path).is_file():
                    continue
                if len(result['files']) >= limit_files or total >= limit_bytes:
                    result['truncated'] = True
                    return result
                data = native_path(path).read_bytes()
                take = data[:max(0, limit_bytes - total)]
                total += len(take)
                result['files'].append({'name': str(path.relative_to(root)).replace('\\', '/'),
                    'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data),
                    'truncated': len(take) < len(data),
                    'text': take.decode('utf-8', errors='replace')})
        return result

    def cleanup_batch(self,limit):
        with self.store.transaction() as db:
            jobs=[json.loads(row[0]) for row in db.execute("SELECT payload FROM jobs WHERE status IN "
                "('succeeded','failed','cancelled','timed_out') AND json_extract(payload,'$.cleanup.done') IS NOT 1 "
                "ORDER BY updated_at LIMIT ?",(limit,))]
        cleaned=0;pending=[]
        for job in jobs:
            try:cleaned+=bool(self.cleanup_terminal(job))
            except (Failure,OSError,ValueError,KeyError) as error:
                pending.append({'job_id':job['job_id'],'message':str(error)[:300]})
        return {'cleaned':cleaned,'pending':pending}

    def logs(self, job_id, task_id):
        job = self.read(job_id, task_id)
        result = self._container_logs(job, task_id)
        worker = self.archived_worker_logs(job, task_id)
        return {**result, **({'worker': worker} if worker else {})}

    def archived_worker_logs(self, job, task_id):
        """读回清理前保存的 Worker 日志；哈希不符即视为不可信，不返回内容。"""
        expected = (job.get('cleanup') or {}).get('output_logs_sha256')
        if not expected:
            return None
        path = self.config.workspace_root.resolve()/'tasks'/task_id/'jobs'/job['job_id']/'output-logs.json'
        if path.resolve() != path or not path.exists() or path.stat().st_size > 512*1024:
            raise Failure('VERIFICATION_FAILED','归档 Worker 日志路径或大小不合法',409)
        data = native_path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise Failure('VERIFICATION_FAILED','归档 Worker 日志校验失败',409)
        return json.loads(data)

    def _container_logs(self, job, task_id):
        if job.get('cleanup',{}).get('logs_sha256'):
            path=self.config.workspace_root.resolve()/'tasks'/task_id/'jobs'/job['job_id']/'logs.json'
            if path.resolve()!=path or path.stat().st_size>512*1024:
                raise Failure('VERIFICATION_FAILED','归档日志路径或大小不合法',409)
            data=native_path(path).read_bytes()
            if hashlib.sha256(data).hexdigest()!=job['cleanup']['logs_sha256']:
                raise Failure('VERIFICATION_FAILED','归档日志校验失败',409)
            return json.loads(data)
        if not job["container_id"]:
            return {"text": "", "bounded": True}
        container = self.docker.inspect(job["container_name"])
        if not container:
            raise Failure("RECONCILIATION_REQUIRED", "日志容器丢失", 409)
        self.matches(job, container)
        result = self.docker.call("logs", "--tail", "200", job["container_id"])
        return {"text": result["stdout"], "stderr": result["stderr"], "bounded": True,
                "window": result.get("window", "tail"),
                "stdout_truncated": result.get("stdout_truncated", False),
                "stderr_truncated": result.get("stderr_truncated", False)}

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
        if job.get("partial_outputs"):
            result["output_review"] = {
                "execution_status": "failed", "answers_task": "not_assessed",
                "message": "失败作业已保存产物，artifact_ids可读回核对。先检查末尾错误和已有文件；正确结果可用于报告，不因日志失败重复计算。文件存在不代表作业或目标通过。"}
        if job["status"] == "succeeded" and job.get("kind") == "environment.prepare" and self.environment_resolver:
            if job.get('source_inspection'):
                if not self.source_requirements_resolver:
                    raise Failure('VERIFICATION_FAILED','源码检查缺少生产者核验',422)
                result['build_requirements'] = self.source_requirements_resolver(task_id,job['spec_revision'],job['action_id'])
                return result
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
