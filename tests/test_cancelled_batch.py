import asyncio
import threading

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from test_conversation_flow import public_stream_adapter
from test_core import task
from test_roles import FixtureFunctionModel


@pytest.mark.asyncio
async def test_cancel_drains_started_effect_and_pairs_unstarted_sibling(client, app, monkeypatch):
    state, _ = task(client, harness_mode='H0')
    engine = app.state.engine
    entered, release = threading.Event(), threading.Event()
    writes = []
    original_write = engine.resources.write

    def write(*args, **kwargs):
        writes.append(args[2])
        entered.set()
        if not release.wait(8):
            raise RuntimeError('test failed to release started effect')
        return original_write(*args, **kwargs)

    monkeypatch.setattr(engine.resources, 'write', write)

    requests = []

    def model(messages, info):
        requests.append(messages)
        if len(requests) > 1:
            return ModelResponse(parts=[TextPart('只解释已保存结果，不重复写入。')])
        return ModelResponse(parts=[ToolCallPart('write_file', {
            'relative_path': 'started.txt', 'content': 'real bytes'}, 'started'),
            ToolCallPart('write_file', {'relative_path': 'never.txt', 'content': 'never'}, 'not-started')])

    fixture = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
    original_agent = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original_agent(*args, **kwargs)
        agent.model = fixture
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.submit(state['task_id'], '依次保存两个文件', 'cancelled-batch')
    run = engine.queue[state['task_id']]
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        current = engine.store.task(state['task_id'])
        result = client.post(f"/tasks/{state['task_id']}/pause", json={
            'request_id': 'pause-batch', 'expected_state_version': current['state_version']})
        assert result.status_code == 200, result.text
        await asyncio.sleep(0.05)
        assert not run.done(), 'started effect must settle before the exclusive scope is released'
    finally:
        release.set()
        await run
    assert writes == ['started.txt']
    with engine.store.snapshot() as db:
        actions = db.execute('SELECT call_id,status,result FROM actions WHERE task_id=?', (state['task_id'],)).fetchall()
        assert len(actions) == 1 and actions[0]['status'] == 'confirmed'
    snapshot = await engine.latest_checkpoint('cancelled-batch')
    assert snapshot is not None
    assert engine.open_tool_calls(snapshot.messages) == []
    returns = {p.tool_call_id: p.content for m in snapshot.messages if isinstance(m, ModelRequest)
        for p in m.parts if isinstance(p, ToolReturnPart)}
    assert returns['started']['kind'] == 'completed'
    assert returns['not-started']['kind'] == 'cancelled'
    assert engine.store.task(state['task_id'])['status'] == 'paused'
    engine.store.change(state['task_id'], 'task.resume_requested',
        lambda s, db: s.update(status='created', pause_requested=False))
    engine.submit(state['task_id'], '只解释已保存结果', 'after-cancel')
    await engine.queue[state['task_id']]
    assert len(requests) == 2 and writes == ['started.txt']
    current = engine.store.task(state['task_id'])
    assert not current['blockers'] and current['messages'][-1]['text'] == '只解释已保存结果，不重复写入。'
