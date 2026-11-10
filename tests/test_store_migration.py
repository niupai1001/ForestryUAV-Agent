import json
import sqlite3

import pytest
from test_backup import fixture

from forestry_agent.backup import create_backup, restore_backup
from forestry_agent.contracts import Failure
from forestry_agent.resources import Resources
from forestry_agent.store import Store


def schema(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()


@pytest.mark.parametrize('legacy', [False, True])
def test_schema_and_data_migrations_rollback_together_and_reenter(tmp_path, monkeypatch, legacy):
    path = tmp_path / 'state.sqlite'
    before_state = None
    if legacy:
        store = Store(path)
        before_state = store.create_task({'request_id': 'legacy', 'objective': '保留原目标', 'input_refs': []}, {'model_profile': 'small'})
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE job_deliveries')
            db.execute('DROP TABLE turn_continuations')
            db.execute('DELETE FROM schema_versions WHERE version>=5')
    before_schema = schema(path)
    connect = sqlite3.connect
    class InterruptedMigration(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if 'INTO schema_versions VALUES(5,' in sql:
                raise RuntimeError('controlled migration interruption after DDL')
            return super().execute(sql, parameters)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *args, **kwargs: connect(*args, factory=InterruptedMigration, **kwargs))
        with pytest.raises(RuntimeError, match='controlled migration interruption'):
            Store(path)
    assert schema(path) == before_schema
    migrated = Store(path)
    with migrated.snapshot() as db:
        assert [row[0] for row in db.execute('SELECT version FROM schema_versions ORDER BY version')] == [1,2,3,4,5,6]
    if before_state:
        assert migrated.task(before_state['task_id']) == before_state
    once = schema(path)
    Store(path)
    assert schema(path) == once


def test_newer_schema_refused_without_downgrade_or_bytes_changed(tmp_path):
    path = tmp_path / 'future.sqlite'
    store = Store(path)
    with store.transaction() as db:
        db.execute("INSERT INTO schema_versions VALUES(7,'future')")
    before = path.read_bytes()
    with pytest.raises(Failure) as error:
        Store(path)
    assert error.value.code == 'SCHEMA_UNSUPPORTED'
    assert path.read_bytes() == before


def test_rollback_preserves_post_release_messages_and_products_without_overwriting_live_data(tmp_path):
    config, store, _, steps = fixture(tmp_path)
    steps.close()
    baseline = tmp_path / 'pre-release'
    create_backup(config, baseline)
    task_id = store.tasks()[0]['task_id']
    store.change(task_id, 'user.new-message', lambda state, db: state['messages'].append(
        {'id':'after-release', 'role':'user', 'text':'发布后新增要求不能丢失'}))
    product = Resources(store, config.workspace_root).write(task_id, 'post-release', '新增结果.md', '发布后新增结果')
    current = tmp_path / 'post-release'
    assert create_backup(config, current)['verified']
    with pytest.raises(Failure, match='已存在'):
        restore_backup(config, baseline)
    assert store.task(task_id)['messages'][-1]['id'] == 'after-release'
    retained = tmp_path / 'retained-current'
    retained.mkdir()
    for managed in ['.forestry', 'tasks']:
        source = (config.workspace_root / managed).resolve()
        target = (retained / managed).resolve()
        assert source.is_relative_to(tmp_path.resolve()) and target.is_relative_to(tmp_path.resolve())
        source.rename(target)
    assert restore_backup(config, baseline)['restored']
    with sqlite3.connect(current / 'payload/.forestry/state.sqlite') as db:
        archived = json.loads(db.execute('SELECT state FROM tasks WHERE id=?', (task_id,)).fetchone()[0])
    assert archived['messages'][-1]['id'] == 'after-release'
    assert product['artifact_id'] in archived['artifact_ids']
    relative = product['locator'].split(str(config.workspace_root))[1].lstrip('\\/')
    assert (current / 'payload' / relative).read_text(encoding='utf-8') == '发布后新增结果'
    assert (retained / relative).read_text(encoding='utf-8') == '发布后新增结果'
    assert not Store(config.workspace_root / '.forestry/state.sqlite').task(task_id)['messages']
    # Returning from the baseline must restore the complete later snapshot,
    # rather than merging selected tables and dropping their event relationships.
    rolled_back = tmp_path / 'retained-baseline'
    rolled_back.mkdir()
    for managed in ['.forestry', 'tasks']:
        source = (config.workspace_root / managed).resolve()
        target = (rolled_back / managed).resolve()
        assert source.is_relative_to(tmp_path.resolve()) and target.is_relative_to(tmp_path.resolve())
        source.rename(target)
    assert restore_backup(config, current)['restored']
    recovered = Store(config.workspace_root / '.forestry/state.sqlite')
    assert recovered.task(task_id) == archived
    assert (config.workspace_root / relative).read_text(encoding='utf-8') == '发布后新增结果'
    with recovered.snapshot() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT payload FROM artifacts WHERE id=?', (product['artifact_id'],)).fetchone()
    recovered.change(task_id, 'user.continue-after-restore', lambda state, db: state['messages'].append(
        {'id':'after-restore', 'role':'user', 'text':'恢复后继续使用'}))
    assert recovered.task(task_id)['messages'][-2:] == [archived['messages'][-1],
        {'id':'after-restore', 'role':'user', 'text':'恢复后继续使用'}]
