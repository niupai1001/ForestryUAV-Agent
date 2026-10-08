"""Task-owned offline wheel environments, prepared by the existing isolated Jobs.

No package code, pip installation or archive extraction runs on the host.
Public acquisition has a separate authorization boundary. Authorized local source
archives use native pip build isolation inside the same offline Job.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .build_inspection import FRONTEND_REQUIREMENTS, HOOK_CALLER
from .build_inspection import WORKER as INSPECTION_WORKER
from .contracts import Failure
from .resources import file_hash, native_path, version
from .source_build import BUILD_ONLY, WORKER
from .source_build_legacy import preparation_code as legacy_preparation_code
from .store import encode

MAX_WHEEL_BYTES = 128 * 1024 * 1024
MAX_ENV_BYTES = 512 * 1024 * 1024
MAX_ENV_FILES = 30000
WHEEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+\-]{0,240}\.whl\Z")
# Archives are mounted by resource ID, never interpolated into a shell. Unlike
# wheel distribution filenames, local source names may contain Unicode/spaces.
SOURCE_NAME = re.compile(r"[^/\\:\x00-\x1f\x7f]{1,230}\.(?:tar\.gz|zip)\Z")


def preparation_code(wheels, image_reference, sources=None, *, build_only=False, inspect_build=None, _legacy=False):
    if _legacy:
        return legacy_preparation_code(wheels, image_reference, sources, build_only=build_only)
    # All filenames come from verified single-file resources; interpolate JSON,
    # never shell commands or package-controlled Python syntax.
    return f'''import hashlib, importlib.metadata, json, os, platform, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
wheels = {encode(wheels)}
sources = {encode(sources or [])}
image_reference = {json.dumps(image_reference)}
{WORKER if sources else ''}
{BUILD_ONLY if build_only else ''}
{('HOOK_CALLER = '+repr(HOOK_CALLER)+chr(10)+INSPECTION_WORKER) if inspect_build else ''}
if sys.version_info[:2] != (3, 12) or sys.platform != 'linux':
    raise RuntimeError('Environment requires Linux Python 3.12; no silent runtime change')
with tempfile.TemporaryDirectory(prefix='environment-build-', dir={'"/build"' if sources else '"/tmp"'}) as temporary:
    root = Path(temporary)
    wheelhouse, site, scratch = root/'wheels', root/'site', root/'tmp'
    for directory in [wheelhouse, site, scratch]:
        directory.mkdir()
    requirements = []
    for wheel in wheels:
        source = Path('/inputs')/wheel['resource_id']
        with source.open('rb') as reader:
            data = reader.read(wheel['size_bytes']+1)
        if len(data) != wheel['size_bytes'] or hashlib.sha256(data).hexdigest() != wheel['sha256']:
            raise RuntimeError('Wheel changed after registration: '+wheel['filename'])
        destination = wheelhouse/wheel['filename']
        shutil.copyfile(source, destination)
        requirements.append(str(destination)+' --hash=sha256:'+wheel['sha256'])
    clean_env = {{'PATH':'/usr/local/bin:/usr/bin:/bin', 'HOME':str(root),
        'TMPDIR':str(scratch), 'PYTHONDONTWRITEBYTECODE':'1'}}
    {('inspect_build_sources(root,wheelhouse,sources,wheels,clean_env,image_reference,'+repr(inspect_build)+'); raise SystemExit(0)') if inspect_build else ''}
    built_wheels = []
    {'built_requirements, built_wheels = build_sources(root, wheelhouse, sources, clean_env); requirements.extend(built_requirements)' if sources else ''}
    {'export_build_result(wheelhouse, sources, wheels, built_wheels, image_reference); raise SystemExit(0)' if build_only else ''}
    lock_input = root/'requirements.lock'
    lock_input.write_text('\\n'.join(requirements)+'\\n', encoding='utf-8')
    command = [sys.executable, '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
        '--no-cache-dir', 'install', '--no-index', '--only-binary', ':all:', '--require-hashes',
        '--ignore-installed', '--no-compile', '--target', str(site), '--find-links', str(wheelhouse),
        '--report', str(root/'install-report.json'), '-r', str(lock_input)]
    result = subprocess.run(command, env=clean_env, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    installed = sorted([{{'name':d.metadata['Name'], 'version':d.version}}
        for d in importlib.metadata.distributions(path=[str(site)])], key=lambda d:d['name'].lower())
    files = sorted(site.rglob('*'))
    if len(files) > {MAX_ENV_FILES} or sum(p.stat().st_size for p in files if p.is_file()) > {MAX_ENV_BYTES}:
        raise RuntimeError('Installed environment exceeds bounded archive limits')
    if any(p.is_symlink() for p in files):
        raise RuntimeError('Installed environment contains a symbolic link')
    manifest = {{'schema_version':1, 'kind':'python_environment', 'image_reference':image_reference,
        'python':platform.python_version(), 'platform':sys.platform, 'machine':platform.machine(),
        'wheels':wheels, 'installed':installed, 'installation':'pip_offline_hash_checked',
        'network':'none', 'scientific_accuracy':'not_assessed'}}
    if sources:
        manifest.update(sources=sources, built_wheels=built_wheels,
            build_frontend={{'name':'pip', 'version':importlib.metadata.version('pip'),
                'isolation':'native_pep517', 'dependency_source':'verified_offline_wheelhouse'}})
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
    with zipfile.ZipFile('/output/environment.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('environment-lock.json', text)
        for path in files:
            if path.is_file():
                archive.write(path, 'site/'+path.relative_to(site).as_posix())
    if Path('/output/environment.zip').stat().st_size > {MAX_WHEEL_BYTES}:
        raise RuntimeError('Compressed environment exceeds archive limit')
    Path('/output/environment-lock.json').write_text(text, encoding='utf-8')
    print('ENVIRONMENT_READY '+json.dumps(installed), flush=True)
'''


def execution_code(code, environment):
    return f'''import hashlib, io, json, shutil, stat, sys, tempfile, zipfile
from pathlib import Path, PurePosixPath
source = Path('/environment.zip')
with source.open('rb') as reader:
    data = reader.read({MAX_WHEEL_BYTES + 1})
if len(data) > {MAX_WHEEL_BYTES} or hashlib.sha256(data).hexdigest() != {json.dumps(environment['sha256'])}:
    raise RuntimeError('Environment archive hash changed')
with tempfile.TemporaryDirectory(prefix='environment-use-', dir='/environment') as temporary:
    root = Path(temporary)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > {MAX_ENV_FILES + 1} or sum(m.file_size for m in members) > {MAX_ENV_BYTES + 65536}:
            raise RuntimeError('Environment archive exceeds extraction limits')
        seen = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if (path.is_absolute() or '..' in path.parts or '\\\\' in member.filename or
                member.filename in seen or stat.S_ISLNK(member.external_attr >> 16) or
                (member.filename != 'environment-lock.json' and (not path.parts or path.parts[0] != 'site'))):
                raise RuntimeError('Unsafe environment archive member')
            seen.add(member.filename)
            destination = root.joinpath(*path.parts)
            if not destination.resolve().is_relative_to(root.resolve()):
                raise RuntimeError('Environment archive path escaped')
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as reader, destination.open('xb') as writer:
                    shutil.copyfileobj(reader, writer, length=65536)
    manifest = json.loads((root/'environment-lock.json').read_text(encoding='utf-8'))
    if manifest != json.loads({json.dumps(encode(environment['manifest']))}):
        raise RuntimeError('Environment archive manifest mismatch')
    sys.path.insert(0, str(root/'site'))
    scope = {{'__name__':'__main__', '__file__':'/run.py'}}
    exec(compile({json.dumps(code)}, '/run.py', 'exec'), scope)
'''


class Environments:
    def __init__(self, config, store, resources, jobs):
        self.config, self.store, self.resources, self.jobs = config, store, resources, jobs

    def acquired_wheel(self, task_id, revision, artifact_id):
        return self.acquired_package(task_id, revision, artifact_id, source=False)

    def acquired_source(self, task_id, revision, artifact_id):
        return self.acquired_package(task_id, revision, artifact_id, source=True)

    def acquired_package(self, task_id, revision, artifact_id, *, source=None):
        artifact = self.resources.artifact(artifact_id, task_id)
        if source is None:
            source = bool(SOURCE_NAME.fullmatch(artifact['name']))
        group, ids = ('source_packages', 'source_artifact_ids') if source else ('wheels', 'wheel_artifact_ids')
        valid_name = SOURCE_NAME.fullmatch(artifact['name']) if source else WHEEL_NAME.fullmatch(artifact['name'])
        with self.store.transaction() as db:
            row = db.execute("SELECT result FROM actions WHERE id=? AND task_id=? AND revision=? "
                "AND kind='environment.acquire' AND status='confirmed'", (artifact["producer_action_id"], task_id, revision)).fetchone()
        receipt = json.loads(row[0]) if row else {}
        record = next((w for w in receipt.get(group, []) if w.get("artifact_id") == artifact_id), None)
        if (not valid_name or receipt.get("kind") != "completed" or artifact_id not in receipt.get(ids, []) or not record or
            record.get("sha256") != artifact["sha256"] or record.get("size_bytes") != artifact["size_bytes"] or
            record.get("filename") != artifact["name"]):
            if valid_name and not source:
                built = self.built(task_id, revision, artifact['producer_action_id'])
                if artifact_id in built['wheel_artifact_ids']:
                    return artifact
            raise Failure("PERMISSION_DENIED", "发行文件类型/产物须匹配本任务当前修订已确认的依赖获取动作", 403)
        return {**artifact, **({'package_name':record['name'], 'package_version':record['version']} if source else {})}

    def prepare(self, task_id, revision, action_id, wheel_refs, seconds=300, *, wheel_artifact_ids=None, source_refs=None,
                source_artifact_ids=None, build_only=False, inspect_build=None):
        wheel_artifact_ids = wheel_artifact_ids or []
        source_refs = source_refs or []
        source_artifact_ids = source_artifact_ids or []
        if type(build_only) is not bool or (build_only and not (source_refs or source_artifact_ids)):
            raise Failure('SCHEMA_INVALID', 'build_only须为布尔值且仅用于有授权源码的构建')
        if inspect_build is not None and (inspect_build not in ('static','dynamic') or build_only or
            not (source_refs or source_artifact_ids) or (inspect_build == 'static' and (wheel_refs or wheel_artifact_ids))):
            raise Failure('SCHEMA_INVALID', 'inspect_build仅为static/dynamic；需源码，与build_only互斥；static不安装wheel')
        artifact_ids = wheel_artifact_ids + source_artifact_ids
        if (not 1 <= len(wheel_refs) + len(source_refs) + len(artifact_ids) <= 32 or
            len(wheel_refs + source_refs) != len(set(wheel_refs + source_refs)) or
            len(artifact_ids) != len(set(artifact_ids))):
            raise Failure("SCHEMA_INVALID", "依赖准备需1–32个唯一的授权wheel/源码资源或已确认获取wheel产物")
        if source_refs or source_artifact_ids:
            with self.store.transaction() as db:
                state = self.store.read_task(db, task_id)
                self.store.authorize_capability(db, state, "environment.prepare")
                self.store.authorize_capability(db, state, "code.execute")
        wheels, total, filenames = [], 0, set()
        for ref, acquired in [(ref, False) for ref in wheel_refs] + [(ref, True) for ref in wheel_artifact_ids]:
            artifact = self.acquired_wheel(task_id, revision, ref) if acquired else None
            source = Path(artifact["locator"]) if acquired else self.resources.resolve(ref, task_id=task_id)
            if not native_path(source).is_file() or native_path(source).is_symlink() or not WHEEL_NAME.fullmatch(source.name):
                raise Failure("PRECONDITION_MISSING", "wheel_refs需名称合法的.whl文件；源码包须通过source_refs单独授权", 422)
            before = version(source)
            size = native_path(source).stat().st_size
            total += size
            if not size or total > MAX_WHEEL_BYTES:
                raise Failure("RESOURCE_EXHAUSTED", "wheel文件总量须为1–128 MiB", 422)
            if source.name in filenames:
                raise Failure("SCHEMA_INVALID", "依赖wheel文件名重复")
            filenames.add(source.name)
            sha = file_hash(source)
            if version(source) != before:
                raise Failure("VERSION_CONFLICT", "记录依赖期间wheel发生变化", 409)
            wheels.append({"resource_id": ref, "filename": source.name, "sha256": sha,
                "size_bytes": size, "source_version": before, **({"source_kind": "acquired_artifact"} if acquired else {})})
        sources = []
        for ref, acquired in [(ref, False) for ref in source_refs] + [(ref, True) for ref in source_artifact_ids]:
            artifact = self.acquired_source(task_id, revision, ref) if acquired else None
            source = Path(artifact['locator']) if acquired else self.resources.resolve(ref, task_id=task_id)
            if not native_path(source).is_file() or native_path(source).is_symlink() or not SOURCE_NAME.fullmatch(source.name):
                raise Failure("PRECONDITION_MISSING", "source_refs需独立授权名称合法的.tar.gz或.zip单文件", 422)
            before = version(source)
            size = native_path(source).stat().st_size
            total += size
            if not size or total > MAX_WHEEL_BYTES:
                raise Failure("RESOURCE_EXHAUSTED", "wheel与源码输入总量须为1–128 MiB", 422)
            sha = file_hash(source)
            if version(source) != before:
                raise Failure("VERSION_CONFLICT", "记录依赖期间源码发生变化", 409)
            sources.append({"resource_id":ref, "filename":source.name, "sha256":sha,
                "size_bytes":size, "source_version":before, **({'source_kind':'acquired_artifact'} if acquired else {})})
        if inspect_build == 'dynamic':
            from packaging.utils import InvalidWheelFilename, parse_wheel_filename
            from packaging.version import Version

            provided = set()
            for wheel in wheels:
                try:
                    name, wheel_version, _, _ = parse_wheel_filename(wheel['filename'])
                    provided.add((name,wheel_version))
                except InvalidWheelFilename:
                    continue
            required = {(name,Version(value)) for name,value in (r.split('==') for r in FRONTEND_REQUIREMENTS)}
            if not required <= provided:
                raise Failure('PRECONDITION_MISSING',
                    '动态检查缺少已核验前端wheel。先对同一源码inspect_build="static"并wait_job，'
                    '把build_requirements.requirements+frontend_requirements交resolve_dependencies/独立审批获取，'
                    '再以实际wheel_artifact_ids和源码ID调用inspect_build="dynamic"；尚未提交作业，不要wait_job或猜job_id。',422)
        return self.jobs.submit(task_id, revision, action_id,
            preparation_code(wheels, self.config.code_image, sources, build_only=build_only, inspect_build=inspect_build), wheel_refs + source_refs, seconds,
            job_kind="environment.prepare", environment_recipe=wheels, input_artifact_ids=artifact_ids,
            source_recipe=sources or None, source_build_only=build_only, source_inspection=inspect_build)

    def requirements(self, task_id, revision, action_id):
        """Validate an actual inspection result; it never authorizes installation."""
        from .resolution import requirement

        with self.store.transaction() as db:
            row = db.execute("SELECT j.payload FROM jobs j JOIN actions a ON a.id=j.action_id WHERE "
                "j.task_id=? AND j.revision=? AND j.action_id=? AND j.status='succeeded' "
                "AND a.kind='environment.prepare' AND a.status='confirmed'", (task_id,revision,action_id)).fetchone()
        job = json.loads(row[0]) if row else {}
        mode = job.get('source_inspection')
        if (mode not in ('static','dynamic') or not job.get('source_recipe') or job.get('source_build_only') or
            job.get('exit_code') != 0 or job.get('image_reference') != self.config.code_image or not job.get('image_id') or
            job.get('code') != preparation_code(job.get('environment_recipe',[]),self.config.code_image,
                job['source_recipe'],inspect_build=mode)):
            raise Failure('PERMISSION_DENIED','需当前修订已确认且成功的源码构建依赖检查作业',403)
        products = [self.resources.artifact(ref,task_id) for ref in job['artifact_ids']]
        lock = next((a for a in products if a['name']=='build-requirements.json'),None)
        if not lock or lock['producer_action_id'] != action_id or lock['size_bytes'] > 256*1024:
            raise Failure('VERIFICATION_FAILED','构建依赖检查缺少有界实际清单',422)
        try:
            manifest = json.loads(native_path(lock['locator']).read_bytes())
            if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'source_build_requirements' or
                manifest.get('inspection') != mode or manifest.get('image_reference') != job['image_reference'] or
                manifest.get('sources') != job['source_recipe'] or manifest.get('wheels') != job['environment_recipe'] or
                manifest.get('runtime_installation') is not False or manifest.get('network') != 'none' or
                len(manifest.get('projects',[])) != len(job['source_recipe'])):
                raise ValueError('inspection identity/purpose')
            requirements = []
            for project,source in zip(manifest['projects'],job['source_recipe'],strict=True):
                if (project['source_resource_id'] != source['resource_id'] or project['source_sha256'] != source['sha256'] or
                    not isinstance(project['build_backend'],str) or not 0 < len(project['build_backend']) <= 1024 or
                    not isinstance(project['backend_path'],list) or len(project['backend_path']) > 32 or
                    any(not isinstance(path,str) or len(path)>1024 for path in project['backend_path'])):
                    raise ValueError('inspection source identity')
                if mode == 'static' and (project['dynamic_requires'] or project.get('frontend') is not None):
                    raise ValueError('static inspection must not claim backend execution')
                if mode == 'dynamic' and project.get('frontend') != {
                    'name':'pyproject-hooks','version':'1.2.0','hook':'get_requires_for_build_wheel'}:
                    raise ValueError('frontend identity')
                for values in (project['static_requires'],project['dynamic_requires']):
                    if not isinstance(values,list) or len(values) > 32:
                        raise ValueError('requirements limit')
                    for value in values:
                        requirement(value)
                        if value not in requirements:requirements.append(value)
                if source.get('source_kind') == 'acquired_artifact':
                    self.acquired_source(task_id,revision,source['resource_id'])
                else:
                    path=self.resources.resolve(source['resource_id'],task_id=task_id)
                    if version(path) != source['source_version'] or file_hash(path) != source['sha256']:
                        raise ValueError('source changed')
            if len(requirements) > 32:
                raise ValueError('aggregate requirements limit')
        except (ValueError,KeyError,TypeError,OSError) as error:
            raise Failure('VERIFICATION_FAILED','构建依赖清单与真实作业/源码不一致',422) from error
        return {'inspection':mode,'requirements':requirements,'projects':manifest['projects'],
            'frontend_requirements':FRONTEND_REQUIREMENTS,'artifact_id':lock['artifact_id'],
            'image_id':job['image_id'],'runtime_installation':False}

    def built(self, task_id, revision, action_id):
        """Accept only source wheels and metadata produced by this exact Job.

        This proves provenance and bounded metadata, not runtime installation or
        the complete dependency closure. No wheel is opened on the host.
        """
        from packaging.utils import InvalidWheelFilename, parse_wheel_filename
        from packaging.version import Version

        from .resolution import core_metadata, requirement

        with self.store.transaction() as db:
            row = db.execute("SELECT j.payload FROM jobs j JOIN actions a ON a.id=j.action_id WHERE "
                "j.task_id=? AND j.revision=? AND j.action_id=? AND j.status='succeeded' "
                "AND a.kind='environment.prepare' AND a.status='confirmed'", (task_id, revision, action_id)).fetchone()
        job = json.loads(row[0]) if row else {}
        if (not job.get('source_recipe') or job.get('kind') != 'environment.prepare' or job.get('exit_code') != 0 or
            job.get('image_reference') != self.config.code_image or not job.get('image_id') or
            job.get('source_inspection') or not any(job.get('code') == preparation_code(
                job.get('environment_recipe', []), self.config.code_image, job.get('source_recipe'),
                build_only=True, _legacy=legacy) for legacy in (False, True))):
            raise Failure('PERMISSION_DENIED', 'wheel须来自当前修订已确认且真实成功的仅源码构建作业或依赖获取动作', 403)
        products = [self.resources.artifact(ref, task_id) for ref in job['artifact_ids']]
        lock = next((a for a in products if a['name'] == 'source-build-lock.json'), None)
        if not lock or lock['producer_action_id'] != action_id or lock['size_bytes'] > 65536:
            raise Failure('VERIFICATION_FAILED', '源码构建缺少有界实际锁', 422)
        try:
            manifest = json.loads(native_path(lock['locator']).read_bytes())
            if (manifest.get('kind') != 'source_wheel_build' or manifest.get('schema_version') != 1 or
                manifest.get('sources') != job['source_recipe'] or manifest.get('wheels') != job['environment_recipe'] or
                manifest.get('image_reference') != job['image_reference'] or manifest.get('runtime_installation') is not False or
                manifest.get('network') != 'none' or manifest.get('build_frontend', {}).get('isolation') != 'native_pep517' or
                len(manifest.get('built_wheels', [])) != len(job['source_recipe'])):
                raise ValueError('build lock identity')
            records = []
            for record, source in zip(manifest['built_wheels'], job['source_recipe'], strict=True):
                wheel = next((a for a in products if a['name'] == record['filename']), {})
                metadata = next((a for a in products if a['name'] == record['metadata_filename']), {})
                if (record['source_resource_id'] != source['resource_id'] or record['source_sha256'] != source['sha256'] or
                    wheel.get('producer_action_id') != action_id or metadata.get('producer_action_id') != action_id or
                    record['sha256'] != wheel.get('sha256') or record['size_bytes'] != wheel.get('size_bytes') or
                    record['metadata_filename'] != record['filename']+'.metadata' or
                    record['metadata_sha256'] != metadata.get('sha256') or
                    record['metadata_size_bytes'] != metadata.get('size_bytes') or not 0 < metadata['size_bytes'] <= 2*1024*1024):
                    raise ValueError('built products/provenance')
                name, version_value, _, _ = parse_wheel_filename(record['filename'])
                if source.get('source_kind') == 'acquired_artifact':
                    acquired = self.acquired_source(task_id,revision,source['resource_id'])
                    if (name != acquired['package_name'] or version_value != Version(acquired['package_version']) or
                        acquired['sha256'] != source['sha256']):
                        raise ValueError('built wheel differs from acquired source project/version')
                dependencies = core_metadata(native_path(metadata['locator']).read_bytes(), name, version_value, manifest['python'])
                # A source backend cannot add a private/direct URL by returning it
                # in generated metadata; the existing public requirement contract applies.
                for dependency in dependencies:
                    requirement(str(dependency))
                records.append({**record, 'name':name, 'version':str(version_value), 'artifact_id':wheel['artifact_id'],
                    'metadata_artifact_id':metadata['artifact_id']})
        except (ValueError, KeyError, TypeError, OSError, InvalidWheelFilename) as error:
            raise Failure('VERIFICATION_FAILED', '源码构建锁、生成wheel或元数据与真实作业不一致', 422) from error
        return {'wheel_artifact_ids':[r['artifact_id'] for r in records], 'built_wheels':records,
            'lock_artifact_id':lock['artifact_id'], 'image_id':job['image_id'], 'runtime_installation':False}

    def resolve(self, task_id, revision, artifact_id):
        artifact = self.resources.artifact(artifact_id, task_id)
        if "," in artifact["locator"]:
            raise Failure("PRECONDITION_MISSING", "依赖环境挂载路径不能包含逗号", 422)
        if artifact["name"] != "environment.zip" or artifact["size_bytes"] > MAX_WHEEL_BYTES:
            raise Failure("PRECONDITION_MISSING", "代码环境须引用实际准备成功的environment.zip", 422)
        with self.store.transaction() as db:
            row = db.execute("SELECT payload FROM jobs WHERE task_id=? AND action_id=? AND status='succeeded'",
                (task_id, artifact["producer_action_id"])).fetchone()
        job = json.loads(row["payload"]) if row else None
        if not job or job.get("kind") != "environment.prepare" or job["spec_revision"] != revision:
            raise Failure("PERMISSION_DENIED", "环境须由本任务当前修订的依赖准备作业产生", 403)
        if job.get('source_inspection'):
            raise Failure('PERMISSION_DENIED', '构建依赖检查不能提供已安装环境归档',403)
        if job.get('source_build_only') or (job.get('source_recipe') and any(job.get('code') ==
            preparation_code(job.get('environment_recipe', []), job['image_reference'],job['source_recipe'],build_only=True,
                _legacy=legacy) for legacy in (False, True))):
            raise Failure('PERMISSION_DENIED', '仅构建作业不能提供已安装的环境归档', 403)
        if artifact_id not in job["artifact_ids"]:
            raise Failure("PERMISSION_DENIED", "环境产物未被准备作业登记", 403)
        locks = [self.resources.artifact(ref, task_id) for ref in job["artifact_ids"]]
        lock = next((a for a in locks if a["name"] == "environment-lock.json"), None)
        if not lock or lock["size_bytes"] > 65536:
            raise Failure("VERIFICATION_FAILED", "环境缺少有界不可变锁文件", 422)
        try:
            manifest = json.loads(native_path(Path(lock["locator"])).read_bytes())
        except (ValueError, OSError) as error:
            raise Failure("VERIFICATION_FAILED", "环境锁文件无法读取", 422) from error
        if (manifest.get("schema_version") != 1 or manifest.get("kind") != "python_environment" or
            manifest.get("wheels") != job["environment_recipe"] or manifest.get("installation") != "pip_offline_hash_checked" or
            manifest.get("image_reference") != job.get("image_reference") or not job.get("image_id")):
            raise Failure("VERIFICATION_FAILED", "环境锁与真实准备作业不一致", 422)
        if manifest.get("sources", []) != job.get("source_recipe", []):
            raise Failure("VERIFICATION_FAILED", "环境源码锁与准备作业不一致", 422)
        if job.get("source_recipe"):
            built = manifest.get("built_wheels", [])
            if (len(built) != len(job["source_recipe"]) or
                manifest.get("build_frontend", {}).get("isolation") != "native_pep517"):
                raise Failure("VERIFICATION_FAILED", "环境缺少完整源码构建记录", 422)
            for record, source in zip(built, job["source_recipe"], strict=True):
                product = next((a for a in locks if a["name"] == record.get("filename")), None)
                if (not product or record.get("source_resource_id") != source["resource_id"] or
                    record.get("source_sha256") != source["sha256"] or record.get("sha256") != product["sha256"] or
                    record.get("size_bytes") != product["size_bytes"]):
                    raise Failure("VERIFICATION_FAILED", "构建wheel与实际登记产物不一致", 422)
                if source.get('source_kind') == 'acquired_artifact':
                    from packaging.utils import InvalidWheelFilename, parse_wheel_filename
                    from packaging.version import Version

                    acquired = self.acquired_source(task_id,revision,source['resource_id'])
                    try:
                        name, v, _, _ = parse_wheel_filename(record['filename'])
                        if name != acquired['package_name'] or v != Version(acquired['package_version']):
                            raise ValueError('acquired source identity')
                    except (ValueError, InvalidWheelFilename) as error:
                        raise Failure('VERIFICATION_FAILED', '生成wheel的项目/版本须匹配实际获取的源码',422) from error
        return {"artifact_id": artifact_id, "sha256": artifact["sha256"], "source": artifact["locator"],
            "image_id": job["image_id"], "manifest": manifest, "lock_artifact_id": lock["artifact_id"]}
