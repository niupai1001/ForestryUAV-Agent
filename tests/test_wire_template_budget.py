import json
from types import SimpleNamespace

import httpx2
import pytest
from test_core import task

from forestry_agent.contracts import Failure
from forestry_agent.runtime.interfaces import RunScope
from forestry_agent.store import encode


@pytest.mark.asyncio
async def test_template_rejection_is_saved_before_transport_is_blocked(client, app, monkeypatch):
    state, _ = task(client)
    engine = app.state.engine
    manifest = {'prepared_input_sha256': 'prepared-hash', 'tool_schema_sha256': 'schema-hash',
                'available_input_tokens': 500}
    with engine.store.transaction() as db:
        db.execute('INSERT INTO contexts VALUES(?,?,?,?)',
            ('wire-budget', state['task_id'], 'wire-budget-turn', encode(manifest)))
    fake_counter = SimpleNamespace(measure_wire=lambda payload: {'status': 'estimated', 'total_tokens': 1000})
    monkeypatch.setattr('forestry_agent.runtime.model_adapter.local_tokenizer', lambda *args: (fake_counter, 'software-fixture'))
    channel = engine.model_adapter.open(engine.config.models['small'], model_identity=state['config_snapshot']['model_identity'])
    sent = []
    channel.client._transport = httpx2.MockTransport(lambda request: sent.append(request) or httpx2.Response(200))
    ctx = SimpleNamespace(deps=SimpleNamespace(scope=RunScope(state['task_id'], 'wire-budget-turn', 1),
        context_manifest_id='wire-budget'))

    async def handler(request_context):
        await channel.client.post('http://127.0.0.1/v1/chat/completions', json={
            'model': 'qwen3.5:4b', 'messages': [{'role': 'user', 'content': 'fixture'}]})

    try:
        with pytest.raises(Failure, match='模板估计'):
            await engine.model_adapter.request_guard(channel, budget_seconds=5)(ctx,
                request_context=None, handler=handler)
        assert not sent
        with engine.store.snapshot() as db:
            saved = json.loads(db.execute("SELECT payload FROM contexts WHERE id='wire-budget'").fetchone()[0])
        assert saved['wire_attempts'][0]['dispatch_state'] == 'blocked_context_budget'
        assert saved['wire_template_measurement']['total_tokens'] == 1000
        assert any(event['type'] == 'model.wire_rejected' for event in engine.store.events(state['task_id']))
    finally:
        await channel.aclose()
