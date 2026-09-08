"""Single-process chat asset lifecycle. No model-visible cleanup tool."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from pathlib import Path
import shutil
import sqlite3
import threading
from uuid import UUID

from .storage import Store, AssetError

log = logging.getLogger(__name__)


def chat_uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise AssetError('A persistent UUID chat_id is required')


class SessionClosed(AssetError):
    pass


class Sessions:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.directories = self.root / 'sessions'
        self.directories.mkdir(exist_ok=True)
        self.database = self.root / 'lifecycle.sqlite3'
        self.lock = threading.RLock()
        self.active = {}
        self.stores = {}
        with self.db() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'open', acknowledged INTEGER NOT NULL DEFAULT 0)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS source_files (
                chat_id TEXT NOT NULL, file_id TEXT NOT NULL,
                PRIMARY KEY(chat_id, file_id))''')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _row(self, conn, owner, chat_id):
        row = conn.execute('SELECT * FROM sessions WHERE id=?', (chat_id,)).fetchone()
        if row is None or row['owner'] != owner:
            raise AssetError('Session not found or not accessible')
        return row

    def create(self, owner, chat_id):
        chat_id = chat_uuid(chat_id)
        with self.lock, self.db() as conn:
            conn.execute('INSERT OR IGNORE INTO sessions(id, owner) VALUES (?, ?)', (chat_id, owner))
            row = self._row(conn, owner, chat_id)
            if row['state'] != 'open':
                raise SessionClosed('Chat was deleted')
        return {'chat_id': chat_id, 'state': 'open'}

    def acquire(self, owner, chat_id):
        chat_id = chat_uuid(chat_id)
        with self.lock, self.db() as conn:
            row = self._row(conn, owner, chat_id)
            if row['state'] != 'open':
                raise SessionClosed('Chat was deleted')
            if chat_id not in self.stores:
                self.stores[chat_id] = SessionStore(self, owner, chat_id)
            self.active[chat_id] = self.active.get(chat_id, 0) + 1
            return self.stores[chat_id]

    def release(self, chat_id):
        with self.lock:
            count = self.active.get(chat_id, 0)
            if count <= 1:
                self.active.pop(chat_id, None)
            else:
                self.active[chat_id] = count - 1

    def source(self, owner, chat_id, file_id):
        chat_id, file_id = chat_uuid(chat_id), chat_uuid(file_id)
        with self.lock, self.db() as conn:
            self._row(conn, owner, chat_id)
            # A late output registration must also be cleaned, even after deletion.
            conn.execute('INSERT OR IGNORE INTO source_files VALUES (?, ?)', (chat_id, file_id))
            conn.execute('UPDATE sessions SET acknowledged=0 WHERE id=?', (chat_id,))

    def mark_deleted(self, owner, chat_id):
        chat_id = chat_uuid(chat_id)
        with self.lock, self.db() as conn:
            self._row(conn, owner, chat_id)
            conn.execute("UPDATE sessions SET state='deleting' WHERE id=? AND state='open'", (chat_id,))
        return {'chat_id': chat_id, 'accepted': True}

    def pending(self):
        with self.lock, self.db() as conn:
            result = []
            for row in conn.execute('SELECT * FROM sessions WHERE acknowledged=0').fetchall():
                files = conn.execute('SELECT file_id FROM source_files WHERE chat_id=?', (row['id'],)).fetchall()
                result.append({**dict(row), 'active_operations': self.active.get(row['id'], 0),
                               'file_ids': [f['file_id'] for f in files]})
            return result

    def acknowledge(self, owner, chat_id, file_ids):
        chat_id = chat_uuid(chat_id)
        with self.lock, self.db() as conn:
            row = self._row(conn, owner, chat_id)
            if row['state'] != 'deleted':
                raise AssetError('Runtime cleanup is not finished')
            # Delete only the observed snapshot: do not lose late registrations.
            for file_id in file_ids:
                conn.execute('DELETE FROM source_files WHERE chat_id=? AND file_id=?', (chat_id, chat_uuid(file_id)))
            remaining = conn.execute('SELECT 1 FROM source_files WHERE chat_id=? LIMIT 1', (chat_id,)).fetchone()
            if remaining is None:
                conn.execute('UPDATE sessions SET acknowledged=1 WHERE id=?', (chat_id,))
        return {'ok': True}

    def reap(self):
        with self.lock, self.db() as conn:
            for row in conn.execute("SELECT id FROM sessions WHERE state='deleting'").fetchall():
                chat_id = row['id']
                if self.active.get(chat_id, 0):
                    continue
                directory = self.directories / chat_uuid(chat_id)
                if directory.is_symlink() or directory.resolve().parent != self.directories.resolve():
                    log.error('Refusing invalid session directory: %s', chat_id)
                    continue
                try:
                    if directory.exists():
                        shutil.rmtree(directory)
                    self.stores.pop(chat_id, None)
                    conn.execute("UPDATE sessions SET state='deleted' WHERE id=?", (chat_id,))
                    log.info('Deleted Runtime assets for chat %s', chat_id)
                except OSError:
                    log.exception('Session cleanup failed: %s', chat_id)


class SessionStore(Store):
    def __init__(self, manager, owner, chat_id):
        self.manager, self.owner, self.chat_id = manager, owner, chat_id
        self.depth = ContextVar('store_operation_' + chat_id, default=0)
        super().__init__(manager.directories / chat_id)

    @contextmanager
    def operation(self):
        nested = self.depth.get() > 0
        if not nested:
            self.manager.acquire(self.owner, self.chat_id)
        token = self.depth.set(self.depth.get() + 1)
        try:
            yield
        finally:
            self.depth.reset(token)
            if not nested:
                self.manager.release(self.chat_id)

    def put(self, *args, **kwargs):
        # Keep a worker-thread upload alive even if its HTTP request is cancelled.
        with self.operation():
            return super().put(*args, **kwargs)
