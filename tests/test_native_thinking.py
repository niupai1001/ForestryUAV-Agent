"""Only native reasoning transport and removal of synthetic progress are new scope."""

import pytest
from pydantic_ai.models.function import DeltaThinkingPart
from test_core import task
from test_roles import FixtureFunctionModel

from forestry_agent.agent import Answer
from forestry_agent.models import thinking_effort

pytestmark=pytest.mark.offline_web


def test_native_thinking_uses_observed_controls_and_no_summary_schema():
    assert thinking_effort({'thinking':True,'thinking_controls':{'values':[False,'low','medium','xhigh'],'default':'medium'}})=='medium'
    assert thinking_effort({'thinking':True,'thinking_controls':{'values':[False,True],'default':True}})=='medium'
    assert thinking_effort({'thinking':True,'thinking_controls':{'values':[False,'low'],'default':False}})=='low'
    assert thinking_effort({'thinking':False})=='none'
    assert 'process_summary' not in Answer.model_json_schema()['properties']


@pytest.mark.asyncio
async def test_native_reasoning_is_streamed_separately_persisted_and_not_resummarized(client,app,monkeypatch):
    state,_=task(client,objective='普通对话的思考通道夹具')
    engine=app.state.engine
    async def stream(messages,info):
        assert 'report_progress' not in {tool.name for tool in info.function_tools}
        for tool in info.output_tools:
            assert 'process_summary' not in str(tool.parameters_json_schema)
        yield {0:DeltaThinkingPart(content='原生第一段。')}
        yield {0:DeltaThinkingPart(content='原生第二段。')}
        yield '这是回答正文。'
    fixture=FixtureFunctionModel(stream_function=stream)
    original=engine.make_agent
    def make(*args,**kwargs):
        agent,connection=original(*args,**kwargs)
        assert agent.model_settings['openai_reasoning_effort']=='medium'
        assert agent.model.profile['openai_chat_send_back_thinking_parts'] is False
        agent.model=fixture
        return agent,connection
    monkeypatch.setattr(engine,'make_agent',make)
    engine.submit(state['task_id'],state['objective'])
    await engine.queue[state['task_id']]
    value=engine.store.task(state['task_id'])
    message=value['messages'][-1]
    assert value['status']=='waiting_user'
    assert message['thinking']=='原生第一段。原生第二段。'
    assert message['text']=='这是回答正文。' and 'process_summary' not in message
    assert value['last_usage']['requests']==1 and value['action_count']==0
    events=engine.store.events(state['task_id'])
    thoughts=[event['payload'] for event in events if event['type']=='assistant.thinking.delta']
    assert ''.join(event['delta'] for event in thoughts)==message['thinking']
    drafts=[event['payload']['text'] for event in events if event['type']=='assistant.draft']
    assert drafts and all('原生' not in text for text in drafts)
    assert not any(event['type']=='action.started' for event in events)
