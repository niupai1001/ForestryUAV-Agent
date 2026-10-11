"""Offline snapshots preserve real SQLite rows and bytes; no model or Worker."""
import hashlib
import json
import socket
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from forestry_agent.backup import create_backup, inspect_backup, restore_backup
from forestry_agent.config import Config, ModelProfile
from forestry_agent.contracts import Failure
from forestry_agent.resources import Resources
from forestry_agent.store import Store, encode


def fixture(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    config = Config(workspace_root=tmp_path / '运行', port=port,
                    models={'small': ModelProfile(repository='Qwen/Qwen3.5-4B', served_name='qwen3.5:4b')})
    store = Store(config.workspace_root / '.forestry/state.sqlite')
    state = store.create_task({'request_id': 'offline-fixture', 'objective': '保留历史，不执行代码', 'input_refs': []},
                              {'model_profile': 'small'})
    store.change(state['task_id'], 'fixture.pause', lambda s, _: s.update(status='paused'))
    resources = Resources(store, config.workspace_root)
    artifact = resources.write(state['task_id'], 'operator-fixture', '中文报告.md', '确定的离线报告\n')
    key = config.workspace_root / '.forestry/local.key'
    key.write_text('local-test-only-key', encoding='utf-8')
    steps = sqlite3.connect(config.workspace_root / '.forestry/model-steps.sqlite')
    steps.execute('PRAGMA journal_mode=WAL')
    steps.execute('CREATE TABLE evidence(value TEXT)')
    steps.execute('INSERT INTO evidence VALUES(?)', ('检查点',))
    steps.commit()
    return config, store, artifact, steps


def test_roundtrip_preserves_wal_state_checkpoint_history_artifact_and_session(tmp_path):
    config, store, artifact, steps = fixture(tmp_path)
    try:
        with store.transaction() as db:
            before = {table: db.execute(f'SELECT * FROM {table}').fetchall()
                      for table in ['tasks', 'specs', 'events', 'user_events', 'artifacts', 'requests']}
        before = {k: [tuple(row) for row in rows] for k, rows in before.items()}
        backup = tmp_path / '备份'
        report = create_backup(config, backup)
        assert report['verified'] and report['classification'] == 'private'
        assert not any(i['path'].endswith(('.sqlite-wal', '.sqlite-shm'))
                       for i in json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))['manifest']['files'])
        steps.close()
        for managed in ['.forestry', 'tasks']:
            (config.workspace_root / managed).rename(tmp_path / ('保留-' + managed))
        restored = restore_backup(config, backup)
        assert restored['restored'] and not restored['service_started']
        assert restored['model_dispatch'] == restored['job_dispatch'] == 0
        actual = Store(config.workspace_root / '.forestry/state.sqlite')
        with actual.transaction() as db:
            assert all([tuple(row) for row in db.execute(f'SELECT * FROM {table}')] == rows for table, rows in before.items())
        assert Path(artifact['locator']).read_text(encoding='utf-8') == '确定的离线报告\n'
        assert (config.workspace_root / '.forestry/local.key').read_text() == 'local-test-only-key'
        with closing(sqlite3.connect(config.workspace_root / '.forestry/model-steps.sqlite')) as db:
            assert db.execute('SELECT value FROM evidence').fetchone()[0] == '检查点'
        with pytest.raises(Failure, match='已存在'):
            restore_backup(config, backup)
        with pytest.raises(Failure, match='已存在'):
            create_backup(config, backup)
    finally:
        steps.close()


def test_live_endpoint_and_unreconciled_effect_refuse_before_copy(tmp_path):
    config, store, _, steps = fixture(tmp_path)
    try:
        with socket.socket() as server:
            server.bind(('127.0.0.1', config.port))
            server.listen(1)
            with pytest.raises(Failure, match='停止'):
                create_backup(config, tmp_path / 'live')
        with store.transaction() as db:
            task_id = db.execute('SELECT id FROM tasks').fetchone()[0]
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)',
                       ('pending', task_id, 'turn', 'call', 1, 'code.execute', '{}', 'pending', None))
        with pytest.raises(Failure, match='未决副作用'):
            create_backup(config, tmp_path / 'pending')
        assert not (tmp_path / 'live').exists() and not (tmp_path / 'pending').exists()
    finally:
        steps.close()


def test_tampered_bytes_and_forged_traversal_never_publish_restore(tmp_path):
    config, _, artifact, steps = fixture(tmp_path)
    try:
        backup = tmp_path / 'safe'
        create_backup(config, backup)
        relative = Path(artifact['locator']).relative_to(config.workspace_root)
        (backup / 'payload' / relative).write_bytes(b'changed')
        with pytest.raises(Failure, match='摘要'):
            inspect_backup(backup)
        (backup / 'payload' / relative).write_text('确定的离线报告\n', encoding='utf-8')
        path = backup / 'manifest.json'
        envelope = json.loads(path.read_text(encoding='utf-8'))
        envelope['manifest']['files'][0]['path'] = '.forestry/../../outside'
        envelope['manifest_sha256'] = hashlib.sha256(encode(envelope['manifest']).encode()).hexdigest()
        path.write_text(json.dumps(envelope), encoding='utf-8')
        with pytest.raises(Failure, match='不安全路径'):
            inspect_backup(backup)
        assert (config.workspace_root / '.forestry/state.sqlite').exists()
    finally:
        steps.close()


def test_backup_must_be_separate_and_restore_preserves_absolute_identity(tmp_path):
    config, _, _, steps = fixture(tmp_path)
    try:
        with pytest.raises(Failure, match='分开'):
            create_backup(config, config.workspace_root / 'backup')
        backup = tmp_path / 'safe'
        create_backup(config, backup)
        config.workspace_root = tmp_path / 'other'
        with pytest.raises(Failure, match='原工作区'):
            restore_backup(config, backup)
        assert not config.workspace_root.exists()
    finally:
        steps.close()


def test_empty_checkpoint_store_excludes_own_journal_but_keeps_named_products(tmp_path):
    config, store, _, steps = fixture(tmp_path)
    steps.close()
    checkpoint = config.workspace_root / '.forestry/model-steps.sqlite'
    checkpoint.write_bytes(b'')  # Real lazy harness initialization before any model run.
    with store.transaction() as db:
        task_id = db.execute('SELECT id FROM tasks').fetchone()[0]
    product = Resources(store, config.workspace_root).write(task_id, 'operator', 'result.sqlite-wal', 'real product')
    backup = tmp_path / 'empty-checkpoint'
    assert create_backup(config, backup)['verified']
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))['manifest']
    paths = {i['path'] for i in manifest['files']}
    assert '.forestry/model-steps.sqlite-journal' not in paths
    assert Path(product['locator']).relative_to(config.workspace_root).as_posix() in paths
    with closing(sqlite3.connect(backup / 'payload/.forestry/model-steps.sqlite')) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
