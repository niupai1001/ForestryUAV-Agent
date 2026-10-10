import hashlib

from fastapi.testclient import TestClient
from test_core import task
from test_knowledge import grant


def imported(client, ref, request_id='import', **extra):
    response = client.post('/knowledge', json={'request_id': request_id, 'resource_id': ref, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_explicit_import_preserves_chinese_conflicts_conditions_and_second_task_scope(client, app, tmp_path):
    first = tmp_path / 'expert.md'
    first.write_text('算术平均应使用真除法。专家意见：合并均值需要按记录数加权。', encoding='utf-8')
    second = tmp_path / 'observations.md'
    second.write_text('专家意见：合并均值不应按记录数加权。测量记录：样本均值37.5。', encoding='utf-8')
    a, b = grant(client, first), grant(client, second, 'source-b')
    original, _ = task(client, input_refs=[a, b])
    knowledge = app.state.engine.knowledge
    knowledge.index(original['task_id'], a)
    assert client.get('/knowledge').json()['imports'] == []
    assert knowledge.search(original['task_id'], '算术平均')['matches'][0]['origin'] == 'task_input_cache'
    imported(client, a, claim_kind='expert_assertion', conditions='合并记录数不同的样本', limitations='软件夹具，不是实测科研结论')
    imported(client, b, 'import-b', claim_kind='measurement', conditions='独立人工样本', limitations='用户分类，尚未科学核验')
    later, _ = task(client, request_id='later', input_refs=[a, b])
    result = client.post(f'/tasks/{later["task_id"]}/knowledge/search', json={'query': '合并均值记录数加权'}).json()
    assert {m['citation']['resource_id'] for m in result['matches']} == {a, b}
    assert result['conflict_resolution'] == 'not_performed'
    assert {entry['claim_kind'] for match in result['matches'] for entry in match['imports']} == {'expert_assertion', 'measurement'}
    assert all(match['imports'][0]['conditions'] and match['imports'][0]['limitations'] for match in result['matches'])
    assert {m['citation']['resource_id'] for m in knowledge.search(later['task_id'], '加权', allowed_refs=[a])['matches']} == {a}
    outsider, _ = task(client, request_id='outsider')
    assert knowledge.search(outsider['task_id'], '加权')['matches'] == []
    assert client.post('/tasks', json={'request_id': 'public-private', 'objective': '读取私密知识', 'context_classification': 'public', 'input_refs': [a]}).status_code == 403
    with app.state.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM user_events WHERE kind='explicit_knowledge_import'").fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == 0


def test_source_revision_replaces_current_index_but_keeps_original_snapshot(client, app, tmp_path):
    source = tmp_path / 'guide.md'
    original = '中文均值指导：使用整数除法。'
    source.write_text(original, encoding='utf-8')
    ref = grant(client, source)
    old = imported(client, ref)
    state, _ = task(client, input_refs=[ref])
    source.write_text('中文均值指导：使用真除法并明确缺失值条件。', encoding='utf-8')
    stale = app.state.engine.knowledge.search(state['task_id'], '中文均值指导')
    assert stale['matches'] == [] and stale['source_notices'][0]['source_status'] == 'stale'
    new = imported(client, ref, 'new')
    current = app.state.engine.knowledge.search(state['task_id'], '中文均值指导')
    assert current['matches'] and all('真除法' in m['text'] for m in current['matches'])
    retained = client.get(f'/knowledge/{old["import_id"]}/content').json()
    assert retained['document']['text'] == original and retained['status'] == 'superseded'
    assert old['citation']['source_sha256'] != new['citation']['source_sha256']
    snapshot = app.state.engine.knowledge._snapshot_path(old['citation']['source_sha256'])
    assert snapshot.read_bytes() == original.encode('utf-8')
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == old['citation']['source_sha256']


def test_import_idempotence_deduplicates_index_without_forged_authorization(client, app, tmp_path):
    source = tmp_path / 'notes.md'
    source.write_text('明确导入知识，保留中文来源。', encoding='utf-8')
    ref = grant(client, source)
    payload = {'request_id': 'same', 'resource_id': ref}
    assert client.post('/knowledge', json={**payload, 'user_event_id': 'forged'}).status_code == 400
    first = client.post('/knowledge', json=payload).json()
    assert client.post('/knowledge', json=payload).json() == first
    assert client.post('/knowledge', json={**payload, 'conditions': 'changed'}).status_code == 409
    assert imported(client, ref, 'same-content')['import_id'] == first['import_id']
    assert client.post('/knowledge', json={'request_id': 'missing', 'resource_id': 'unknown'}).status_code == 403
    assert client.post('/knowledge', json={'request_id': 'escape', 'resource_id': ref, 'relative_path': '../escape.md'}).status_code == 403
    with app.state.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM knowledge_imports').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM knowledge_chunks').fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM user_events WHERE kind='explicit_knowledge_import'").fetchone()[0] == 1
    with TestClient(app, base_url='http://127.0.0.1') as unauthenticated:
        assert unauthenticated.get('/knowledge').status_code == 403
        assert unauthenticated.get(f'/knowledge/{first["import_id"]}/content').status_code == 403


def test_snapshot_corruption_and_revocation_never_fall_back_to_imported_advice(client, app, tmp_path):
    source = tmp_path / 'expert.md'
    source.write_text('中文专家建议：应检查样本条件。', encoding='utf-8')
    ref = grant(client, source)
    note = imported(client, ref)
    state, _ = task(client, input_refs=[ref])
    knowledge = app.state.engine.knowledge
    snapshot = knowledge._snapshot_path(note['citation']['source_sha256'])
    snapshot.write_bytes(b'changed')
    assert knowledge.search(state['task_id'], '中文专家建议')['matches'] == []
    assert client.get(f'/knowledge/{note["import_id"]}/content').status_code == 409
    snapshot.unlink()
    assert client.get(f'/knowledge/{note["import_id"]}/content').status_code == 409
    with app.state.store.transaction() as db:
        db.execute('DELETE FROM resources WHERE id=?', (ref,))
    assert client.post('/knowledge', json={'request_id': 'import', 'resource_id': ref}).status_code == 403
    assert knowledge.search(state['task_id'], '中文专家建议')['matches'] == []
    assert client.get(f'/knowledge/{note["import_id"]}/content').status_code == 403


def test_many_old_versions_do_not_hide_current_source_and_import_pages_share_snapshot(client, app, tmp_path):
    source = tmp_path / 'versions.md'
    ref = None
    for index in range(30):
        source.write_text(f'中文指导版本 {index}：保留当前条件。', encoding='utf-8')
        if ref is None:
            ref = grant(client, source)
        imported(client, ref, f'version-{index}')
    state, _ = task(client, input_refs=[ref])
    matches = app.state.engine.knowledge.search(state['task_id'], '中文指导版本')['matches']
    assert matches and all('版本 29' in m['text'] for m in matches)
    source.write_text('中文知识。' * 800 + '后续适用条件：仅人工样本。', encoding='utf-8')
    page0 = imported(client, ref, 'page0')
    assert page0['has_more'] and page0['next_page'] == 1
    page1 = imported(client, ref, 'page1', page=1)
    assert page1['citation']['source_sha256'] == page0['citation']['source_sha256']
    assert page1['document']['text'] == '后续适用条件：仅人工样本。'
    assert len(list(app.state.engine.knowledge._snapshot_path(page0['citation']['source_sha256']).parent.glob('*.bin'))) == 31
    listing = client.get('/knowledge').json()
    assert len(listing['imports']) == 20 and listing['has_more']
    assert len(client.get(f'/knowledge?offset={listing["next"]}').json()['imports']) == 12


def test_chinese_utf8_character_crossing_window_is_preserved_once(client, app, tmp_path):
    source = tmp_path / 'boundary.md'
    text = 'X' + '中文。' * 2000
    source.write_text(text, encoding='utf-8')
    ref = grant(client, source)
    first, second = imported(client, ref, 'first'), imported(client, ref, 'second', page=1)
    assert first['document']['text'] + second['document']['text'] == text
    assert first['citation']['unit'].split(':')[-1] == second['citation']['unit'].split(':')[1]
    assert '\ufffd' not in first['document']['text'] + second['document']['text']


def test_legacy_text_cache_cannot_override_current_utf8_reader(client, app, tmp_path):
    source = tmp_path / 'legacy.md'
    source.write_text('中文来源的当前有效知识。', encoding='utf-8')
    ref = grant(client, source)
    state, _ = task(client, input_refs=[ref])
    knowledge = app.state.engine.knowledge
    document = app.state.engine.documents.read(state['task_id'], ref)
    legacy = {'citation': {key: value for key, value in document['citation'].items() if key != 'reader_version'},
              'text': '\ufffd旧版缓存：截断了汉字。'}
    with app.state.store.transaction() as db:
        knowledge._index_chunks(db, knowledge._chunks(legacy))
    result = knowledge.search(state['task_id'], '旧版缓存')
    assert not result['matches'] and result['legacy_text_chunks_filtered'] == 1
    knowledge.index(state['task_id'], ref)
    assert knowledge.search(state['task_id'], '当前有效知识')['matches'][0]['citation']['reader_version'] == 'utf8_aligned_windows_v1'
