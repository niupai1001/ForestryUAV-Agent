"""Session-owned file and path-reference artifact registry."""
import hashlib
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import time
import uuid

from .exec.paths import is_within


class AssetError(ValueError):
    pass


class Store:
    def operation(self):
        return nullcontext()

    # ------------------------------------------------------------------ settings

    def setting_overrides(self) -> dict[str, str]:
        """Tunable values written through the workbench, which outrank the environment.

        Deliberately on the base store: the settings panel is served for whichever
        store the request resolves to, and a method that existed only on the run
        store would fail there with an AttributeError naming nothing useful.
        """
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def set_setting(self, key: str, value: str, *, updated_by: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value, updated_at, updated_by) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (key, value, time.time(), updated_by),
            )

    def clear_setting(self, key: str) -> None:
        """Drop an override so the environment or the default applies again."""
        with self.connect() as connection:
            connection.execute("DELETE FROM settings WHERE key=?", (key,))

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'assets.sqlite3'
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL, media_type TEXT,
                parent_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
            columns = {row['name'] for row in c.execute('PRAGMA table_info(assets)').fetchall()}
            if 'managed_path' not in columns:
                c.execute('ALTER TABLE assets ADD COLUMN managed_path TEXT')
            if 'artifact_kind' not in columns:
                c.execute("ALTER TABLE assets ADD COLUMN artifact_kind TEXT NOT NULL DEFAULT 'file'")
            if 'metadata_json' not in columns:
                c.execute("ALTER TABLE assets ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
            # Tunable values live beside the chat's own data. They are read on demand
            # rather than cached at start, so a change applies to the next Run, and a
            # value written for one chat does not silently change another's behaviour.
            c.execute('''CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at REAL NOT NULL, updated_by TEXT)''')
            if 'sealed_at' not in columns:
                c.execute('ALTER TABLE assets ADD COLUMN sealed_at REAL')
            if 'verification_json' not in columns:
                c.execute("ALTER TABLE assets ADD COLUMN verification_json TEXT NOT NULL DEFAULT '{}'")
            c.execute('CREATE INDEX IF NOT EXISTS asset_owner ON assets(owner)')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def get(self, asset_id, owner):
        if not re.fullmatch(r'asset_[0-9a-f]{32}', asset_id):
            raise AssetError('Invalid asset_id')
        with self.connect() as c:
            row = c.execute('SELECT * FROM assets WHERE id=? AND owner=?', (asset_id, owner)).fetchone()
        if row is None:
            raise AssetError('Asset not found or not accessible')
        result = dict(row)
        result.pop('owner')
        try:
            result['metadata'] = json.loads(result.pop('metadata_json') or '{}')
        except (TypeError, json.JSONDecodeError):
            result['metadata'] = {}
        try:
            result['verification'] = json.loads(result.pop('verification_json') or '{}')
        except (TypeError, json.JSONDecodeError):
            result['verification'] = {}
        return result

    def path(self, asset_id, owner):
        asset = self.get(asset_id, owner)
        managed_path = asset.get('managed_path')
        if managed_path:
            path = (self.root / managed_path).resolve()
            if not is_within(path, self.root):
                raise AssetError('Registered artifact path leaves the session')
        else:
            path = self.root / asset_id / 'content'
        if not path.is_file():
            raise AssetError('Stored file is missing')
        return path

    def list(self, owner):
        with self.connect() as c:
            rows = c.execute('SELECT id FROM assets WHERE owner=? ORDER BY created_at DESC LIMIT 200', (owner,)).fetchall()
        return [self.get(r['id'], owner) for r in rows]

    def put(self, stream, filename, owner, media_type=None, parent_id=None, limit=None,
            metadata=None):
        name = str(filename or 'unnamed').replace('\\', '/').rsplit('/', 1)[-1]
        if name in ('', '.', '..') or len(name) > 240 or any(ord(ch) < 32 for ch in name):
            raise AssetError('Invalid filename')
        limit = limit if limit is not None else int(os.getenv('MAX_UPLOAD_BYTES', 536870912))
        asset_id = 'asset_' + uuid.uuid4().hex
        directory = self.root / asset_id
        directory.mkdir()
        path = directory / 'content'
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open('wb') as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise AssetError(f'File exceeds the {limit} byte limit')
                    digest.update(chunk)
                    output.write(chunk)
            checksum = digest.hexdigest()
            with self.connect() as c:
                existing = c.execute('SELECT id FROM assets WHERE owner=? AND name=? AND sha256=? AND parent_id IS ?',
                                     (owner, name, checksum, parent_id)).fetchone()
                if existing and (self.root / existing['id'] / 'content').is_file():
                    path.unlink()
                    directory.rmdir()
                    return self.get(existing['id'], owner)
                c.execute('INSERT INTO assets(id,owner,name,size,sha256,media_type,parent_id,metadata_json) VALUES(?,?,?,?,?,?,?,?)',
                          (asset_id, owner, name, size, checksum, media_type, parent_id,
                           json.dumps(metadata or {}, ensure_ascii=False)))
            return self.get(asset_id, owner)
        except BaseException:
            path.unlink(missing_ok=True)
            directory.rmdir()
            raise

    def register_path(self, path, filename, owner, media_type=None, parent_id=None,
                      artifact_kind='file', metadata=None):
        """Register a runtime-owned file without copying it into another asset tree."""
        target = Path(path).resolve()
        if not target.is_file():
            raise AssetError('Artifact file does not exist')
        if not is_within(target, self.root):
            raise AssetError('Only files owned by this session can be registered by path')
        name = str(filename or target.name).replace('\\', '/').rsplit('/', 1)[-1]
        if name in ('', '.', '..') or len(name) > 240 or any(ord(ch) < 32 for ch in name):
            raise AssetError('Invalid filename')
        relative = target.relative_to(self.root).as_posix()
        asset_id = 'asset_' + uuid.uuid4().hex
        size = target.stat().st_size
        with self.connect() as c:
            existing = c.execute(
                'SELECT id FROM assets WHERE owner=? AND managed_path=?',
                (owner, relative),
            ).fetchone()
            if existing:
                c.execute(
                    'UPDATE assets SET size=?, name=?, media_type=?, artifact_kind=?, metadata_json=? WHERE id=?',
                    (size, name, media_type, artifact_kind, json.dumps(metadata or {}, ensure_ascii=False), existing['id']),
                )
                result_id = existing['id']
            else:
                c.execute(
                    '''INSERT INTO assets(id,owner,name,size,sha256,media_type,parent_id,
                       managed_path,artifact_kind,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (asset_id, owner, name, size, '', media_type, parent_id, relative,
                     artifact_kind, json.dumps(metadata or {}, ensure_ascii=False)),
                )
                result_id = asset_id
        return self.get(result_id, owner)

    def seal(self, asset_id, owner, verification=None):
        """Copy a selected working artifact to an immutable session-owned path."""
        asset = self.get(asset_id, owner)
        if asset.get('sealed_at'):
            return asset
        source = self.path(asset_id, owner)
        directory = self.root / 'sealed' / asset_id
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / 'content.partial'
        target = directory / 'content'
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open('rb') as input_file, temporary.open('xb') as output:
                while chunk := input_file.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    output.write(chunk)
            temporary.replace(target)
            relative = target.relative_to(self.root).as_posix()
            with self.connect() as c:
                c.execute(
                    """UPDATE assets SET managed_path=?,size=?,sha256=?,artifact_kind='sealed-output',
                    sealed_at=?,verification_json=? WHERE id=? AND owner=?""",
                    (relative, size, digest.hexdigest(), time.time(),
                     json.dumps(verification or {'status': 'unverified'}, ensure_ascii=False),
                     asset_id, owner),
                )
            return self.get(asset_id, owner)
        except BaseException:
            temporary.unlink(missing_ok=True)
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
            raise
