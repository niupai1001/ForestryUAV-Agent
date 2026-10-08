"""Bounded metadata-only PyPI provider for the mature resolvelib resolver.

No distribution is imported or extracted here. The existing Jobs execution
path probes the target; existing Acquisition and offline pip verify/install.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.parser import BytesParser
from urllib.parse import unquote, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version
from resolvelib import AbstractProvider, BaseReporter, Resolver
from resolvelib.resolvers import ResolutionImpossible, ResolutionTooDeep

from .contracts import Failure
from .network import MAX_BYTES, url_parts
from .resources import native_path, version
from .store import encode

PROBE_CODE = '''import json, platform, sys, sysconfig
from pathlib import Path
target = {'python_full_version':platform.python_version(), 'sys_platform':sys.platform,
    'implementation_name':sys.implementation.name, 'platform_machine':platform.machine(),
    'platform_system':platform.system(), 'platform_release':platform.release(),
    'platform_version':platform.version(), 'platform_python_implementation':platform.python_implementation(),
    'libc':list(platform.libc_ver()), 'soabi':sysconfig.get_config_var('SOABI')}
Path('/output/execution-target.json').write_text(json.dumps(target, sort_keys=True), encoding='utf-8')
print('EXECUTION_TARGET_READY')
'''
MAX_REQUESTS, MAX_PROJECTS, MAX_INDEX_BYTES, MAX_TOTAL_BYTES = 64, 32, 8*1024*1024, 32*1024*1024


def requirement(value):
    try:
        parsed = Requirement(value)
        if (len(value) > 500 or parsed.url or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', parsed.name) or
            any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', extra) for extra in parsed.extras)):
            raise ValueError('unsupported source/name')
        return parsed
    except (InvalidRequirement, ValueError, TypeError) as error:
        raise Failure('SCHEMA_INVALID', '仅接受有界PEP508包名/版本约束/extra/marker，不接受URL、路径或私有索引', 422) from error


def target_tags(target):
    try:
        py = Version(target['python_full_version'])
        machine = target['platform_machine']
        libc, libc_version = target['libc']
        major, minor = (int(i) for i in libc_version.split('.')[:2])
        if (py.release[:2] != (3, 12) or target['sys_platform'] != 'linux' or target['implementation_name'] != 'cpython' or
            libc != 'glibc' or major != 2 or not 5 <= minor <= 99 or machine not in {'x86_64', 'aarch64', 'i686'} or
            not str(target['soabi']).startswith('cpython-312-')):
            raise ValueError('unsupported execution target')
        baseline = 17 if machine == 'aarch64' else 5
        platforms = []
        for n in range(minor, baseline-1, -1):
            platforms.append(f'manylinux_2_{n}_{machine}')
            if n in {5, 12, 17}:
                alias = {5: 'manylinux1', 12: 'manylinux2010', 17: 'manylinux2014'}[n]
                platforms.append(f'{alias}_{machine}')
        platforms.append(f'linux_{machine}')
        tags = list(cpython_tags((3, 12), abis=['cp312'], platforms=platforms))
        tags.extend(compatible_tags((3, 12), interpreter='cp312', platforms=platforms))
        return {tag: i for i, tag in reversed(list(enumerate(tags)))}
    except (KeyError, ValueError, TypeError, InvalidVersion) as error:
        raise Failure('PRECONDITION_MISSING', '当前解析支持实测CPython3.12 Linux glibc目标；其他运行时尚待适配', 422) from error


def core_metadata(data, name, version_value, python_full_version):
    """Shared bounded text validation for remote and container-produced metadata."""
    if not data or len(data) > MAX_BYTES:
        raise Failure('RESOURCE_EXHAUSTED', 'wheel元数据须为1–2 MiB以内的正文', 422)
    parsed = BytesParser().parsebytes(data)
    try:
        if (len(parsed.get_all('Name', [])) != 1 or len(parsed.get_all('Version', [])) != 1 or
            len(parsed.get_all('Requires-Python', [])) > 1 or canonicalize_name(parsed['Name']) != name or
            Version(parsed['Version']) != version_value or
            not SpecifierSet(parsed.get('Requires-Python', '')).contains(python_full_version)):
            raise ValueError('metadata identity/runtime')
        dependencies = [Requirement(text) for text in parsed.get_all('Requires-Dist', [])]
        if len(dependencies) > 256:
            raise Failure('RESOURCE_EXHAUSTED', '单个wheel依赖声明超过256项', 422)
    except (ValueError, TypeError, InvalidVersion, InvalidSpecifier) as error:
        raise Failure('VERIFICATION_FAILED', 'wheel元数据身份或Python约束不符', 422) from error
    return dependencies


@dataclass(frozen=True)
class Candidate:
    name: str
    version: Version
    filename: str
    url: str
    wheel_sha: str
    metadata_sha: str
    requires_python: str
    rank: int
    extras: frozenset[str] = frozenset()
    artifact_id: str | None = None
    metadata_artifact_id: str | None = None


class WheelProvider(AbstractProvider):
    def __init__(self, target, fetch, built_wheels=None):
        self.target, self.fetch, self.tags = target, fetch, target_tags(target)
        self.indices, self.metadata = {}, {}
        self.environment = {**target, 'python_version': '3.12', 'os_name': 'posix',
            'implementation_version': target['python_full_version']}
        # Explicitly provided source products are immutable candidates for their
        # own projects. Remote runtime dependencies still use the same resolver.
        self.built = {}
        for record, data in built_wheels or []:
            name, v, _, tags = parse_wheel_filename(record['filename'])
            supported = tags & self.tags.keys()
            if not supported:
                raise Failure('PRECONDITION_MISSING', '构建wheel与实测目标标签不兼容', 422)
            url = 'artifact:'+record['artifact_id']
            candidate = Candidate(name, v, record['filename'], url, record['sha256'], record['metadata_sha256'],
                '', min(self.tags[t] for t in supported), artifact_id=record['artifact_id'],
                metadata_artifact_id=record['metadata_artifact_id'])
            self.built.setdefault(name, []).append(candidate)
            self.metadata[url] = core_metadata(data, name, v, target['python_full_version'])

    def identify(self, requirement_or_candidate):
        return canonicalize_name(requirement_or_candidate.name)

    def get_preference(self, identifier, resolutions, candidates, information, backtrack_causes):
        return (0 if any(any(s.operator in {'==', '==='} for s in i.requirement.specifier) for i in information[identifier]) else 1, identifier)

    def index(self, name):
        if name in self.built:
            return self.built[name]
        if name in self.indices:
            return self.indices[name]
        if len(self.indices) + len(self.built) >= MAX_PROJECTS:
            raise Failure('RESOURCE_EXHAUSTED', '依赖闭包超过32个项目', 422)
        # Reserve the slot before fetching; repeated/failed lookups cannot evade the limit.
        self.indices[name] = []
        body = self.fetch(f'https://pypi.org/simple/{name}/', simple=True)
        try:
            index = json.loads(body)
            if canonicalize_name(index['name']) != name or str(index['meta']['api-version']).split('.')[0] != '1':
                raise ValueError('index identity/version')
            for entry in index['files']:
                if not entry['filename'].endswith('.whl') or entry.get('yanked', False) is not False:
                    continue
                try:
                    package, v, _, tags = parse_wheel_filename(entry['filename'])
                    supported = tags & self.tags.keys()
                    if package != name or not supported or not SpecifierSet(entry.get('requires-python') or '').contains(self.target['python_full_version']):
                        continue
                except (InvalidWheelFilename, InvalidSpecifier):
                    continue
                url, _, _, origin = url_parts(entry['url'])
                parts = urlsplit(entry['url'])
                wheel_hash = entry['hashes'].get('sha256', '')
                core = entry.get('core-metadata', entry.get('dist-info-metadata'))
                metadata_hash = core.get('sha256', '') if isinstance(core, dict) else ''
                if (origin != 'https://files.pythonhosted.org' or not parts.path.startswith('/packages/') or
                    parts.query or parts.fragment or unquote(parts.path.rsplit('/', 1)[-1]) != entry['filename'] or
                    not re.fullmatch('[0-9a-f]{64}', wheel_hash) or not re.fullmatch('[0-9a-f]{64}', metadata_hash)):
                    continue
                self.indices[name].append(Candidate(name, v, entry['filename'], url, wheel_hash, metadata_hash,
                    entry.get('requires-python') or '', min(self.tags[t] for t in supported)))
        except (ValueError, KeyError, TypeError) as error:
            raise Failure('VERIFICATION_FAILED', 'PyPI Simple索引身份、版本或结构不合法', 422) from error
        return self.indices[name]

    def find_matches(self, identifier, requirements, incompatibilities):
        required = list(requirements[identifier])
        constraints = SpecifierSet(','.join(str(r.specifier) for r in required if r.specifier))
        extras = frozenset(e for r in required for e in r.extras)
        blocked = set(incompatibilities[identifier])
        available = [Candidate(**{**c.__dict__, 'extras': extras}) for c in self.index(identifier)
            if constraints.contains(c.version, prereleases=bool(constraints.prereleases))]
        return [c for c in sorted(available, key=lambda c: (c.version, -c.rank, c.filename), reverse=True) if c not in blocked]

    def is_satisfied_by(self, requirement, candidate):
        return (self.identify(requirement) == candidate.name and requirement.specifier.contains(candidate.version, prereleases=True) and
            frozenset(canonicalize_name(e) for e in requirement.extras) <= frozenset(canonicalize_name(e) for e in candidate.extras))

    def get_dependencies(self, candidate):
        if candidate.url not in self.metadata:
            data = self.fetch(candidate.url+'.metadata')
            if hashlib.sha256(data).hexdigest() != candidate.metadata_sha:
                raise Failure('VERIFICATION_FAILED', 'wheel Core Metadata SHA-256不符', 422)
            self.metadata[candidate.url] = core_metadata(data, candidate.name, candidate.version, self.target['python_full_version'])
        return [requirement(str(r)) for r in self.metadata[candidate.url] if r.marker is None or
            any(r.marker.evaluate({**self.environment, 'extra': extra}) for extra in {'', *candidate.extras})]


class DependencyResolution:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store

    def target(self, deps, artifact_id):
        artifact = self.engine.resources.artifact(artifact_id, deps.task_id)
        with self.store.transaction() as db:
            row = db.execute("SELECT j.payload FROM jobs j JOIN actions a ON j.action_id=a.id WHERE "
                "j.task_id=? AND j.revision=? AND a.kind='environment.acquire' AND a.status='confirmed' "
                "AND j.action_id=?", (deps.task_id, deps.revision, artifact['producer_action_id'])).fetchone()
        job = json.loads(row[0]) if row else {}
        if (job.get('code') != PROBE_CODE or job.get('status') != 'succeeded' or job.get('exit_code') != 0 or
            job.get('mounts') != [] or job.get('environment') or artifact_id not in job.get('artifact_ids', []) or
            artifact['name'] != 'execution-target.json' or artifact['size_bytes'] > 8192 or
            job.get('image_reference') != self.engine.config.code_image or not job.get('image_id')):
            raise Failure('PERMISSION_DENIED', '目标必须来自当前任务/修订真实成功的无输入受限探测作业', 403)
        image = self.engine.jobs.docker.call('image', 'inspect', '--format', '{{.Id}}', self.engine.config.code_image)
        if image['code'] or image['stdout'].strip() != job['image_id']:
            raise Failure('VERSION_CONFLICT', '执行镜像已变化，须重新探测目标', 409)
        target = json.loads(native_path(artifact['locator']).read_bytes())
        target_tags(target)
        return target, artifact, job

    def lock(self, deps, artifact_id):
        artifact = self.engine.resources.artifact(artifact_id, deps.task_id)
        with self.store.transaction() as db:
            row = db.execute("SELECT result FROM actions WHERE id=? AND task_id=? AND revision=? "
                "AND kind='environment.acquire' AND status='confirmed'", (artifact['producer_action_id'], deps.task_id, deps.revision)).fetchone()
        receipt = json.loads(row[0]) if row else {}
        if receipt.get('kind') != 'completed' or receipt.get('resolution_lock_artifact_id') != artifact_id or artifact['size_bytes'] > 512*1024:
            raise Failure('PERMISSION_DENIED', '需当前任务/修订已确认的解析锁', 403)
        lock = json.loads(native_path(artifact['locator']).read_bytes())
        _, target_artifact, job = self.target(deps, lock['target_artifact_id'])
        if target_artifact['sha256'] != lock['target_sha256'] or job['image_id'] != lock['image_id']:
            raise Failure('VERSION_CONFLICT', '解析目标身份已变化', 409)
        for selected in lock['selected']:
            if selected.get('artifact_id'):
                record, _ = self.built_wheel(deps, selected['artifact_id'], job['image_id'])
                if any(selected.get(key) != record.get(key) for key in
                    ['name','version','filename','sha256','metadata_artifact_id']):
                    raise Failure('VERSION_CONFLICT', '解析锁中的源码wheel身份变化', 409)
                if selected['core_metadata_sha256'] != record['metadata_sha256']:
                    raise Failure('VERSION_CONFLICT', '解析锁中的源码元数据变化', 409)
        return lock, artifact

    def built_wheel(self, deps, artifact_id, image_id):
        artifact = self.engine.resources.artifact(artifact_id, deps.task_id)
        built = self.engine.environments.built(deps.task_id, deps.revision, artifact['producer_action_id'])
        if built['image_id'] != image_id:
            raise Failure('VERSION_CONFLICT', '源码构建与依赖解析须使用同一实际镜像', 409)
        record = next((r for r in built['built_wheels'] if r['artifact_id'] == artifact_id), None)
        if not record:
            raise Failure('PERMISSION_DENIED', '仅接受当前修订真实源码构建wheel', 403)
        metadata = self.engine.resources.artifact(record['metadata_artifact_id'], deps.task_id)
        return record, native_path(metadata['locator']).read_bytes()

    def resolve(self, deps, action_id, requirements, target_artifact_id=None, approval_id=None, *, built_wheel_artifact_ids=None):
        if deps.assignment_id:
            raise Failure('PERMISSION_DENIED', '当前依赖解析/目标探测仅开放给根协调者', 403)
        if not isinstance(requirements, list) or not 1 <= len(requirements) <= 32:
            raise Failure('SCHEMA_INVALID', '解析需1–32个PEP508需求', 422)
        roots = [requirement(text) for text in requirements]
        built_wheel_artifact_ids = built_wheel_artifact_ids or []
        if len(built_wheel_artifact_ids) > 32 or len(set(built_wheel_artifact_ids)) != len(built_wheel_artifact_ids):
            raise Failure('SCHEMA_INVALID', '源码构建wheel最多32项且不可重复', 422)
        if not target_artifact_id:
            with self.store.transaction() as db:
                previous = [r[0] for r in db.execute("SELECT f.id FROM artifacts f JOIN actions a "
                    "ON json_extract(f.payload,'$.producer_action_id')=a.id WHERE f.task_id=? AND a.revision=? "
                    "AND a.kind='environment.acquire' AND a.status='confirmed' "
                    "AND json_extract(f.payload,'$.name')='execution-target.json' ORDER BY f.rowid DESC", (deps.task_id, deps.revision))]
            for ref in previous:
                try:
                    self.target(deps, ref)
                except Failure:
                    continue
                return {'target_artifact_id': ref, 'continuation': '复用已确认的实际目标，用同requirements与target_artifact_id再次调用resolve_dependencies。'}
            with self.store.transaction() as db:
                self.engine.roles.authorize(db, deps, 'code.execute', {}, check_budget=False)
            result = self.engine.jobs.submit(deps.task_id, deps.revision, action_id, PROBE_CODE, [], 60)
            return {**result, 'continuation': '用wait_job观察探测成功，从artifacts找到execution-target.json的artifact_id，再用同requirements和target_artifact_id调用resolve_dependencies。'}
        target, artifact, job = self.target(deps, target_artifact_id)
        built = [self.built_wheel(deps, ref, job['image_id']) for ref in built_wheel_artifact_ids]
        if sum(len(data) for _, data in built) > MAX_TOTAL_BYTES:
            raise Failure('RESOURCE_EXHAUSTED', '源码wheel元数据累计超过32 MiB', 422)
        state = self.store.task(deps.task_id)
        sources = [{'resource_id': ref, 'version': version(self.engine.resources.resolve(ref, task_id=deps.task_id))} for ref in state['input_refs']]
        request = {'kind': 'environment.acquire', 'operation': 'resolve', 'value': encode({'requirements': requirements,
            'target_artifact_id': target_artifact_id, 'target_sha256': artifact['sha256'], 'image_id': job['image_id'],
            **({'built_wheels':[{k:r[k] for k in ['artifact_id','sha256','metadata_artifact_id','metadata_sha256']} for r,_ in built]} if built else {})}),
            'url': 'https://pypi.org/simple/'+canonicalize_name(roots[0].name)+'/',
            'destinations': ['https://pypi.org', 'https://files.pythonhosted.org'], 'spec_revision': deps.revision,
            'sources': sources, 'method': 'GET', 'max_bytes': MAX_TOTAL_BYTES, 'seconds': 180,
            'max_requests': MAX_REQUESTS, 'max_projects': MAX_PROJECTS, 'transitive_dependencies': True,
            'metadata_only': True, 'package_execution': False}
        admitted = self.engine.network.admit(deps, action_id, request, hashlib.sha256(encode(request).encode()).hexdigest(), approval_id)
        if isinstance(admitted, dict):
            return {**admitted, 'continuation': '这是元数据解析审批，尚无wheel解析结果；批准后用相同requirements/target_artifact_id及此approval_id重调resolve_dependencies。解析成功才用返回wheels/解析锁调用acquire_dependencies并提出独立下载审批。'}
        deadline, snapshots, total, wire_total, requests_used = time.monotonic()+180, [], sum(len(data) for _,data in built), 0, 0
        def guard(url, expected):
            if url_parts(url)[0] != expected:
                raise Failure('PERMISSION_DENIED', '解析只访问核验的精确索引/元数据URL', 403)
            if time.monotonic() >= deadline:
                raise Failure('RESOURCE_EXHAUSTED', '依赖解析总时限已用尽', 422)
            with self.store.transaction() as db:
                current = self.store.read_task(db, deps.task_id)
                self.engine.roles.authorize(db, deps, 'environment.acquire', {}, check_budget=False)
                if current['spec_revision'] != deps.revision or current['pause_requested'] or current['cancellation_requested']:
                    raise Failure('EXECUTION_STOPPED', '控制状态或修订阻止依赖解析', 409)
            self.engine.resources.artifact(target_artifact_id, deps.task_id)
            for ref in built_wheel_artifact_ids:
                self.built_wheel(deps, ref, job['image_id'])
            if admitted:
                grant = next(a for a in self.engine.network.list(deps.task_id) if a['approval_id'] == admitted)
                if grant['status'] != 'consumed' or grant['consumed_action_id'] != action_id or datetime.fromisoformat(grant['expires_at']) <= datetime.now(UTC):
                    raise Failure('PERMISSION_DENIED', '依赖解析审批已失效', 403)
            if any(version(self.engine.resources.resolve(s['resource_id'], task_id=deps.task_id)) != s['version'] for s in sources):
                raise Failure('VERSION_CONFLICT', '依赖解析期间输入版本变化', 409)
        def fetch(url, simple=False):
            nonlocal total, wire_total, requests_used
            if requests_used >= MAX_REQUESTS:
                raise Failure('RESOURCE_EXHAUSTED', '解析元数据请求超过64次', 422)
            remaining = min(MAX_TOTAL_BYTES-total, MAX_TOTAL_BYTES-wire_total)
            if remaining <= 0:
                raise Failure('RESOURCE_EXHAUSTED', '解析元数据传输或解压总量达到32 MiB上限', 422)
            response = self.engine.acquisition.http.get(url, guard=lambda u: guard(u, url),
                allowed_origins=['https://pypi.org' if simple else 'https://files.pythonhosted.org'],
                max_bytes=min(MAX_INDEX_BYTES if simple else MAX_BYTES, remaining),
                simple_json=simple, deadline=deadline, max_hops=min(4, MAX_REQUESTS-requests_used))
            guard(url, url)
            data = response['body']
            requests_used += len(response.get('hops', [])) or 1
            total += response.get('decoded_bytes', len(data))
            wire_total += response.get('wire_bytes', len(data))
            if max(total, wire_total) > MAX_TOTAL_BYTES:
                raise Failure('RESOURCE_EXHAUSTED', '解析元数据传输或解压总量超过32 MiB', 422)
            directory = self.engine.resources.task_root(deps.task_id)/'dependencies'/action_id
            native_path(directory).mkdir(parents=True, exist_ok=True)
            path = directory/f'metadata-{len(snapshots):03d}.txt'
            native_path(path).write_bytes(data)
            saved = self.engine.resources.import_file(deps.task_id, action_id, path, f'dependencies/{action_id}/{path.name}')
            snapshots.append({'url': url, 'artifact_id': saved['artifact_id'], 'sha256': saved['sha256'], 'size_bytes': len(data),
                'http_content_encoding': response.get('headers', {}).get('content-encoding', 'identity'),
                'snapshot_representation': 'decoded_http_body', 'wire_size_bytes': response.get('wire_bytes', len(data)),
                'response_decoded_bytes': response.get('decoded_bytes', len(data)), 'http_requests': len(response.get('hops', [])) or 1})
            return data
        provider = WheelProvider(target, fetch, built)
        roots = [r for r in roots if r.marker is None or r.marker.evaluate(provider.environment)]
        try:
            resolved = Resolver(provider, BaseReporter()).resolve(roots, max_rounds=100)
        except ResolutionImpossible as error:
            raise Failure('DEPENDENCY_CONFLICT', '没有满足全部约束的兼容wheel闭包；可能冲突、缺失文件级元数据或仅有源码包', 422) from error
        except ResolutionTooDeep as error:
            raise Failure('RESOURCE_EXHAUSTED', '依赖解析超过100轮，未静默选择不完整集合', 422) from error
        if len(resolved.mapping) > 32 or not resolved.mapping:
            raise Failure('PRECONDITION_MISSING', '解析没有1–32项可安装的wheel闭包', 422)
        selected = [{'name': c.name, 'version': str(c.version), 'filename': c.filename,
            'sha256': c.wheel_sha, 'core_metadata_sha256': c.metadata_sha, 'url': c.url,
            'extras': sorted(c.extras), **({'artifact_id':c.artifact_id,'metadata_artifact_id':c.metadata_artifact_id}
                if c.artifact_id else {})} for c in sorted(resolved.mapping.values(), key=lambda c: c.name)]
        guard(request['url'], request['url'])
        lock = self.engine.resources.write(deps.task_id, action_id, f'dependencies/{action_id}/resolution-lock.json',
            json.dumps({'schema_version': 1, 'kind': 'python_wheel_resolution', 'requirements': requirements,
                'target': target, 'target_artifact_id': target_artifact_id, 'target_sha256': artifact['sha256'],
                'image_id': job['image_id'], 'selected': selected, 'metadata_snapshots': snapshots,
                'resolver': 'resolvelib', 'package_execution': False}, ensure_ascii=False, indent=2))
        return {'wheels': [{k: c[k] for k in ['name', 'version', 'filename']} for c in selected if not c.get('artifact_id')],
            **({'built_wheel_artifact_ids':[c['artifact_id'] for c in selected if c.get('artifact_id')]} if built else {}),
            **({'built_wheels':[{k:c[k] for k in ['name','version','filename','artifact_id']} for c in selected if c.get('artifact_id')]} if built else {}),
            'resolution_lock_artifact_id': lock['artifact_id'], 'target_artifact_id': target_artifact_id,
            'wheel_sha256': {c['name']: c['sha256'] for c in selected},
            'metadata_requests': requests_used, 'package_execution': False,
            'continuation': '仅将非空wheels及解析锁交acquire_dependencies另行审批下载；下载IDs与built_wheel_artifact_ids合并交prepare_environment。无下载项直接安装已有构建IDs，实际pip仍核验完整闭包。'}
