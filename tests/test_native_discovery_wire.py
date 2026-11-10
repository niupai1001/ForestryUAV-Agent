import json

import httpx2
import pytest
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.runtime.discovery import DISCOVERY_GUIDANCE


@pytest.mark.asyncio
async def test_native_search_changes_next_actual_sdk_schema_not_current_request(client, app, monkeypatch):
    engine = app.state.engine
    engine.config.tool_discovery_mode = 'native_bm25'
    state, _ = task(client, harness_mode='H1')
    identity = {**state['config_snapshot']['model_identity'], 'digest': 'software-native-search-http'}
    with engine.store.transaction() as db:
        current = engine.store.read_task(db, state['task_id'])
        current['config_snapshot']['model_identity'] = identity
        engine.store.save_task(db, current, current['state_version'])

    async def identity_probe(*args):
        return identity

    monkeypatch.setattr('forestry_agent.agent.model_identity', identity_probe)
    current = engine.store.task(state['task_id'])
    agent, channel = engine.make_agent('small', 'native-search-sdk', current['config_snapshot'], state['task_id'])
    requests = []

    async def transport(request):
        body = json.loads(request.content)
        names = {tool['function']['name'] for tool in body['tools']}
        requests.append(body)
        if len(requests) == 1:
            assert 'search_tools' in names and 'read_user_memory' not in names
            search = next(tool['function'] for tool in body['tools'] if tool['function']['name'] == 'search_tools')
            assert search['description'] == DISCOVERY_GUIDANCE
            assert '不必知道工具名' in search['parameters']['properties']['queries']['description']
            assert DISCOVERY_GUIDANCE in '\n'.join(message.get('content') or '' for message in body['messages']
                                                  if message['role'] in {'system', 'developer'})
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'native-find', 'type': 'function', 'function': {
                    'name': 'search_tools', 'arguments': json.dumps({'queries': ['用户记忆 笔记']})}}]}
            finish = 'tool_calls'
        else:
            assert len(requests) == 2 and 'read_user_memory' in names
            message, finish = {'role': 'assistant', 'content': '已发现合法笔记读取能力。'}, 'stop'
        return httpx2.Response(200, json={'id': 'native-search-response', 'object': 'chat.completion',
            'created': 0, 'model': 'qwen3.5:4b', 'choices': [{'index': 0, 'message': message,
                'finish_reason': finish}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 105}})

    channel.client._transport = httpx2.MockTransport(transport)
    try:
        result = await agent.run('查找可用用户笔记能力', deps=Deps(engine, state['task_id'], 'native-search-sdk', 1))
        assert result.output.response == '已发现合法笔记读取能力。'
    finally:
        await channel.aclose()
    assert len(requests) == 2
    snapshots = [e['payload'] for e in engine.store.events(state['task_id']) if e['type'] == 'tools.snapshot']
    assert 'read_user_memory' not in snapshots[0]['names']
    assert 'read_user_memory' in snapshots[1]['names']
    with engine.store.snapshot() as db:
        assert not db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='tools.activated'", (state['task_id'],)).fetchone()
        assert db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='tools.search_ranked'", (state['task_id'],)).fetchone()
