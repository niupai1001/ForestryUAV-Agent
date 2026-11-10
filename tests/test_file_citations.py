import hashlib
import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from test_core import task
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps


@pytest.mark.parametrize('bom', [False, True])
def test_full_read_citation_names_actual_source_bytes_not_hashes_in_content(client, app, tmp_path, bom):
    raw = (b'\xef\xbb\xbf' if bom else b'') + b'{"lut_sha256":"' + b'f' * 64 + b'"}'
    source = tmp_path / 'result.json'
    source.write_bytes(raw)
    grant = client.post('/resources/register', json={'request_id': 'source', 'path': str(source)}).json()
    state, _ = task(client, input_refs=[grant['resource_id']])
    receipt = app.state.resources.read(grant['resource_id'], task_id=state['task_id'])
    citation = receipt['citation']
    assert json.loads(receipt['text'])['lut_sha256'] == 'f' * 64
    assert citation['source_sha256'] == hashlib.sha256(raw).hexdigest() != 'f' * 64
    assert citation['hash_scope'] == 'complete_file'
    assert citation['unit'] == f'bytes:0:{len(raw)}'


def test_partial_read_never_claims_whole_file_hash_even_for_final_window(client, app, tmp_path, monkeypatch):
    raw = b'0123456789' * 2500
    source = tmp_path / 'long.txt'
    source.write_bytes(raw)
    grant = client.post('/resources/register', json={'request_id': 'large-source', 'path': str(source)}).json()
    state, _ = task(client, input_refs=[grant['resource_id']])
    monkeypatch.setattr('forestry_agent.resources.file_hash', lambda *args: pytest.fail('bounded read must not hash whole file'))
    for offset, end in [(0, 12000), (24000, 25000)]:
        receipt = app.state.resources.read(grant['resource_id'], offset=offset, task_id=state['task_id'])
        citation = receipt['citation']
        assert citation['source_sha256'] is None and citation['hash_scope'] == 'byte_window'
        assert citation['window_sha256'] == hashlib.sha256(raw[offset:end]).hexdigest()
        assert citation['unit'] == f'bytes:{offset}:{end}'


@pytest.mark.asyncio
async def test_native_file_read_to_report_copies_exact_source_citation(client, app, tmp_path):
    source = tmp_path / 'result.json'
    raw = b'{"lut_sha256":"' + b'f' * 64 + b'","LAI":0.965}'
    source.write_bytes(raw)
    grant = client.post('/resources/register', json={'request_id': 'native-source', 'path': str(source)}).json()
    state, _ = task(client, input_refs=[grant['resource_id']], constraints={
        'allowed_capabilities': ['fs.read', 'fs.write_workspace', 'action.inspect']})
    engine, calls = app.state.engine, []
    def model(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart('read_file', {'resource_id': grant['resource_id']}, 'read-source')])
        receipt = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)][-1].content
        assert receipt['kind'] == 'completed'
        if len(calls) == 2:
            assert receipt['citation']['source_sha256'] == hashlib.sha256(raw).hexdigest()
            return ModelResponse(parts=[ToolCallPart('write_file', {'relative_path':'report.md',
                'content':'# 核查\n输入字段lut_sha256为LUT身份，不是输入文件身份。',
                'citation_actions':{'input_source':receipt['action_id']}}, 'write-report')])
        return ModelResponse(parts=[TextPart(f"[报告](/artifacts/{receipt['artifact_id']}/content)")])
    agent, channel = engine.make_agent('small', 'native-citation', state['config_snapshot'], state['task_id'])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            await agent.run('按真实来源保存核查报告', deps=Deps(engine,state['task_id'],'native-citation',1))
        saved = engine.resources.artifact(engine.store.task(state['task_id'])['artifact_ids'][0], state['task_id'])
        body = Path(saved['locator']).read_text(encoding='utf-8')
        assert hashlib.sha256(raw).hexdigest() in body and 'complete_file' in body
        assert grant['resource_id'] in body and len(calls) == 3
    finally:
        await channel.aclose()
