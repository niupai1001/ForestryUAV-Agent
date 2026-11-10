import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from test_conversation_flow import public_stream_adapter
from test_core import task
from test_roles import FixtureFunctionModel

from forestry_agent.runtime.discovery import search
from forestry_agent.runtime.interfaces import ToolDescriptor


@pytest.mark.parametrize('query,expected', [
    ('正射影像重建', 'process_imagery'), ('多光谱LAI反演', 'process_imagery'),
    ('LAZ地形', 'process_lidar_terrain'), ('CSV缺失值', 'code_execute'), ('图像计数', 'view_image')])
def test_bilingual_retrieval_finds_required_capability(query, expected):
    descriptions = {'process_imagery': '正射拼接 多光谱 PROSAIL LAI反演',
        'process_lidar_terrain': 'LAZ点云地形 terrain', 'code_execute': '断网Python代码作业',
        'view_image': '读取图像 image 视觉计数'}
    docs = [ToolDescriptor(name, desc, name.replace('_', ' '), 'test', 'test', name)
            for name, desc in descriptions.items()]
    assert search(docs, query, 1)[0]['name'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('restricted', [False, True])
async def test_discovery_activates_next_step_and_respects_permissions(client, app, monkeypatch, restricted,
                                                                   discovery_mode='bm25'):
    engine = app.state.engine
    engine.config.tool_discovery_mode = discovery_mode
    state, _ = task(client, harness_mode='H1',
        constraints={'allowed_capabilities': ['fs.inputs']} if restricted else {})
    seen = []

    def model(messages, info):
        names = {t.name for t in info.function_tools}
        seen.append(names)
        if len(seen) == 1:
            if restricted and discovery_mode == 'native_bm25' and 'search_tools' not in names:
                assert 'read_user_memory' not in names
                return ModelResponse(parts=[TextPart('当前没有允许发现的额外能力。')])
            assert 'search_tools' in names and 'read_user_memory' not in names
            arguments = ({'queries': ['用户记忆 笔记']} if discovery_mode == 'native_bm25'
                         else {'query': '用户记忆 笔记', 'limit': 1})
            return ModelResponse(parts=[ToolCallPart('search_tools', arguments, 'discover')])
        if len(seen) == 2 and not restricted:
            assert 'read_user_memory' in names
            return ModelResponse(parts=[ToolCallPart('read_user_memory', {}, 'read-memory')])
        if restricted:
            assert 'read_user_memory' not in names
        return ModelResponse(parts=[TextPart('已按当前允许范围回答。')])

    fixture = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original(*args, **kwargs)
        agent.model = fixture
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.submit(state['task_id'], '查找并读取适用的用户笔记', 'discover-owner')
    await engine.queue[state['task_id']]
    current = engine.store.task(state['task_id'])
    assert not current['blockers'] and current['messages'][-1]['role'] == 'assistant'
    events = engine.store.events(state['task_id'])
    snapshots = [e['payload'] for e in events if e['type'] == 'tools.snapshot']
    assert 'read_user_memory' not in snapshots[0]['names']
    if len(snapshots) > 1:
        assert ('read_user_memory' in snapshots[1]['names']) is not restricted
    else:
        assert restricted
    assert all(item['request_id'] and item['schema_hash'] for item in snapshots)
    if not restricted:
        assert len(seen) == 3
        # A fresh run factory restores only still-legal persisted activations.
        engine.submit(state['task_id'], '继续按现有资料回答', 'discovery-restored')
        await engine.queue[state['task_id']]
        await asyncio.sleep(0)
        assert 'read_user_memory' in seen[-1]
    else:
        with engine.store.snapshot() as db:
            assert not db.execute("SELECT 1 FROM actions WHERE task_id=? AND kind='memory.read'", (state['task_id'],)).fetchone()


@pytest.mark.asyncio
@pytest.mark.parametrize('query,expected', [
    ('正射影像重建', 'process_imagery'), ('多光谱LAI反演', 'process_imagery'),
    ('LAZ地形', 'process_lidar_terrain'), ('CSV缺失值', 'code_execute'), ('图像计数', 'view_image')])
async def test_real_registered_catalog_recall_without_inputs(client, app, monkeypatch, query, expected,
                                                           discovery_mode='bm25'):
    engine = app.state.engine
    engine.config.tool_discovery_mode = discovery_mode
    # These identities enable descriptor registration only; no scientific job is run.
    engine.config.imagery_image = 'sha256:' + '1' * 64
    engine.config.science_image = 'sha256:' + '2' * 64
    state, _ = task(client, harness_mode='H1')
    seen = []

    def model(messages, info):
        names = {t.name for t in info.function_tools}
        seen.append(names)
        if len(seen) == 1:
            arguments = {'queries': [query]} if discovery_mode == 'native_bm25' else {'query': query}
            return ModelResponse(parts=[ToolCallPart('search_tools', arguments, 'domain-discovery')])
        assert expected in names
        return ModelResponse(parts=[TextPart('需要用户提供输入，当前未执行科学计算。')])

    fixture = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original(*args, **kwargs)
        agent.model = fixture
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.submit(state['task_id'], query, 'catalog-recall')
    await engine.queue[state['task_id']]
    current = engine.store.task(state['task_id'])
    assert not current['blockers'] and len(seen) == 2
    with engine.store.snapshot() as db:
        assert not db.execute('SELECT 1 FROM jobs WHERE task_id=?', (state['task_id'],)).fetchone()


@pytest.mark.asyncio
async def test_search_cannot_activate_a_hidden_tool_inside_the_same_batch(client, app, monkeypatch,
                                                                       discovery_mode='bm25'):
    engine = app.state.engine
    engine.config.tool_discovery_mode = discovery_mode
    state, _ = task(client, harness_mode='H1')
    requests = []

    def model(messages, info):
        requests.append(messages)
        if len(requests) == 1:
            assert 'read_user_memory' not in {t.name for t in info.function_tools}
            return ModelResponse(parts=[
                ToolCallPart('search_tools', {'queries': ['用户记忆 笔记']} if discovery_mode == 'native_bm25'
                    else {'query': '用户记忆 笔记', 'limit': 1}, 'search'),
                ToolCallPart('read_user_memory', {}, 'guessed-hidden')])
        return ModelResponse(parts=[TextPart('本轮未越过冻结工具边界。')])

    fixture = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original(*args, **kwargs)
        agent.model = fixture
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.submit(state['task_id'], '查找用户笔记', 'same-batch-boundary')
    await engine.queue[state['task_id']]
    current = engine.store.task(state['task_id'])
    assert not current['blockers'] and current['messages'][-1]['role'] == 'assistant'
    with engine.store.snapshot() as db:
        assert not db.execute("SELECT 1 FROM actions WHERE task_id=? AND kind='memory.read'", (state['task_id'],)).fetchone()
