"""Offline, byte-verified snapshots of application state and task products."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path, PurePosixPath

from .contracts import Failure
from .resources import native_path, version
from .store import encode, now, uid

DATABASES = {'.forestry/state.sqlite', '.forestry/model-steps.sqlite'}


def _hash(path):
    value = hashlib.sha256()
    with native_path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _offline(config):
    # Reserve the actual listen address for the duration of the operation.
    # A connect timeout is not proof that Windows has no listening service.
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == 'nt':
            guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        guard.bind((config.host, config.port))
        return guard
    except OSError:
        guard.close()
        raise Failure('PRECONDITION_MISSING', '请先停止此配置的本地服务；无法独占其监听地址，备份/恢复只支持离线操作', 409) from None


def _quiescent(db):
    checks = [
        "SELECT 1 FROM tasks WHERE status IN ('running','cancelling','publishing') LIMIT 1",
        "SELECT 1 FROM turns WHERE status IN ('queued','running') LIMIT 1",
        "SELECT 1 FROM actions WHERE status='pending' LIMIT 1",
        "SELECT 1 FROM attempts WHERE effect_state<>'confirmed' LIMIT 1",
        ("SELECT 1 FROM jobs j JOIN tasks t ON j.task_id=t.id WHERE j.status IN ('running','cancelling') "
         "OR (j.status NOT IN ('succeeded','failed','cancelled','timed_out') AND t.status NOT IN ('paused','cancelled','completed','failed')) LIMIT 1"),
        ("SELECT 1 FROM assignments a JOIN tasks t ON a.task_id=t.id WHERE a.status='running' "
         "OR (a.status NOT IN ('succeeded','partial','failed','cancelled') AND t.status<>'paused') LIMIT 1"),
        "SELECT 1 FROM deliveries WHERE status NOT IN ('published','failed') LIMIT 1",
    ]
    if any(db.execute(query).fetchone() for query in checks):
        raise Failure('PRECONDITION_MISSING', '存在活跃执行或未决副作用；先暂停并确认Worker停止、完成对账，再备份', 409)
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise Failure('VERIFICATION_FAILED', 'SQLite完整性检查失败', 422)


def _files(root):
    """Never follow a link or Windows junction while collecting managed data."""
    result = []
    for managed in ['.forestry', 'tasks']:
        base = root / managed
        if not native_path(base).exists():
            continue
        pending = [base]
        while pending:
            path = pending.pop()
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root.resolve()):
                raise Failure('PERMISSION_DENIED', '备份目录包含链接或越界路径', 403)
            if native_path(path).is_dir():
                with os.scandir(native_path(path)) as entries:
                    pending.extend(path / entry.name for entry in entries)
            elif native_path(path).is_file():
                relative = path.relative_to(root).as_posix()
                if relative not in {db + suffix for db in DATABASES for suffix in ['-wal', '-shm', '-journal']}:
                    result.append(relative)
            else:
                raise Failure('PERMISSION_DENIED', '备份只支持普通文件及目录', 403)
    return sorted(result)


def create_backup(config, destination):
    root, destination = config.workspace_root.resolve(), Path(destination).resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise Failure('PERMISSION_DENIED', '备份位置须与运行工作区分开，且不能包含运行工作区', 403)
    if destination.exists():
        raise Failure('VERSION_CONFLICT', '备份目标已存在；使用新位置保留历史', 409)
    state = root / '.forestry/state.sqlite'
    if not state.is_file():
        raise Failure('PRECONDITION_MISSING', '没有可备份的应用数据库', 422)
    with closing(_offline(config)), ExitStack() as stack:
        # Hold the state writer lock before observing quiescence and copying files.
        state_db = stack.enter_context(closing(sqlite3.connect(native_path(state), timeout=1)))
        state_db.execute('BEGIN IMMEDIATE')
        _quiescent(state_db)
        relative_files = _files(root)
        sqlite_files = [p for p in relative_files if p in DATABASES]
        for relative in sqlite_files:
            if relative == '.forestry/state.sqlite':
                continue
            lock = stack.enter_context(closing(sqlite3.connect(native_path(root / relative), timeout=1)))
            lock.execute('BEGIN IMMEDIATE')
        sources = [json.loads(row[0]) for row in state_db.execute('SELECT payload FROM resources')]
        inputs = [{'resource_id': item['resource_id'], 'locator': item['locator'], 'version': item['version'],
                   'classification': item['classification'], 'included': Path(item['locator']).resolve().is_relative_to(root / 'tasks')}
                  for item in sources]
        destination.mkdir(parents=True, exist_ok=False)
        payload = destination / 'payload'
        records = []
        for relative in relative_files:
            source, target = root / relative, payload / relative
            native_path(target.parent).mkdir(parents=True, exist_ok=True)
            if relative in sqlite_files:
                with closing(sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True)) as reader, closing(sqlite3.connect(native_path(target))) as writer:
                    reader.backup(writer)
                    writer.execute('PRAGMA journal_mode=DELETE')
                    if writer.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise Failure('VERIFICATION_FAILED', '备份SQLite完整性失败', 422)
            else:
                before = version(source)
                original_sha = _hash(source)
                shutil.copy2(native_path(source), native_path(target))
                if version(source) != before or _hash(target) != original_sha:
                    raise Failure('VERSION_CONFLICT', '备份期间文件变化；部分副本保留，不发布清单', 409)
            records.append({'path': relative, 'sha256': _hash(target), 'size_bytes': native_path(target).stat().st_size})
        if _files(root) != relative_files:
            raise Failure('VERSION_CONFLICT', '备份期间文件集合变化，不发布清单', 409)
        manifest = {'schema_version': 1, 'kind': 'offline-workspace-backup', 'created_at': now(),
                    'workspace_root': str(root), 'classification': 'private', 'files': records,
                    'resources': inputs, 'scope': ['.forestry', 'tasks'],
                    'external_inputs_included': False, 'restore_requires_original_workspace': True}
        envelope = {'manifest': manifest, 'manifest_sha256': hashlib.sha256(encode(manifest).encode()).hexdigest()}
        with (destination / 'manifest.json').open('x', encoding='utf-8') as handle:
            json.dump(envelope, handle, ensure_ascii=False, indent=2)
    return inspect_backup(destination)


def inspect_backup(source):
    source = Path(source).resolve(strict=True)
    manifest_path = source / 'manifest.json'
    if manifest_path.is_symlink():
        raise Failure('PERMISSION_DENIED', '备份清单不能是链接', 403)
    envelope = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest = envelope['manifest']
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'offline-workspace-backup' or
            envelope['manifest_sha256'] != hashlib.sha256(encode(manifest).encode()).hexdigest()):
        raise Failure('VERIFICATION_FAILED', '备份清单摘要或版本不匹配', 422)
    paths = []
    for item in manifest['files']:
        relative = PurePosixPath(item['path'])
        if (relative.is_absolute() or len(relative.parts) < 2 or relative.parts[0] not in {'.forestry', 'tasks'} or
                '..' in relative.parts or '\\' in item['path'] or ':' in item['path'] or relative.as_posix() != item['path']):
            raise Failure('PERMISSION_DENIED', '备份清单包含不安全路径', 403)
        target = source / 'payload' / relative
        if (not target.resolve().is_relative_to(source / 'payload') or target.is_symlink() or
                native_path(target).stat().st_size != item['size_bytes'] or _hash(target) != item['sha256']):
            raise Failure('VERIFICATION_FAILED', '备份文件大小、摘要或路径不匹配', 422)
        paths.append(item['path'])
    if len(paths) != len(set(paths)) or '.forestry/state.sqlite' not in paths or _files(source / 'payload') != sorted(paths):
        raise Failure('VERIFICATION_FAILED', '备份文件集合不匹配或缺少数据库', 422)
    for database in DATABASES:
        for suffix in ['-wal', '-journal']:
            sidecar = source / 'payload' / (database + suffix)
            if native_path(sidecar).exists() and native_path(sidecar).stat().st_size:
                raise Failure('VERIFICATION_FAILED', '备份出现未纳入清单的SQLite事务内容', 422)
    # Snapshot databases are complete standalone files. Immutable reads neither
    # consume an injected WAL nor create cache sidecars in the frozen backup.
    with closing(sqlite3.connect(f'{(source / "payload/.forestry/state.sqlite").as_uri()}?mode=ro&immutable=1', uri=True)) as db:
        _quiescent(db)
    resources = []
    for item in manifest['resources']:
        path = Path(item['locator'])
        current = version(path) if native_path(path).exists() else None
        resources.append({'resource_id': item['resource_id'], 'included': item['included'],
                          'source_available_unchanged': current == item['version']})
    return {'backup': str(source), 'manifest_sha256': envelope['manifest_sha256'],
            'files': len(paths), 'bytes': sum(i['size_bytes'] for i in manifest['files']),
            'verified': True, 'classification': manifest['classification'], 'resources': resources,
            'workspace_root': manifest['workspace_root']}


def restore_backup(config, source):
    with closing(_offline(config)):
        report = inspect_backup(source)
        root, source = config.workspace_root.resolve(), Path(source).resolve()
        if Path(report['workspace_root']).resolve() != root:
            raise Failure('VERSION_CONFLICT', '恢复必须使用原工作区位置；绝对路径及授权记录不会改写', 409)
        if source.is_relative_to(root) or root.is_relative_to(source):
            raise Failure('PERMISSION_DENIED', '恢复源须与运行工作区分开', 403)
        if any(native_path(root / managed).exists() for managed in ['.forestry', 'tasks']):
            raise Failure('VERSION_CONFLICT', '运行数据已存在；先停服并将原.forestry和tasks移至独立保留位置，不覆盖历史', 409)
        stage = root / ('.restore-' + uid())
        native_path(stage).mkdir(parents=True, exist_ok=False)
        manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))['manifest']
        if hashlib.sha256(encode(manifest).encode()).hexdigest() != report['manifest_sha256']:
            raise Failure('VERSION_CONFLICT', '恢复前清单发生变化；暂存目录保留', 409)
        for item in manifest['files']:
            target = stage / item['path']
            native_path(target.parent).mkdir(parents=True, exist_ok=True)
            original = source / 'payload' / item['path']
            if not original.resolve().is_relative_to(source / 'payload') or original.is_symlink():
                raise Failure('PERMISSION_DENIED', '恢复前备份路径变化', 403)
            shutil.copy2(native_path(original), native_path(target))
            if _hash(target) != item['sha256']:
                raise Failure('VERIFICATION_FAILED', '恢复副本校验失败；保留暂存目录供检查', 422)
        # Publish products first, database last. No service/model/Worker is started.
        for managed in ['tasks', '.forestry']:
            if native_path(stage / managed).exists():
                native_path(stage / managed).rename(native_path(root / managed))
        native_path(stage).rmdir()
        return {**report, 'restored': True, 'service_started': False, 'model_dispatch': 0, 'job_dispatch': 0}
