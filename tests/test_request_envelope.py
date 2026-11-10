import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from test_core import task

from forestry_agent.contracts import Failure
from forestry_agent.runtime.interfaces import RunScope
from forestry_agent.runtime.model_adapter import LocalModelAdapter
from forestry_agent.store import encode
from forestry_agent.resources import native_path
from forestry_agent.agent import Deps


@pytest.mark.asyncio
async def test_exact_provider_bytes_and_distinct_attempts_are_saved_without_credentials(app):
    recorded, sent = [], []
    adapter = LocalModelAdapter(app.state.engine.config, lambda *args: None,
        record_request=lambda scope, envelope: recorded.append(envelope))
    profile = app.state.engine.config.models['small']
    channel = adapter.open(profile, model_identity={'digest': 'frozen-model'})
    async def transport(request):
        sent.append(request.content)
        return httpx2.Response(200, json={'ok': True})
    channel.client._transport = httpx2.MockTransport(transport)
    scope = RunScope('envelope-task', 'envelope-turn', 1)
    ctx = SimpleNamespace(deps=SimpleNamespace(scope=scope, context_manifest_id='prepared-input'))
    body = b'{ "model":"qwen3.5:4b", "messages":[{"content":"private-content"}], "tools":[], "stream":true }'

    async def handler(request_context):
        for _ in range(2):
            await channel.client.post('http://127.0.0.1/v1/chat/completions?key=query-only-secret',
                content=body, headers={'Authorization': 'Bearer header-only-secret'})

    try:
        await adapter.request_guard(channel, budget_seconds=5)(ctx, request_context=None, handler=handler)
    finally:
        await channel.aclose()
    assert sent == [body, body] and len(recorded) == 2
    assert recorded[0]['attempt_id'] != recorded[1]['attempt_id']
    for item in recorded:
        assert native_path(Path(item['body_ref'])).read_bytes() == body
        assert item['body_sha256'] == hashlib.sha256(body).hexdigest()
        assert item['model_identity'] == {'digest': 'frozen-model'}
        assert item['endpoint'] == 'http://127.0.0.1/v1/chat/completions'
    files = list((app.state.engine.config.workspace_root / '.forestry/model-inputs').rglob('*.json'))
    assert len(files) == 4
    assert all(b'header-only-secret' not in native_path(p).read_bytes() and b'query-only-secret' not in native_path(p).read_bytes() for p in files)


@pytest.mark.asyncio
async def test_native_sdk_request_is_recorded_after_provider_transformation(client, app, monkeypatch):
    state, _ = task(client, harness_mode='H0')
    engine = app.state.engine
    identity = {**state['config_snapshot']['model_identity'], 'digest': 'software-http-provider'}
    with engine.store.transaction() as db:
        current = engine.store.read_task(db, state['task_id'])
        current['config_snapshot']['model_identity'] = identity
        engine.store.save_task(db, current, current['state_version'])
    current = engine.store.task(state['task_id'])

    async def identity_probe(*args):
        return identity

    monkeypatch.setattr('forestry_agent.agent.model_identity', identity_probe)
    agent, channel = engine.make_agent('small', 'sdk-wire', current['config_snapshot'], state['task_id'])
    sent = []

    async def transport(request):
        sent.append(request.content)
        return httpx2.Response(200, json={'id': 'offline-response', 'object': 'chat.completion',
            'created': 0, 'model': 'qwen3.5:4b', 'choices': [{'index': 0, 'message': {
                'role': 'assistant', 'content': '软件HTTP夹具回答。'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}})

    channel.client._transport = httpx2.MockTransport(transport)
    try:
        result = await agent.run('直接回答', deps=Deps(engine, state['task_id'], 'sdk-wire', 1))
        assert result.output.response == '软件HTTP夹具回答。'
    finally:
        await channel.aclose()
    assert len(sent) == 1
    with engine.store.snapshot() as db:
        manifest = json.loads(db.execute("SELECT payload FROM contexts WHERE turn_id='sdk-wire'").fetchone()[0])
    envelope = manifest['wire_attempts'][0]
    assert native_path(Path(envelope['body_ref'])).read_bytes() == sent[0]
    wire = json.loads(sent[0])
    assert wire['model'] == 'qwen3.5:4b' and wire['messages'] and wire['tools']
    assert envelope['model_identity']['digest'] == 'software-http-provider'



@pytest.mark.asyncio
async def test_unbound_request_is_rejected_before_transport(app):
    adapter = LocalModelAdapter(app.state.engine.config, lambda *args: None)
    channel = adapter.open(app.state.engine.config.models['small'])
    sent = []
    channel.client._transport = httpx2.MockTransport(lambda req: sent.append(req) or httpx2.Response(200))
    try:
        with pytest.raises(Failure, match='输入身份'):
            await channel.client.post('http://127.0.0.1/v1/chat/completions', json={'model': 'test'})
        assert not sent
    finally:
        await channel.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('paused,wrong_model', [(False, False), (True, False), (False, True)])
async def test_wire_record_is_bound_to_manifest_and_control_before_send(client, app, paused, wrong_model):
    state, _ = task(client)
    engine = app.state.engine
    manifest = {'prepared_input_sha256': 'application-hash', 'tool_schema_sha256': 'schema-hash'}
    with engine.store.transaction() as db:
        db.execute('INSERT INTO contexts VALUES(?,?,?,?)', ('prepared-real', state['task_id'], 'wire-turn', encode(manifest)))
        if paused:
            current = engine.store.read_task(db, state['task_id'])
            current['pause_requested'] = True
            engine.store.save_task(db, current, current['state_version'])
    channel = engine.model_adapter.open(engine.config.models['small'], model_identity=state['config_snapshot']['model_identity'])
    sent = []
    channel.client._transport = httpx2.MockTransport(lambda req: sent.append(req) or httpx2.Response(200))
    ctx = SimpleNamespace(deps=SimpleNamespace(scope=RunScope(state['task_id'], 'wire-turn', 1),
        context_manifest_id='prepared-real'))

    async def handler(request_context):
        await channel.client.post('http://127.0.0.1/v1/chat/completions',
            json={'model': 'different-model' if wrong_model else 'qwen3.5:4b'})

    try:
        guard = engine.model_adapter.request_guard(channel, budget_seconds=5)
        if paused or wrong_model:
            with pytest.raises(Failure, match='发送前' if paused else 'model字段'):
                await guard(ctx, request_context=None, handler=handler)
            assert not sent
        else:
            await guard(ctx, request_context=None, handler=handler)
            assert len(sent) == 1
            with engine.store.snapshot() as db:
                saved = json.loads(db.execute("SELECT payload FROM contexts WHERE id='prepared-real'").fetchone()[0])
            assert saved['wire_attempts'][0]['tool_schema_sha256'] == 'schema-hash'
            assert 'wire_attempts' not in engine.contexts.public(saved)
            public_event = next(e['payload'] for e in engine.store.events(state['task_id']) if e['type'] == 'model.wire_prepared')
            assert 'body_ref' not in public_event
    finally:
        await channel.aclose()
