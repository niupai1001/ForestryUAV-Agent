"""Public release acquisition, without importing, installing or unpacking packages.

The native model chooses exact versions/files; pip still resolves and verifies
the full installed dependency set in the existing offline container job.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, model_validator

from .contracts import Boundary, Failure
from .environments import MAX_WHEEL_BYTES, WHEEL_NAME
from .network import MAX_BYTES, PublicHTTP, url_parts
from .resources import native_path, version
from .store import encode


class WheelRequest(Boundary):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,99}$")
    filename: str = Field(min_length=1, max_length=244)

    @model_validator(mode="after")
    def canonical(self):
        if not WHEEL_NAME.fullmatch(self.filename):
            raise ValueError("仅接受明确的单个wheel文件名，不接受URL、路径或源码包")
        self.name = re.sub(r"[-_.]+", "-", self.name).lower()
        return self


class Acquisition:
    def __init__(self, engine, http=None):
        self.engine, self.store = engine, engine.store
        self.http = http or PublicHTTP()

    def acquire(self, deps, action_id, wheels, source: Literal["pypi", "tuna"] = "pypi", approval_id=None,
                resolution_lock_artifact_id=None):
        wheels = [WheelRequest.model_validate(w).model_dump() for w in wheels]
        if source not in {"pypi", "tuna"} or not 1 <= len(wheels) <= 32:
            raise Failure("SCHEMA_INVALID", "获取需1–32个精确wheel请求及支持的来源")
        if len({w["name"] for w in wheels}) != len(wheels) or len({w["filename"] for w in wheels}) != len(wheels):
            raise Failure("SCHEMA_INVALID", "同次获取不可重复包名或文件名")
        resolved = {}
        resolution = None
        if resolution_lock_artifact_id is not None:
            resolution, lock_artifact = self.engine.resolution.lock(deps, resolution_lock_artifact_id)
            requested = {encode(w) for w in wheels}
            selected = {encode({k: w[k] for k in ['name', 'version', 'filename']}) for w in resolution['selected']}
            if requested != selected:
                raise Failure('VERSION_CONFLICT', '获取集合须与已确认解析锁的完整wheel集合相同', 409)
            resolved = {w['filename']: w['sha256'] for w in resolution['selected']}
        state = self.store.task(deps.task_id)
        sources = [{"resource_id": ref, "version": version(self.engine.resources.resolve(ref, task_id=deps.task_id))}
            for ref in state["input_refs"]]
        origin = "https://files.pythonhosted.org" if source == "pypi" else "https://pypi.tuna.tsinghua.edu.cn"
        urls = [f"https://pypi.org/pypi/{w['name']}/{w['version']}/json" for w in wheels]
        request = {"kind": "environment.acquire", "value": encode({"wheels": wheels, "source": source}),
            "url": urls[0], "metadata_urls": urls, "destinations": ["https://pypi.org", origin],
            "spec_revision": deps.revision, "sources": sources, "method": "GET",
            "max_bytes": MAX_WHEEL_BYTES, "max_metadata_bytes": MAX_BYTES, "seconds": 180,
            "package_execution": False}
        if resolution:
            request['resolution_lock_artifact_id'] = resolution_lock_artifact_id
            request['resolution_lock_sha256'] = lock_artifact['sha256']
        admission = self.engine.network.admit(deps, action_id, request, hashlib.sha256(encode(request).encode()).hexdigest(), approval_id)
        if isinstance(admission, dict):
            return admission
        deadline, total, records = time.monotonic() + 180, 0, []
        directory = self.engine.resources.task_root(deps.task_id) / "dependencies" / action_id
        def guard(url, expected):
            if url_parts(url)[0] != expected:
                raise Failure("PERMISSION_DENIED", "依赖下载不允许改写已核验的精确URL", 403)
            if time.monotonic() >= deadline:
                raise Failure("RESOURCE_EXHAUSTED", "依赖获取总时限已用尽", 422)
            with self.store.transaction() as db:
                current = self.store.read_task(db, deps.task_id)
                self.engine.roles.authorize(db, deps, "environment.acquire", {}, check_budget=False)
                if current["spec_revision"] != deps.revision or current["pause_requested"] or current["cancellation_requested"]:
                    raise Failure("EXECUTION_STOPPED", "控制状态或修订阻止依赖获取", 409)
            if admission:
                grant = next(a for a in self.engine.network.list(deps.task_id) if a["approval_id"] == admission)
                if grant["status"] != "consumed" or grant["consumed_action_id"] != action_id or datetime.fromisoformat(grant["expires_at"]) <= datetime.now(UTC):
                    raise Failure("PERMISSION_DENIED", "依赖获取授权已失效", 403)
            for item in sources:
                if version(self.engine.resources.resolve(item["resource_id"], task_id=deps.task_id)) != item["version"]:
                    raise Failure("VERSION_CONFLICT", "获取前输入版本变化", 409)
            if resolution:
                self.engine.resources.artifact(resolution_lock_artifact_id, deps.task_id)
                self.engine.resources.artifact(resolution['target_artifact_id'], deps.task_id)
        for wheel, url in zip(wheels, urls, strict=True):
            metadata = self.http.get(url, guard=lambda value, url=url: guard(value, url),
                allowed_origins=["https://pypi.org"], deadline=deadline)
            guard(url, url)
            raw = metadata["body"]
            if len(raw) > MAX_BYTES:
                raise Failure("RESOURCE_EXHAUSTED", "依赖元数据超过2 MiB", 422)
            try:
                release = json.loads(raw)
                if release["info"]["version"] != wheel["version"] or re.sub(r"[-_.]+", "-", release["info"]["name"]).lower() != wheel["name"]:
                    raise ValueError("release mismatch")
                entry = next(e for e in release["urls"] if e["filename"] == wheel["filename"] and e["packagetype"] == "bdist_wheel")
                official, _, _, official_origin = url_parts(entry["url"])
                parts = urlsplit(entry["url"])
                expected, size = entry["digests"]["sha256"], entry["size"]
                if (entry["yanked"] or official_origin != "https://files.pythonhosted.org" or not parts.path.startswith("/packages/") or
                    parts.query or parts.fragment or unquote(parts.path.rsplit("/", 1)[-1]) != wheel["filename"] or
                    not re.fullmatch(r"[0-9a-f]{64}", expected) or type(size) is not int or size <= 0):
                    raise ValueError("unsafe release")
            except (ValueError, KeyError, TypeError, StopIteration) as error:
                raise Failure("PRECONDITION_MISSING", "官方元数据缺少匹配的未撤回wheel、大小或SHA-256", 422) from error
            if resolved and resolved[wheel['filename']] != expected:
                raise Failure('VERSION_CONFLICT', '官方wheel哈希已偏离解析锁，须重新解析/审批', 409)
            total += size
            if total > MAX_WHEEL_BYTES or shutil.disk_usage(self.engine.config.workspace_root).free < total + 2**30:
                raise Failure("RESOURCE_EXHAUSTED", "依赖总量超128 MiB或磁盘预留不足", 422)
            native_path(directory).mkdir(parents=True, exist_ok=True)
            snapshot = directory / (wheel["name"] + "-metadata.json")
            native_path(snapshot).write_bytes(raw)
            metadata_artifact = self.engine.resources.import_file(deps.task_id, action_id, snapshot, f"dependencies/{action_id}/{snapshot.name}")
            selected = official.replace("https://files.pythonhosted.org", origin, 1)
            downloaded = self.http.get(selected, guard=lambda value, selected=selected: guard(value, selected),
                allowed_origins=[origin], max_bytes=size, deadline=deadline)
            guard(selected, selected)
            data = downloaded["body"]
            if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
                raise Failure("VERIFICATION_FAILED", "下载的wheel大小或SHA-256与官方元数据不符", 422)
            staging = directory / wheel["filename"]
            native_path(staging).write_bytes(data)
            artifact = self.engine.resources.import_file(deps.task_id, action_id, staging, f"dependencies/{action_id}/{wheel['filename']}")
            records.append({**wheel, "artifact_id": artifact["artifact_id"], "sha256": expected, "size_bytes": size,
                "metadata_url": url, "metadata_artifact_id": metadata_artifact["artifact_id"], "metadata_sha256": hashlib.sha256(raw).hexdigest(),
                "official_url": official, "download_url": selected, "source": source, "hops": downloaded["hops"],
                "observed_at": downloaded["observed_at"]})
        guard(urls[0], urls[0])
        lock = self.engine.resources.write(deps.task_id, action_id, f"dependencies/{action_id}/acquisition-lock.json",
            json.dumps({"schema_version": 1, "kind": "public_wheel_acquisition", "wheels": records,
                "package_execution": False, "hash_authority": "official_pypi_metadata_not_independent_signature",
                "scientific_accuracy": "not_assessed"}, ensure_ascii=False, indent=2))
        return {"wheel_artifact_ids": [w["artifact_id"] for w in records], "wheels": records,
            "lock_artifact_id": lock["artifact_id"], "package_execution": False,
            "continuation": "将wheel_artifact_ids传给prepare_environment的wheel_artifact_ids；只有容器安装成功才有environment.zip。"}
