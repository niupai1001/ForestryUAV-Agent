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
from .environments import MAX_WHEEL_BYTES, SOURCE_NAME, WHEEL_NAME
from .network import MAX_BYTES, PublicHTTP, url_parts
from .resources import native_path, version
from .store import encode


class PackageRequest(Boundary):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,99}$")
    filename: str = Field(min_length=1, max_length=244)

    @model_validator(mode="after")
    def canonical(self):
        if not (WHEEL_NAME.fullmatch(self.filename) or SOURCE_NAME.fullmatch(self.filename)):
            raise ValueError("需明确的wheel/.tar.gz/.zip发行文件名，不接受URL或路径")
        self.name = re.sub(r"[-_.]+", "-", self.name).lower()
        return self


class WheelRequest(PackageRequest):
    @model_validator(mode="after")
    def wheel_only(self):
        if not WHEEL_NAME.fullmatch(self.filename):
            raise ValueError("wheels仅接受wheel文件名；源码使用source_packages")
        return self


class Acquisition:
    def __init__(self, engine, http=None):
        self.engine, self.store = engine, engine.store
        self.http = http or PublicHTTP()

    def confirmed_receipts(self, task_id, revision):
        """At most one sourced resolution and acquisition, with no copied body.

        These are historical tool facts, not a workflow state or freshness check.
        Actual reuse still validates immutable bytes, grants and current inputs.
        """
        receipts, seen = [], set()
        with self.store.transaction() as db:
            rows = db.execute("SELECT id,result FROM actions WHERE task_id=? AND revision=? "
                "AND kind='environment.acquire' AND status='confirmed' AND json_extract(result,'$.kind')='completed' "
                "AND json_type(result,'$.wheels')='array' ORDER BY rowid DESC LIMIT 64", (task_id, revision))
            for row in rows:
                result = json.loads(row['result'])
                operation = 'download' if 'wheel_artifact_ids' in result else 'resolve'
                source_packages = result.get('source_packages', [])
                if operation in seen or not 1 <= len(result['wheels']) + len(source_packages) <= 32:
                    continue
                if operation == 'download' and (len(result['wheel_artifact_ids']) != len(result['wheels']) or
                    len(set(result['wheel_artifact_ids'])) != len(result['wheel_artifact_ids'])):
                    continue
                if source_packages and (operation != 'download' or
                    len(result.get('source_artifact_ids', [])) != len(source_packages) or
                    len(set(result['source_artifact_ids'])) != len(source_packages)):
                    continue
                lock_id = result.get('lock_artifact_id' if operation == 'download' else 'resolution_lock_artifact_id')
                lock = db.execute("SELECT payload FROM artifacts WHERE id=? AND task_id=?", (lock_id, task_id)).fetchone()
                lock = json.loads(lock['payload']) if lock else {}
                if lock.get('producer_action_id') != row['id'] or lock.get('name') != ('acquisition-lock.json' if operation == 'download' else 'resolution-lock.json'):
                    continue
                wheels, sources = [], []
                for wheel in result['wheels'] + source_packages:
                    if not all(k in wheel for k in ['name', 'version', 'filename']):
                        break
                    value = {k: wheel[k] for k in ['name', 'version', 'filename']}
                    if operation == 'download':
                        artifact_id = wheel.get('artifact_id')
                        source_package = wheel in source_packages
                        id_group = result['source_artifact_ids'] if source_package else result['wheel_artifact_ids']
                        stored = db.execute('SELECT payload FROM artifacts WHERE id=? AND task_id=?', (artifact_id, task_id)).fetchone()
                        artifact = json.loads(stored['payload']) if stored else {}
                        if (artifact_id not in id_group or artifact.get('producer_action_id') != row['id'] or
                            artifact.get('name') != wheel['filename'] or artifact.get('sha256') != wheel.get('sha256')):
                            break
                        value['artifact_id'] = artifact_id
                    (sources if wheel in source_packages else wheels).append(value)
                else:
                    receipts.append({'operation': operation, 'source_action_id': row['id'],
                        'lock_artifact_id': lock_id, 'wheels': wheels,
                        **({'source_packages':sources} if sources else {}),
                        **({'target_artifact_id': result['target_artifact_id']} if operation == 'resolve' and 'target_artifact_id' in result else {})})
                    seen.add(operation)
        return receipts

    def acquire(self, deps, action_id, wheels, source: Literal["pypi", "tuna"] = "pypi", approval_id=None,
                resolution_lock_artifact_id=None, *, source_packages=None):
        wheels = [WheelRequest.model_validate(w).model_dump() for w in (wheels or [])]
        source_packages = [PackageRequest.model_validate(w).model_dump() for w in (source_packages or [])]
        if any(not SOURCE_NAME.fullmatch(w['filename']) for w in source_packages):
            raise Failure('SCHEMA_INVALID', 'source_packages仅接受明确.tar.gz/.zip源码发行包')
        packages = wheels + source_packages
        if source not in {"pypi", "tuna"} or not 1 <= len(packages) <= 32:
            raise Failure("SCHEMA_INVALID", "获取需1–32个精确发行文件请求及支持的来源")
        if len({w["name"] for w in packages}) != len(packages) or len({w["filename"] for w in packages}) != len(packages):
            raise Failure("SCHEMA_INVALID", "同次获取不可重复包名或文件名")
        resolved = {}
        resolution = None
        if resolution_lock_artifact_id is not None:
            if source_packages:
                raise Failure('VERSION_CONFLICT', 'wheel解析锁不授权源码获取；须独立精确源码审批', 409)
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
        urls = [f"https://pypi.org/pypi/{w['name']}/{w['version']}/json" for w in packages]
        value = {"wheels":wheels, "source":source}
        if source_packages:
            value['source_packages'] = source_packages
        request = {"kind": "environment.acquire", "value": encode(value),
            "url": urls[0], "metadata_urls": urls, "destinations": ["https://pypi.org", origin],
            "spec_revision": deps.revision, "sources": sources, "method": "GET",
            "max_bytes": MAX_WHEEL_BYTES, "max_metadata_bytes": MAX_BYTES, "seconds": 180,
            "package_execution": False}
        if resolution:
            request['resolution_lock_artifact_id'] = resolution_lock_artifact_id
            request['resolution_lock_sha256'] = lock_artifact['sha256']
        admission = self.engine.network.admit(deps, action_id, request, hashlib.sha256(encode(request).encode()).hexdigest(), approval_id)
        if isinstance(admission, dict):
            return {**admission, 'continuation': '这是精确发行文件下载审批，尚未下载；批准后用相同wheels/source/source_packages（若有）/解析锁（若有）及此approval_id重调acquire_dependencies。成功的wheel_artifact_ids/source_artifact_ids交prepare_environment；构建/安装须观察真实作业。'}
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
        for index, (wheel, url) in enumerate(zip(packages, urls, strict=True)):
            package_type = 'bdist_wheel' if index < len(wheels) else 'sdist'
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
                entry = next(e for e in release["urls"] if e["filename"] == wheel["filename"] and e["packagetype"] == package_type)
                official, _, _, official_origin = url_parts(entry["url"])
                parts = urlsplit(entry["url"])
                expected, size = entry["digests"]["sha256"], entry["size"]
                if (entry["yanked"] or official_origin != "https://files.pythonhosted.org" or not parts.path.startswith("/packages/") or
                    parts.query or parts.fragment or unquote(parts.path.rsplit("/", 1)[-1]) != wheel["filename"] or
                    not re.fullmatch(r"[0-9a-f]{64}", expected) or type(size) is not int or size <= 0):
                    raise ValueError("unsafe release")
            except (ValueError, KeyError, TypeError, StopIteration) as error:
                raise Failure("PRECONDITION_MISSING", "官方元数据缺少匹配类型的未撤回发行文件、大小或SHA-256", 422) from error
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
                raise Failure("VERIFICATION_FAILED", "下载的发行文件大小或SHA-256与官方元数据不符", 422)
            staging = directory / wheel["filename"]
            native_path(staging).write_bytes(data)
            artifact = self.engine.resources.import_file(deps.task_id, action_id, staging, f"dependencies/{action_id}/{wheel['filename']}")
            records.append({**wheel, "artifact_id": artifact["artifact_id"], "sha256": expected, "size_bytes": size,
                "metadata_url": url, "metadata_artifact_id": metadata_artifact["artifact_id"], "metadata_sha256": hashlib.sha256(raw).hexdigest(),
                "official_url": official, "download_url": selected, "source": source, "hops": downloaded["hops"],
                "observed_at": downloaded["observed_at"]})
        guard(urls[0], urls[0])
        wheel_records, source_records = records[:len(wheels)], records[len(wheels):]
        lock_body = {"schema_version": 1, "kind": "public_package_acquisition" if source_packages else "public_wheel_acquisition", "wheels": wheel_records,
                "package_execution": False, "hash_authority": "official_pypi_metadata_not_independent_signature",
                "scientific_accuracy": "not_assessed"}
        if source_packages:
            lock_body['source_packages'] = source_records
        lock = self.engine.resources.write(deps.task_id, action_id, f"dependencies/{action_id}/acquisition-lock.json",
            json.dumps(lock_body, ensure_ascii=False, indent=2))
        return {"wheel_artifact_ids": [w["artifact_id"] for w in wheel_records], "wheels": wheel_records,
            **({'source_packages':source_records, 'source_artifact_ids':[w['artifact_id'] for w in source_records]} if source_packages else {}),
            "lock_artifact_id": lock["artifact_id"], "package_execution": False,
            "continuation": "将wheel_artifact_ids/source_artifact_ids交prepare_environment同名参数；源码另需code.execute及构建/运行wheel，实际作业成功才有environment.zip。"}
