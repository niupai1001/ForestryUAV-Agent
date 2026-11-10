import pytest
from pathlib import Path
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from test_core import task
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['unknown', 'foreign', 'valid'])
async def test_markdown_artifact_links_obey_saved_task_ownership(client, app, kind):
    state, _ = task(client)
    engine = app.state.engine
    foreign, _ = task(client, request_id='other-report')
    saved = engine.resources.write(state['task_id'] if kind == 'valid' else foreign['task_id'],
                                   'fixture', 'report.md', '# real report')
    ref = '0797bdf7-e31a-4e36-b50c-e053c421a06d' if kind == 'unknown' else saved['artifact_id']
    calls = []
    def model(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart(f'[报告](/artifacts/{ref}/content)' if len(calls) == 1
                                              else '尚未保存本任务报告，没有可用下载链接。')])
    agent, channel = engine.make_agent('small', 'report-link', state['config_snapshot'], state['task_id'])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            result = await agent.run('交付报告', deps=Deps(engine, state['task_id'], 'report-link', 1))
        assert len(calls) == (1 if kind == 'valid' else 2)
        assert ('/artifacts/' in result.output.response) == (kind == 'valid')
    finally:
        await channel.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['unknown', 'foreign', 'tampered', 'valid'])
async def test_saved_markdown_checks_internal_links_before_creating_output(client, app, kind):
    state, _ = task(client, constraints={'allowed_capabilities': ['fs.write_workspace']})
    engine = app.state.engine
    other, _ = task(client, request_id='other-source')
    saved = engine.resources.write(other['task_id'] if kind == 'foreign' else state['task_id'],
                                   'fixture', 'source.md', '# source')
    if kind == 'tampered':
        Path(saved['locator']).write_text('changed', encoding='utf-8')
    ref = 'verification_report_001' if kind == 'unknown' else saved['artifact_id']
    text = f'# report\n下载链接：/artifacts/{ref}/content'
    calls = []
    def model(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart('write_file',
                {'relative_path': 'blocked.md', 'content': text}, 'body-reference')])
        returned = [p for msg in messages if isinstance(msg, ModelRequest)
                    for p in msg.parts if isinstance(p, ToolReturnPart)][-1].content
        assert returned['kind'] == ('completed' if kind == 'valid' else 'failed')
        if kind != 'valid':
            assert 'VERIFICATION_FAILED' in str(returned)
            assert not (engine.resources.task_root(state['task_id']) / 'blocked.md').exists()
            return ModelResponse(parts=[TextPart('未保存包含无效链接的报告。')])
        return ModelResponse(parts=[TextPart(f"[报告](/artifacts/{returned['artifact_id']}/content)")])
    agent, channel = engine.make_agent('small', 'body-reference', state['config_snapshot'], state['task_id'])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            await agent.run('保存报告', deps=Deps(engine, state['task_id'], 'body-reference', 1))
        with engine.store.snapshot() as db:
            produced = [row[0] for row in db.execute('SELECT payload FROM artifacts WHERE task_id=?', (state['task_id'],))]
        assert sum('blocked.md' in row for row in produced) == (kind == 'valid')
        assert len(calls) == 2
    finally:
        await channel.aclose()


@pytest.mark.asyncio
async def test_absolute_report_path_returns_actionable_relative_path_without_input_write_grant(client, app):
    state, _ = task(client, constraints={'allowed_capabilities': ['fs.write_workspace']})
    engine = app.state.engine
    calls = []
    def model(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart('write_file', {'relative_path': '/output/report.md', 'content': '# report'}, 'bad-path')])
        returns = [part for msg in messages if isinstance(msg, ModelRequest) for part in msg.parts if isinstance(part, ToolReturnPart)]
        if len(calls) == 2:
            assert 'reports/report.md' in str(returns[-1].content)
            return ModelResponse(parts=[ToolCallPart('write_file', {'relative_path': 'reports/report.md', 'content': '# report'}, 'good-path')])
        receipt = returns[-1].content
        assert receipt['kind'] == 'completed'
        return ModelResponse(parts=[TextPart(f"[报告](/artifacts/{receipt['artifact_id']}/content)")])
    agent, channel = engine.make_agent('small', 'report-save', state['config_snapshot'], state['task_id'])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            result = await agent.run('保存报告', deps=Deps(engine, state['task_id'], 'report-save', 1))
        assert '/artifacts/' in result.output.response and len(calls) == 3
        final = engine.store.task(state['task_id'])
        assert len(final['artifact_ids']) == 1 and final['action_count'] == 2
        assert engine.resources.artifact(final['artifact_ids'][0], state['task_id'])['name'] == 'report.md'
    finally:
        await channel.aclose()
