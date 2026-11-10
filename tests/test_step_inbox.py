import asyncio

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from test_core import task
from test_roles import FixtureFunctionModel
from test_conversation_flow import public_stream_adapter

from forestry_agent.runtime.session_repository import StoreSessionRepository
from forestry_agent.store import now


def enqueue(client, state, key, text):
    result = client.post(f"/tasks/{state['task_id']}/messages", json={
        'request_id': key, 'expected_spec_revision': 1, 'text': text})
    assert result.status_code == 200, result.text
    return result.json()


def test_step_claim_is_durable_ordered_and_single_use(client, app):
    state, _ = task(client)
    store = app.state.store
    with store.transaction() as db:
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',
            ('owner', state['task_id'], 1, 'original', 'running', now()))
    enqueue(client, state, 'steer-one', '先检查单位')
    enqueue(client, state, 'steer-two', '再说明误差')
    repo = app.state.engine.sessions
    assert repo.unrepresented_requests(state['task_id'], 'owner', after_turn_id=None) == []
    claimed = repo.claim_step_input(state['task_id'], 'owner', 1)
    assert [row['prompt'] for row in claimed] == ['先检查单位', '再说明误差']
    assert StoreSessionRepository(store).claim_step_input(state['task_id'], 'owner', 1) == []
    # Without a checkpoint, claimed input remains recoverable from original facts.
    assert [item.text for item in repo.committed_requests(state['task_id'])][-2:] == ['先检查单位', '再说明误差']
    assert len([e for e in store.events(state['task_id']) if e['type'] == 'inbox.claimed']) == 2


@pytest.mark.asyncio
async def test_new_user_input_reaches_next_native_step_without_second_turn(client, app, monkeypatch):
    state, _ = task(client, harness_mode='H0')
    engine = app.state.engine
    requests = []

    def model(messages, info):
        requests.append(messages)
        if len(requests) == 1:
            enqueue(client, state, 'live-steer', '停止增加文件，说明当前结果')
            return ModelResponse(parts=[ToolCallPart('write_file', {
                'relative_path': 'result.txt', 'content': 'verified'}, 'one-write')])
        text = '\n'.join(part.content for msg in messages if isinstance(msg, ModelRequest)
            for part in msg.parts if isinstance(part, UserPromptPart) and isinstance(part.content, str))
        assert text.count('停止增加文件，说明当前结果') == 1
        return ModelResponse(parts=[TextPart('当前结果已保存，只说明结果。')])

    fixture = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original(*args, **kwargs)
        agent.model = fixture
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    owner = engine.submit(state['task_id'], '保存一次结果', 'live-owner')
    await engine.queue[state['task_id']]
    await asyncio.sleep(0)
    assert len(requests) == 2
    with engine.store.snapshot() as db:
        queued = db.execute("SELECT COUNT(*) FROM turns WHERE task_id=? AND status='queued'", (state['task_id'],)).fetchone()[0]
        claimed = db.execute("SELECT status FROM turns t JOIN turn_requests r ON t.id=r.turn_id WHERE r.request_id='live-steer'").fetchone()[0]
    assert queued == 0 and claimed == 'steered'
    assert engine.store.task(state['task_id'])['active_turn_id'] == owner
    assert engine.store.task(state['task_id'])['messages'][-1]['role'] == 'assistant'
    assert not engine.store.task(state['task_id'])['blockers']
    checkpoint = await engine.latest_checkpoint(owner)
    assert checkpoint is not None
    persisted = '\n'.join(part.content for msg in checkpoint.messages if isinstance(msg, ModelRequest)
        for part in msg.parts if isinstance(part, UserPromptPart) and isinstance(part.content, str))
    assert persisted.count('停止增加文件，说明当前结果') == 1
    # A later turn inherits the checkpoint and must not carry the steered input twice.
    engine.submit(state['task_id'], '继续说明', 'after-steer')
    await engine.queue[state['task_id']]
    assert len(requests) == 3
    assert engine.store.task(state['task_id'])['messages'][-1]['text'] == '当前结果已保存，只说明结果。'


@pytest.mark.asyncio
async def test_message_arriving_with_final_response_starts_exactly_one_next_turn(client, app, monkeypatch):
    state, _ = task(client, harness_mode='H0')
    engine = app.state.engine
    requests = []

    def model(messages, info):
        requests.append(messages)
        if len(requests) == 1:
            enqueue(client, state, 'finish-boundary-input', '现在回答第二个问题')
            return ModelResponse(parts=[TextPart('第一个问题已回答。')])
        assert len(requests) == 2
        return ModelResponse(parts=[TextPart('第二个问题已回答。')])

    fixture = FixtureFunctionModel(model, stream_function=public_stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original(*args, **kwargs)
        agent.model = fixture
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.submit(state['task_id'], '第一个问题', 'finishing-owner')
    first = engine.queue[state['task_id']]
    await first
    await asyncio.sleep(0)
    second = engine.queue[state['task_id']]
    assert second is not first
    await second
    await asyncio.sleep(0)
    assert len(requests) == 2
    current = engine.store.task(state['task_id'])
    assert not current['blockers']
    assert [m['text'] for m in current['messages'] if m['role'] == 'assistant'] == [
        '第一个问题已回答。', '第二个问题已回答。']
    assert engine.start_queued(state['task_id']) is None
    with engine.store.snapshot() as db:
        rows = db.execute('SELECT status FROM turns WHERE task_id=?', (state['task_id'],)).fetchall()
        assert len(rows) == 2 and all(row['status'] == 'completed' for row in rows)
