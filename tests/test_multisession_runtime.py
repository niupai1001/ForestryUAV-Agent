"""Exercise actual Engine turns while one tool waits outside the model."""
import asyncio
import threading

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from test_core import task
from test_roles import FixtureFunctionModel
from test_conversation_flow import public_stream_adapter


@pytest.mark.asyncio
async def test_other_session_answers_while_first_session_tool_is_waiting(client, app, monkeypatch):
    first, _ = task(client, harness_mode='H0')
    second, _ = task(client, harness_mode='H0', request_id='second-session')
    engine = app.state.engine
    entered, release = threading.Event(), threading.Event()
    original_write = engine.resources.write

    def slow_write(*args, **kwargs):
        entered.set()
        if not release.wait(8):
            raise RuntimeError('test did not release external tool')
        return original_write(*args, **kwargs)

    monkeypatch.setattr(engine.resources, 'write', slow_write)
    original_agent = engine.make_agent
    calls = {}

    def make_agent(profile, turn, snapshot, task_id, **kwargs):
        agent, channel = original_agent(profile, turn, snapshot, task_id, **kwargs)

        def model(messages, info):
            calls[task_id] = calls.get(task_id, 0) + 1
            if task_id == first['task_id'] and calls[task_id] == 1:
                return ModelResponse(parts=[ToolCallPart('write_file', {
                    'relative_path': 'first.txt', 'content': 'first result'}, 'first-effect')])
            return ModelResponse(parts=[TextPart('当前会话已回答。')])

        agent.model = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.submit(first['task_id'], '保存文件', 'first-turn')
    a = engine.queue[first['task_id']]
    b = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        engine.submit(second['task_id'], '普通问答', 'second-turn')
        b = engine.queue[second['task_id']]
        await asyncio.wait_for(asyncio.shield(b), 3)
        assert not a.done()
        assert engine.store.task(second['task_id'])['messages'][-1]['text'] == '当前会话已回答。'
    finally:
        release.set()
        await a
        if b is not None:
            await b
    assert calls[first['task_id']] == 2 and calls[second['task_id']] == 1
