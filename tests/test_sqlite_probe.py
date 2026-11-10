"""Exception diagnostics must not write databases, sidecars or probe files."""
import sqlite3
import tempfile

from forestry_agent.agent import sqlite_probe


def snapshot(root):
    return {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.iterdir()}


def test_probe_uses_escaped_immutable_uri_and_never_creates_files(tmp_path, monkeypatch):
    root = tmp_path/'diagnostics # snapshot'
    folder = root/'.forestry'
    folder.mkdir(parents=True)
    path = folder/'state.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE evidence(value TEXT)')
    before = snapshot(folder)
    original = sqlite3.connect
    uris = []

    def readonly(database, **kwargs):
        assert 'mode=ro&immutable=1' in database and '%23' in database and kwargs['uri']
        uris.append(database)
        return original(database, **kwargs)

    def no_temp_write(*args, **kwargs):
        raise AssertionError('Diagnostic attempted a write probe')

    monkeypatch.setattr(sqlite3,'connect',readonly)
    monkeypatch.setattr(tempfile,'NamedTemporaryFile',no_temp_write)
    result = sqlite_probe(root)
    assert len(uris) == 1 and 'tables=1' in result, result
    assert 'db_write=unknown' in result and 'dir_write=ok' not in result
    assert snapshot(folder) == before


def test_wal_probe_preserves_live_sidecars_and_marks_committed_state_unknown(tmp_path):
    folder = tmp_path/'.forestry'
    folder.mkdir()
    writer = sqlite3.connect(folder/'state.sqlite')
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE pending_wal(value TEXT)')
        writer.commit()
        before = snapshot(folder)
        assert 'state.sqlite-wal' in before
        result = sqlite_probe(tmp_path)
        assert 'committed_state=unknown' in result
        assert snapshot(folder) == before
    finally:
        writer.close()


def test_missing_or_corrupt_database_is_not_created_or_misreported_as_write_failure(tmp_path):
    missing = tmp_path/'absent'
    assert 'stat failed' in sqlite_probe(missing) and not missing.exists()
    folder = tmp_path/'.forestry'
    folder.mkdir()
    (folder/'state.sqlite').write_bytes(b'not a database')
    before = snapshot(folder)
    result = sqlite_probe(tmp_path)
    assert 'read=FAILED' in result and 'write=FAILED' not in result
    assert snapshot(folder) == before
