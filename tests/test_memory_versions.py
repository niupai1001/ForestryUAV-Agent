import hashlib
import json

import pytest
from test_core import task

from forestry_agent.contracts import Failure
from forestry_agent.knowledge import Memory
from forestry_agent.store import Store, encode


def test_settings_diagnostics_do_not_probe_deferred_search(client, monkeypatch):
    calls = []

    async def doctor_stub(config, *, probe_search):
        calls.append(probe_search)
        return {"search_probed": probe_search}

    monkeypatch.setattr('forestry_agent.app.doctor', doctor_stub)
    assert client.get('/models/profiles').json() == {"search_probed": False}
    assert calls == [False]


def test_correction_preserves_authorized_versions_and_rejects_stale_writes(client, app):
    note = client.post('/memory', json={"request_id": "save", "text": "均值使用整数除法"}).json()
    url = '/memory/' + note['memory_id']
    update = {"request_id": "correct", "expected_version": 1, "text": "均值使用真除法",
              "source_note": "用户核对三行 CSV", "conditions": "金额为人民币", "limitations": "未覆盖缺失值"}
    assert client.patch(url, json={**update, "user_event_id": "forged"}).status_code == 400
    fixed = client.patch(url, json=update).json()
    assert fixed['version'] == 2 and fixed['text'] == update['text']
    assert client.patch(url, json=update).json() == fixed
    assert client.patch(url, json={**update, "text": "changed"}).status_code == 409
    assert client.patch(url, json={**update, "request_id": "stale"}).status_code == 409
    assert client.request('DELETE', url, json={"request_id": "stale-delete", "expected_version": 1}).status_code == 409
    assert client.get('/memory').json()['notes'] == [fixed]
    retired = client.request('DELETE', url, json={"request_id": "retire", "expected_version": 2}).json()
    assert retired['version'] == 3 and client.get('/memory').json()['notes'] == []
    history = client.get(url + '/versions').json()['versions']
    assert [v['note']['version'] for v in history] == [3, 2, 1]
    assert history[-1]['note']['text'] == note['text']
    assert not history[0]['note']['active']
    assert client.get('/memory?include_inactive=true').json()['notes'][0]['active'] is False
    assert client.patch(url, json={**update, "request_id": "revive", "expected_version": 3}).status_code == 409
    with app.state.store.transaction() as db:
        kinds = [db.execute('SELECT kind FROM user_events WHERE id=?', (v['user_event_id'],)).fetchone()[0] for v in history]
        assert kinds == ['explicit_memory_forget', 'explicit_memory_update', 'explicit_memory_save']


def test_model_reads_only_current_unexpired_in_scope_private_notes(client, app):
    scoped, _ = task(client, request_id='scoped')
    other, _ = task(client, request_id='other')
    public, _ = task(client, request_id='public', context_classification='public')
    memory = app.state.engine.memory
    client.post('/memory', json={"request_id": "global", "text": "所有私密任务使用中文"})
    payload = {"request_id": "scoped-save", "text": "仅当前样地可用", "scope_task_ids": [scoped['task_id']],
               "source_note": "用户提供", "conditions": "闭合林分", "limitations": "不能推广到其他样地"}
    note = client.post('/memory', json=payload).json()
    assert len(memory.list(task_id=scoped['task_id'])['notes']) == 2
    assert [n['text'] for n in memory.list(task_id=other['task_id'])['notes']] == ['所有私密任务使用中文']
    with pytest.raises(Failure, match='公开任务'):
        memory.list(task_id=public['task_id'], include_inactive=True)
    client.patch('/memory/' + note['memory_id'], json={**payload, "request_id": "expire", "expected_version": 1,
                 "text": "过期建议", "expires_at": "2000-01-01T00:00:00Z"})
    assert [n['text'] for n in memory.list(task_id=scoped['task_id'])['notes']] == ['所有私密任务使用中文']
    assert len(client.get('/memory?include_inactive=true').json()['notes']) == 2
    assert client.post('/memory', json={**payload, 'request_id': 'missing-task', 'scope_task_ids': ['missing']}).status_code == 404
    assert client.post('/memory', json={**payload, 'request_id': 'naive', 'expires_at': '2030-01-01T00:00:00'}).status_code == 400
    assert client.post('/memory', json={'request_id': 'blank', 'text': '  '}).status_code == 400
    future = client.post('/memory', json={'request_id': 'utc', 'text': 'UTC有效期',
                         'expires_at': '2030-01-01T08:00:00+08:00'}).json()
    assert future['expires_at'] == '2030-01-01T00:00:00Z' and future['schema_version'] == 1


