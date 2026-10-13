"""Operator uploads and per-conversation grants in the existing resource/spec store."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from pydantic import Field

from .contracts import Boundary, Failure
from .resources import file_hash
from .store import encode, uid

UPLOAD_LIMIT = 128 * 1024 * 1024


class AttachmentChange(Boundary):
    request_id: str = Field(min_length=1, max_length=200)
    expected_spec_revision: int = Field(ge=1)
    add_refs: list[str] = Field(default_factory=list, max_length=100)
    remove_refs: list[str] = Field(default_factory=list, max_length=100)


class LocalSelection(Boundary):
    request_id: str = Field(pattern=r'^[A-Za-z0-9-]{1,80}$')
    directory: bool = False


def pick_local(resources, payload):
    """A user-clicked Windows picker; large sources remain in their original location."""
    if os.name != 'nt':
        raise Failure('PRECONDITION_MISSING', '当前系统请通过本地路径添加目录或大文件', 422)
    kind = ('New-Object System.Windows.Forms.FolderBrowserDialog; $dialog.Description="选择会话只读目录"'
            if payload.directory else 'New-Object System.Windows.Forms.OpenFileDialog; $dialog.Multiselect=$true')
    result = ('$dialog.SelectedPath' if payload.directory else '$dialog.FileNames')
    script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); '
              'Add-Type -AssemblyName System.Windows.Forms; $dialog=' + kind + '; '
              'try { if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {'
              '@(' + result + ') | ConvertTo-Json -Compress } else { "[]" } } finally { $dialog.Dispose() }')
    try:
        process = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-Command', script],
            capture_output=True, encoding='utf-8', timeout=300, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise Failure('EXECUTION_STOPPED', '文件选择超时，可重新打开选择窗口', 409) from None
    if process.returncode:
        raise Failure('PRECONDITION_MISSING', '系统文件选择器无法打开，请使用上传或本地路径', 422)
    paths = json.loads(process.stdout)
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list) or len(paths) > 100:
        raise Failure('INPUT_LIMIT', '请选择不超过100个文件', 422)
    return [resources.register({'request_id': f'{payload.request_id}-{index}', 'path': str(Path(path)),
                                'classification': 'private', 'read_only': True})
            for index, path in enumerate(paths)]


def claim_uploads(db, refs, task_id):
    for ref in refs:
        row = db.execute('SELECT payload FROM resources WHERE id=?', (ref,)).fetchone()
        if not row:
            raise Failure('PERMISSION_DENIED', '文件尚未由用户选择', 403)
        resource = json.loads(row['payload'])
        if resource.get('upload_draft_id'):
            if resource.get('owner_task_id') not in (None, task_id):
                raise Failure('PERMISSION_DENIED', '上传文件属于其他会话，请在本会话重新选择文件', 403)
            resource['owner_task_id'] = task_id
            db.execute('UPDATE resources SET payload=? WHERE id=?', (encode(resource), ref))


async def upload(resources, request, filename, draft_id, request_id):
    for value in (draft_id, request_id):
        if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', value):
            raise Failure('SCHEMA_INVALID', '附件请求标识不合法')
    if (not filename or len(filename) > 180 or filename in {'.', '..'}
            or filename.rstrip(' .') != filename or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in filename)
            or filename.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
        raise Failure('SCHEMA_INVALID', '文件名包含不允许的路径字符')
    try:
        content_length = int(request.headers.get('content-length', '0'))
    except ValueError:
        raise Failure('SCHEMA_INVALID', '附件长度不合法') from None
    if content_length > UPLOAD_LIMIT:
        raise Failure('INPUT_LIMIT', '上传上限为128 MiB；大文件请添加本地路径引用', 413)
    root = resources.workspace / 'uploads' / draft_id / request_id
    root.mkdir(parents=True, exist_ok=True)
    if root.resolve() != root or any(p.is_symlink() for p in (root, root.parent, root.parent.parent)):
        raise Failure('PERMISSION_DENIED', '附件目录不能重定向', 403)
    staging = root / ('.' + uid() + '.partial')
    sha, size = hashlib.sha256(), 0
    try:
        with staging.open('xb') as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > UPLOAD_LIMIT:
                    raise Failure('INPUT_LIMIT', '上传上限为128 MiB；大文件请添加本地路径引用', 413)
                sha.update(chunk)
                stream.write(chunk)
        payload = {'request_id': request_id, 'path': str(root / filename), 'classification': 'private',
                   'read_only': True, 'upload_draft_id': draft_id, 'upload_sha256': sha.hexdigest()}
        with resources.store.transaction() as db:
            prior = resources.store.replay(db, request_id, {'kind': 'resource.register', **payload})
            if prior is not None:
                return prior
            target = root / filename
            if target.exists():
                if target.is_symlink() or file_hash(target) != sha.hexdigest():
                    raise Failure('VERSION_CONFLICT', '附件请求标识已用于不同文件内容', 409)
            else:
                os.replace(staging, target)
        return resources.register(payload)
    finally:
        staging.unlink(missing_ok=True)
