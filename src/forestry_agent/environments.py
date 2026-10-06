"""Task-owned offline wheel environments, prepared by the existing isolated Jobs.

No package code, pip installation or archive extraction runs on the host.
Online acquisition and source builds remain separate preparation work.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .contracts import Failure
from .resources import file_hash, native_path, version
from .store import encode

MAX_WHEEL_BYTES = 128 * 1024 * 1024
MAX_ENV_BYTES = 512 * 1024 * 1024
MAX_ENV_FILES = 30000
WHEEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+\-]{0,240}\.whl\Z")


def preparation_code(wheels, image_reference):
    # All filenames come from verified single-file resources; interpolate JSON,
    # never shell commands or package-controlled Python syntax.
    return f'''import hashlib, importlib.metadata, json, os, platform, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
wheels = {encode(wheels)}
image_reference = {json.dumps(image_reference)}
if sys.version_info[:2] != (3, 12) or sys.platform != 'linux':
    raise RuntimeError('Environment requires Linux Python 3.12; no silent runtime change')
with tempfile.TemporaryDirectory(prefix='environment-build-', dir='/tmp') as temporary:
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
    lock_input = root/'requirements.lock'
    lock_input.write_text('\\n'.join(requirements)+'\\n', encoding='utf-8')
    command = [sys.executable, '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
        '--no-cache-dir', 'install', '--no-index', '--only-binary', ':all:', '--require-hashes',
        '--ignore-installed', '--no-compile', '--target', str(site), '--find-links', str(wheelhouse),
        '--report', str(root/'install-report.json'), '-r', str(lock_input)]
    result = subprocess.run(command, env={{'PATH':'/usr/local/bin:/usr/bin:/bin', 'HOME':str(root),
        'TMPDIR':str(scratch), 'PYTHONDONTWRITEBYTECODE':'1'}}, check=False)
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

    def prepare(self, task_id, revision, action_id, wheel_refs, seconds=300):
        if not 1 <= len(wheel_refs) <= 32 or len(wheel_refs) != len(set(wheel_refs)):
            raise Failure("SCHEMA_INVALID", "依赖准备需1–32个唯一的授权wheel文件资源")
        wheels, total, filenames = [], 0, set()
        for ref in wheel_refs:
            source = self.resources.resolve(ref, task_id=task_id)
            if not source.is_file() or source.is_symlink() or not WHEEL_NAME.fullmatch(source.name):
                raise Failure("PRECONDITION_MISSING", "需单独授权名称合法的.whl文件；不执行源码包或宿主安装", 422)
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
                "size_bytes": size, "source_version": before})
        return self.jobs.submit(task_id, revision, action_id,
            preparation_code(wheels, self.config.code_image), wheel_refs, seconds,
            job_kind="environment.prepare", environment_recipe=wheels)

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
        return {"artifact_id": artifact_id, "sha256": artifact["sha256"], "source": artifact["locator"],
            "image_id": job["image_id"], "manifest": manifest, "lock_artifact_id": lock["artifact_id"]}