def test_legacy_migration_keeps_original_bytes_events_and_receipts(tmp_path):
    store = Store(tmp_path / 'legacy.sqlite')
    legacy = {'memory_id': 'old-note', 'text': '旧版中文笔记', 'tags': [], 'classification': 'private',
              'created_at': '2026-01-01T00:00:00+00:00', 'origin': 'explicit_user_note'}
    original_bytes = encode(legacy)
    request = {'kind': 'memory.save', 'request_id': 'legacy-save', 'text': legacy['text'], 'tags': []}
    with store.transaction() as db:
        event = store.user_event(db, 'explicit_memory_save', request)
        db.execute('INSERT INTO memories VALUES(?,?,?)', ('old-note', original_bytes, event))
        store.receipt(db, 'legacy-save', request, legacy)
        db.execute('DELETE FROM schema_versions WHERE version=3')
        user_event_bytes = db.execute('SELECT payload FROM user_events WHERE id=?', (event,)).fetchone()[0]
    reopened = Store(store.path)
    memory = Memory(reopened)
    assert memory.save({key: value for key, value in request.items() if key != 'kind'}) == legacy
    assert memory.list()['notes'][0]['version'] == 1
    memory.update('old-note', {'request_id': 'upgrade', 'expected_version': 1, 'text': '旧记录更正', 'tags': []})
    with reopened.transaction() as db:
        first = db.execute('SELECT * FROM memory_versions WHERE version=1').fetchone()
        assert first['payload'] == original_bytes and first['event_id'] == event
        assert db.execute('SELECT payload FROM user_events WHERE id=?', (event,)).fetchone()[0] == user_event_bytes
    assert len(Memory(Store(store.path)).history('old-note')['versions']) == 2


def test_filtered_pagination_and_history_do_not_reintroduce_retired_versions(client, app):
    state, _ = task(client)
    for index in range(23):
        payload = {'request_id': f'save-{index}', 'text': f'记录 {index}', 'tags': []}
        if index < 2:
            payload['expires_at'] = '2000-01-01T00:00:00+00:00'
        client.post('/memory', json=payload)
    first = app.state.engine.memory.list(task_id=state['task_id'])
    second = app.state.engine.memory.list(first['next'], task_id=state['task_id'])
    assert len(first['notes']) == 20 and first['has_more']
    assert len(second['notes']) == 1 and not second['has_more']
    note = first['notes'][0]
    url = '/memory/' + note['memory_id']
    with app.state.store.transaction() as db:
        original = db.execute('SELECT payload FROM memory_versions WHERE memory_id=? AND version=1', (note['memory_id'],)).fetchone()[0]
    digest = hashlib.sha256(original.encode()).hexdigest()
    for version in range(1, 22):
        assert client.patch(url, json={'request_id': f'edit-{version}', 'expected_version': version, 'text': f'修订 {version}'}).status_code == 200
    history = client.get(url + '/versions').json()
    older = client.get(url + f'/versions?offset={history["next"]}').json()
    assert len(history['versions']) == 20 and history['has_more']
    assert [v['note']['version'] for v in older['versions']] == [2, 1] and not older['has_more']
    with app.state.store.transaction() as db:
        preserved = db.execute('SELECT payload FROM memory_versions WHERE memory_id=? AND version=1', (note['memory_id'],)).fetchone()[0]
    assert hashlib.sha256(preserved.encode()).hexdigest() == digest
    assert json.loads(preserved)['text'] == note['text']
