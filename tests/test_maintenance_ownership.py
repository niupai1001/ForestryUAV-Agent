import json
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_backup import fixture

from forestry_agent.app import create_app
from forestry_agent.backup import create_backup, restore_backup
from forestry_agent.cli import main
from forestry_agent.contracts import Failure
from forestry_agent.store import encode, inspect_task


@pytest.mark.parametrize('operation', ['create', 'restore'])
def test_maintenance_refuses_service_on_different_port_before_copy(tmp_path, operation):
    config, _, _, steps = fixture(tmp_path)
    try:
        backup = tmp_path / 'original-backup'
        create_backup(config, backup)
        app = create_app(config, enable_agent=False)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            other = config.model_copy(update={'port': probe.getsockname()[1]})
        with TestClient(app):
            with pytest.raises(Failure) as error:
                if operation == 'create':
                    create_backup(other, tmp_path / 'must-not-exist')
                else:
                    restore_backup(other, backup)
            assert error.value.code == 'WORKSPACE_IN_USE'
        assert not (tmp_path / 'must-not-exist').exists()
    finally:
        steps.close()


def test_copy_and_restore_hold_same_lock_while_metadata_directory_is_absent(tmp_path, monkeypatch):
    config, _, _, steps = fixture(tmp_path)
    copied = []
    renamed = []
    original_copy = shutil.copy2
    original_rename = Path.rename

    def assert_service_blocked():
        with pytest.raises(Failure) as error:
            create_app(config, enable_agent=False)
        assert error.value.code == 'WORKSPACE_IN_USE'

    def copy(*args, **kwargs):
        if not copied:
            assert_service_blocked()
            copied.append(True)
        return original_copy(*args, **kwargs)

    def rename(source, destination):
        if source.name == '.forestry' and source.parent.name.startswith('.restore-'):
            assert not (config.workspace_root / '.forestry').exists()
            assert_service_blocked()
            renamed.append(True)
        return original_rename(source, destination)

    try:
        monkeypatch.setattr(shutil, 'copy2', copy)
        backup = tmp_path / 'snapshot'
        create_backup(config, backup)
        manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))['manifest']
        assert all(not row['path'].endswith('service.lock') for row in manifest['files'])
        steps.close()
        for managed in ['.forestry', 'tasks']:
            (config.workspace_root / managed).rename(tmp_path / ('retained-' + managed))
        monkeypatch.setattr(Path, 'rename', rename)
        assert restore_backup(config, backup)['restored']
        assert copied == renamed == [True]
    finally:
        steps.close()


def test_task_inspection_reads_committed_wal_without_schema_or_write_transaction(tmp_path, monkeypatch):
    config, store, _, steps = fixture(tmp_path)
    try:
        path = config.workspace_root / '.forestry/state.sqlite'
        with sqlite3.connect(path) as writer:
            writer.execute('PRAGMA wal_autocheckpoint=0')
            state = store.tasks()[0]
            state['objective'] = 'latest WAL state'
            writer.execute('UPDATE tasks SET state=? WHERE id=?', (encode(state), state['task_id']))
            writer.commit()
            assert Path(str(path) + '-wal').stat().st_size > 0
            # A readonly inspection must also work while another writer owns BEGIN IMMEDIATE.
            writer.execute('BEGIN IMMEDIATE')
            calls = []
            connect = sqlite3.connect

            def reader(*args, **kwargs):
                assert kwargs.get('uri') and args[0].endswith('?mode=ro')
                db = connect(*args, **kwargs)
                db.set_trace_callback(calls.append)
                return db

            monkeypatch.setattr(sqlite3, 'connect', reader)
            assert inspect_task(path, state['task_id']) == state
            assert not any(query.upper().startswith(('CREATE', 'INSERT', 'UPDATE', 'BEGIN IMMEDIATE')) for query in calls)
            writer.rollback()
    finally:
        steps.close()


def test_cli_missing_database_does_not_create_workspace(tmp_path, monkeypatch, capsys):
    import forestry_agent.cli as cli

    config, _, _, steps = fixture(tmp_path)
    steps.close()
    config = config.model_copy(update={'workspace_root': tmp_path / 'never-created'})
    monkeypatch.setattr(cli, 'load_config', lambda _: config)
    monkeypatch.setattr(sys, 'argv', ['forestry-agent', 'tasks', 'inspect', '--task', 'missing'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'DATABASE_READ_FAILED'
    assert not config.workspace_root.exists()
