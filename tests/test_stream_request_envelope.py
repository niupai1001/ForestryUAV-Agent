import json
from pathlib import Path

import httpx2
import pytest
from test_core import task

from forestry_agent.resources import native_path


@pytest.mark.asyncio
async def test_streamed_engine_request_records_exact_sdk_payload_and_commits_answer(client, app, monkeypatch):
    state, _ = task(client, harness_mode='H0')
    engine = app.state.engine
    identity = {**state['config_snapshot']['model_identity'], 'digest': 'software-stream-provider'}
    with engine.store.transaction() as db:
        current = engine.store.read_task(db, state['task_id'])
        current['config_snapshot']['model_identity'] = identity
        engine.store.save_task(db, current, current['state_version'])

    async def identity_probe(*args):
        return identity

    monkeypatch.setattr('forestry_agent.agent.model_identity', identity_probe)
    sent = []
    events = [
        {'id': 'offline-stream', 'object': 'chat.completion.chunk', 'created': 0,
         'model': 'qwen3.5:4b', 'choices': [{'index': 0, 'delta': {
             'role': 'assistant', 'content': '流式软件夹具完整正文。'}, 'finish_reason': None}]},
        {'id': 'offline-stream', 'object': 'chat.completion.chunk', 'created': 0,
         'model': 'qwen3.5:4b', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        {'id': 'offline-stream', 'object': 'chat.completion.chunk', 'created': 0,
         'model': 'qwen3.5:4b', 'choices': [],
         'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}},
    ]
    stream = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'

    async def transport(request):
        sent.append(request.content)
        return httpx2.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)

    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, channel = original(*args, **kwargs)
        channel.client._transport = httpx2.MockTransport(transport)
        return agent, channel

    monkeypatch.setattr(engine, 'make_agent', make_agent)
    engine.submit(state['task_id'], '直接流式回答', 'streamed-wire')
    await engine.queue[state['task_id']]
    current = engine.store.task(state['task_id'])
    assert not current['blockers'] and current['messages'][-1]['text'] == '流式软件夹具完整正文。'
    assert len(sent) == 1 and json.loads(sent[0])['stream'] is True
    with engine.store.snapshot() as db:
        manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id='streamed-wire'").fetchone()[0])
    assert native_path(Path(manifest['wire_attempts'][0]['body_ref'])).read_bytes() == sent[0]
    kinds = {event['type'] for event in engine.store.events(state['task_id'])}
    assert {'model.wire_prepared', 'model.stream_opened', 'run.finished'} <= kinds
