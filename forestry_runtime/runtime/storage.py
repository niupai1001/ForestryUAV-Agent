"""Persistent file assets only; no task state or execution log database."""
import hashlib
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid


class AssetError(ValueError):
    pass


class Store:
    def operation(self):
        return nullcontext()

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'assets.sqlite3'
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL, media_type TEXT,
                parent_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('CREATE INDEX IF NOT EXISTS asset_owner ON assets(owner)')

    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def get(self, asset_id, owner):
        if not re.fullmatch(r'asset_[0-9a-f]{32}', asset_id):
            raise AssetError('Invalid asset_id')
        with self.connect() as c:
            row = c.execute('SELECT * FROM assets WHERE id=? AND owner=?', (asset_id, owner)).fetchone()
        if row is None:
            raise AssetError('Asset not found or not accessible')
        result = dict(row)
        result.pop('owner')
        return result

    def path(self, asset_id, owner):
        self.get(asset_id, owner)
        path = self.root / asset_id / 'content'
        if not path.is_file():
            raise AssetError('Stored file is missing')
        return path

    def list(self, owner):
        with self.connect() as c:
            rows = c.execute('SELECT id FROM assets WHERE owner=? ORDER BY created_at DESC LIMIT 200', (owner,)).fetchall()
        return [self.get(r['id'], owner) for r in rows]

    def put(self, stream, filename, owner, media_type=None, parent_id=None, limit=None):
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
                c.execute('INSERT INTO assets(id,owner,name,size,sha256,media_type,parent_id) VALUES(?,?,?,?,?,?,?)',
                          (asset_id, owner, name, size, checksum, media_type, parent_id))
            return self.get(asset_id, owner)
        except BaseException:
            path.unlink(missing_ok=True)
            directory.rmdir()
            raise
