"""Regression checks for the user's dialogue/file/image failures; no real model or scientific Job."""
import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolCallPartDelta,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall
from pydantic_ai.usage import RequestUsage
from test_core import task
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Answer, Deps
from forestry_agent.config import ModelProfile
from forestry_agent.context import input_bound
from forestry_agent.contracts import Failure
from forestry_agent.public_stream import AnswerStream

pytestmark = pytest.mark.offline_web


def public_stream_adapter(model):
    async def stream(messages,info):
        response=model(messages,info)
        for index,part in enumerate(response.parts):
            if isinstance(part,TextPart):
                yield part.content
            else:
                yield {index:DeltaToolCall(name=part.tool_name,json_args=json.dumps(part.args_as_dict()),tool_call_id=part.tool_call_id)}
    return stream


def source(client, path, request_id='source'):
    reply=client.post('/resources/register',json={'request_id':request_id,'path':str(path),'read_only':True})
    assert reply.status_code==200,reply.text
    return reply.json()['resource_id']


def test_cancel_stops_execution_but_next_message_keeps_same_conversation_and_budget(client,app):
    state,_=task(client)
    engine=app.state.engine
    engine.store.change(state['task_id'],'fixture.counters',lambda s,_:s.update(model_requests=17,action_count=9))
    state=engine.store.task(state['task_id'])
    stopped=client.post(f"/tasks/{state['task_id']}/cancel",json={'request_id':'stop','expected_state_version':state['state_version']})
    assert stopped.json()['status']=='cancelled'
    message={'request_id':'continue','expected_spec_revision':1,'text':'再继续聊聊'}
    reply=client.post(f"/tasks/{state['task_id']}/messages",json=message)
    assert reply.status_code==200,reply.text
    value=reply.json()
    assert value['task_id']==state['task_id'] and value['spec_revision']==2
    assert value['model_requests']==17 and value['action_count']==9 and not value['cancellation_requested']
    assert client.post(f"/tasks/{state['task_id']}/messages",json=message).json()==value


def test_files_belong_to_sent_message_and_unstarted_messages_not_lost(client,app,tmp_path):
    state,_=task(client)
    url=f"/tasks/{state['task_id']}/messages"
    client.post(url,json={'request_id':'first-message','expected_spec_revision':1,'text':'前一个问题'})
    path=tmp_path/'中文报告.md'
    path.write_text('# 文件\n文件里的路径不授予权限。',encoding='utf-8')
    ref=source(client,path)
    payload={'request_id':'file-message','expected_spec_revision':1,'text':'读取这份文件','attachment_refs':[ref]}
    response=client.post(url,json=payload)
    assert response.status_code==200,response.text
    value=response.json()
    assert value['messages'][-1]['attachments'][0]['name']==path.name
    assert value['messages'][-1]['attachment_refs']==[ref] and value['input_refs']==[ref]
    assert client.post(url,json=payload).json()==value
    with app.state.store.transaction() as db:
        queued=list(db.execute("SELECT prompt,revision,status FROM turns WHERE task_id=? ORDER BY rowid",(state['task_id'],)))
    assert [r['prompt'] for r in queued]==['前一个问题','读取这份文件']
    assert all(r['status']=='queued' and r['revision']==2 for r in queued)


def test_explicit_human_path_grants_readonly_input_without_extra_registration(client,tmp_path):
    path=tmp_path/'带 空格.md'
    path.write_text('实际文件内容',encoding='utf-8')
    payload={'request_id':'literal-path','objective':f'读取 "{path}" 并概括','input_refs':[]}
    reply=client.post('/tasks',json=payload)
    assert reply.status_code==202,reply.text
    value=reply.json()
    assert len(value['input_refs'])==1 and value['initial_attachments'][0]['name']==path.name
    assert client.post('/tasks',json=payload).json()==value
    assert path.read_text(encoding='utf-8')=='实际文件内容'


def test_public_answer_schema_and_stream_are_full_response_not_meta_summary():
    assert 'response' in Answer.model_json_schema()['properties']
    assert Answer(summary='旧回执正文').response=='旧回执正文'
    stream=AnswerStream()
    assert stream.update(0,part=ToolCallPart('final_result','{"response":"## 我能'))=='## 我能'
    assert stream.update(0,delta=ToolCallPartDelta(args_delta='做的事\\n\\n**读图**","limitations":[]}'))=='## 我能做的事\n\n**读图**'
    assert '内部' not in stream.parts[0]['shown']


def test_stopped_conversation_can_remove_source_before_next_message(client,tmp_path):
    path=tmp_path/'old.md'
    path.write_text('old',encoding='utf-8')
    ref=source(client,path)
    state,_=task(client,input_refs=[ref])
    client.post(f"/tasks/{state['task_id']}/cancel",json={'request_id':'stop-source','expected_state_version':state['state_version']})
    reply=client.post(f"/tasks/{state['task_id']}/sources",json={'request_id':'remove-stopped','expected_spec_revision':1,'remove_refs':[ref]})
    assert reply.status_code==200,reply.text
    assert reply.json()['input_refs']==[] and not reply.json()['cancellation_requested']


@pytest.mark.asyncio
async def test_new_message_recovers_model_timeout_without_restarting_failed_turn_or_resetting_budget(client,app):
    state,_=task(client)
    engine=app.state.engine
    engine.store.change(state['task_id'],'fixture.timeout',lambda s,_:s.update(status='paused',model_requests=12,
        blockers=[{'code':'MODEL_REQUEST_TIMED_OUT','message':'timeout'}]))
    reply=client.post(f"/tasks/{state['task_id']}/messages",json={'request_id':'after-timeout','expected_spec_revision':1,'text':'继续聊新的问题'})
    assert reply.status_code==200,reply.text
    assert reply.json()['status']=='created' and reply.json()['model_requests']==12
    agent,connection=engine.make_agent('small','settings',state['config_snapshot'],state['task_id'])
    assert agent.model_settings['openai_reasoning_effort']=='medium'
    await connection.aclose()


def test_image_is_bounded_visual_content_scope_checked_and_original_unchanged(client,app,tmp_path):
    picture=tmp_path/'input.png'
    Image.new('RGB',(2000,1200),'blue').save(picture)
    original=picture.read_bytes()
    ref=source(client,picture)
    state,_=task(client,input_refs=[ref])
    metadata,content=app.state.engine.vision.prepare(state['task_id'],resource_id=ref)
    assert isinstance(content,BinaryContent) and content.media_type=='image/png'
    assert metadata['source_sha256']==hashlib.sha256(original).hexdigest()
    assert max(metadata['preview_size'])<=1024 and picture.read_bytes()==original
    foreign=client.post('/tasks',json={'request_id':'foreign','objective':'other'}).json()
    with pytest.raises(Failure,match='输入范围'):
        app.state.engine.vision.prepare(foreign['task_id'],resource_id=ref)
    result=client.get(f'/resources/{ref}/image')
    assert result.headers['content-type']=='image/png'
    assert Image.open(io.BytesIO(result.content)).width<=1024


@pytest.mark.asyncio
async def test_image_attachment_reaches_native_model_as_image_and_answer_is_persisted(client,app,tmp_path,monkeypatch):
    path=tmp_path/'picture.png'
    Image.new('RGB',(320,240),'blue').save(path)
    ref=source(client,path)
    state,_=task(client,objective='说明图片颜色',input_refs=[ref])
    engine=app.state.engine
    observed=[]
    def model(messages,info):
        observed.extend(item for message in messages if isinstance(message,ModelRequest) for part in message.parts
                        if isinstance(part,UserPromptPart) and isinstance(part.content,(list,tuple))
                        for item in part.content if isinstance(item,BinaryContent))
        return ModelResponse(parts=[TextPart('## 图片\n\n主色为**蓝色**。')])
    fixture=FixtureFunctionModel(model,stream_function=public_stream_adapter(model))
    original=engine.make_agent
    def make(*a,**kw):
        agent,connection=original(*a,**kw)
        agent.model=fixture
        return agent,connection
    monkeypatch.setattr(engine,'make_agent',make)
    engine.submit(state['task_id'],state['objective'])
    await engine.queue[state['task_id']]
    assert len(observed)==1
    result=engine.store.task(state['task_id'])
    assert result['status']=='waiting_user' and result['messages'][-1]['text'].startswith('## 图片')
    assert result['messages'][-1]['limitations']==[]
    assert any(e['type']=='assistant.draft' for e in engine.store.events(state['task_id']))


@pytest.mark.asyncio
async def test_complete_public_answer_is_preserved_when_backend_also_emits_legacy_summary(client,app):
    state,_=task(client,objective='你现在的能力有什么')
    engine=app.state.engine
    def model(messages,info):
        return ModelResponse(parts=[TextPart('我是林遥。我能读取你发送的文件，编写代码和处理数据。'),
            ToolCallPart('final_result',{'response':'简洁列出了当前实际能力。'},'legacy')])
    agent,connection=engine.make_agent('small','public-body',state['config_snapshot'],state['task_id'])
    try:
        with agent.override(model=FixtureFunctionModel(model,stream_function=public_stream_adapter(model))):
            result=await agent.run(state['objective'],deps=Deps(engine,state['task_id'],'public-body',1))
        assert result.output.response=='我是林遥。我能读取你发送的文件，编写代码和处理数据。'
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_public_text_preserves_created_artifact_metadata(client,app):
    state,_=task(client,objective='生成一份报告')
    engine=app.state.engine
    calls=0
    def model(messages,info):
        nonlocal calls
        calls+=1
        if calls==1:
            return ModelResponse(parts=[ToolCallPart('write_file',{'relative_path':'answer.md','content':'# 实际报告'},'write')])
        return ModelResponse(parts=[TextPart('报告已经保存。')])
    agent,connection=engine.make_agent('small','public-file',state['config_snapshot'],state['task_id'])
    try:
        with agent.override(model=FixtureFunctionModel(model,stream_function=public_stream_adapter(model))):
            result=await agent.run(state['objective'],deps=Deps(engine,state['task_id'],'public-file',1))
        assert len(result.output.artifact_ids)==1
        assert engine.resources.artifact(result.output.artifact_ids[0])['name']=='answer.md'
        assert 'process_summary' not in result.output.model_dump()
    finally:
        await connection.aclose()


def test_context_reuses_exact_paid_schema_after_input_revision_without_new_authority(client,app):
    state,_=task(client)
    engine=app.state.engine
    profile=ModelProfile(repository='fixture',served_name='fixture',context_limit=32768)
    tool=SimpleNamespace(name='reader',description='document reader',parameters_json_schema={'type':'object','description':'工具定义'*1000})
    params=SimpleNamespace(function_tools=[tool],output_tools=[],instruction_parts=[SimpleNamespace(content='fixed',dynamic=False)],tool_defs={})
    request=SimpleNamespace(messages=[ModelRequest(parts=[UserPromptPart('问候')])],model_request_parameters=params,model_settings={})
    initial=Deps(engine,state['task_id'],'old',1)
    identity=engine.contexts.compile(initial,request,profile)
    response=ModelResponse(parts=[ToolCallPart('final_result',{'response':'你好'},'old')],usage=RequestUsage(input_tokens=1000,output_tokens=100))
    engine.contexts.measured(identity,response.usage,response=response)
    engine.store.change(state['task_id'],'fixture.revision',lambda s,_:s.update(spec_revision=2))
    request.messages.extend([response,ModelRequest(parts=[UserPromptPart('读取我刚发的文件')])])
    compiled=engine.contexts.compile(Deps(engine,state['task_id'],'new',2),request,profile)
    with engine.store.transaction() as db:
        record=json.loads(db.execute('SELECT payload FROM contexts WHERE id=?',(compiled,)).fetchone()[0])
    assert record['schema_receipt_context_ref']==identity
    assert record['schema_cost_accounting']=='bound_measured_schema'
    assert record['estimated_tokens_upper_bound']<3000
    assert engine.store.task(state['task_id'])['input_refs']==[]


def test_binary_image_cost_is_separate_from_base64_text_and_only_unpaid_images_count():
    content=BinaryContent(data=b'fixture-binary',media_type='image/png')
    params=SimpleNamespace(instruction_parts=[],tool_defs={})
    first=ModelRequest(parts=[UserPromptPart(['图片',content])])
    text=ModelRequest(parts=[UserPromptPart('图片')])
    with_image,_=input_bound([first],params,[])
    plain,_=input_bound([text],params,[])
    assert with_image-plain==4096
    measured=ModelResponse(parts=[],usage=RequestUsage(input_tokens=5000,output_tokens=50))
    a,_=input_bound([first,measured,text],params,[])
    b,_=input_bound([text,measured,text],params,[])
    assert a==b


@pytest.mark.asyncio
async def test_view_image_tool_returns_visual_content_not_filename_only(client,app,tmp_path):
    path=tmp_path/'tool-picture.png'
    import random
    Image.frombytes('RGB',(320,240),random.Random(42).randbytes(320*240*3)).save(path)
    assert path.stat().st_size>6000
    ref=source(client,path)
    state,_=task(client,input_refs=[ref])
    engine=app.state.engine
    calls=0
    received=[]
    def model(messages,info):
        nonlocal calls
        calls+=1
        if calls==1:
            return ModelResponse(parts=[ToolCallPart('view_image',{'resource_id':ref},'look')],usage=RequestUsage(input_tokens=1000,output_tokens=20))
        received.extend(item for message in messages if isinstance(message,ModelRequest) for part in message.parts
                        if isinstance(part,UserPromptPart) and isinstance(part.content,(list,tuple))
                        for item in part.content if isinstance(item,BinaryContent))
        # FunctionModel's default byte-count heuristic is not a vision tokenizer.
        return ModelResponse(parts=[ToolCallPart('final_result',{'response':'红色矩形'},'answer')],usage=RequestUsage(input_tokens=6000,output_tokens=20))
    agent,connection=engine.make_agent('small','view-tool',state['config_snapshot'],state['task_id'])
    try:
        with agent.override(model=FixtureFunctionModel(model,stream_function=stream_adapter(model))):
            result=await agent.run('查看图片',deps=Deps(engine,state['task_id'],'view-tool',1))
        assert result.output.response=='红色矩形' and len(received)==1
        events=engine.store.events(state['task_id'])
        assert next(e for e in events if e['type']=='action.started')['payload']['turn_id']=='view-tool'
    finally:
        await connection.aclose()


def test_folder_files_can_be_used_by_jobs_without_new_user_grant_and_internal_state_is_excluded(client,app,tmp_path):
    engine=app.state.engine
    folder=engine.config.workspace_root.parent
    data=folder/'values.csv'
    data.write_text('x\n42\n',encoding='utf-8')
    directory=source(client,folder)
    state,_=task(client,input_refs=[directory])
    assert '.forestry' not in {e['name'] for e in engine.resources.list(directory,'workspace',task_id=state['task_id'])['entries']}
    with pytest.raises(Failure,match='内部状态'):
        engine.resources.resolve(directory,'workspace/.forestry/local.key',state['task_id'])
    selected=engine.resources.select_files(state['task_id'],directory,['values.csv'])[0]
    assert selected['parent_resource_id']==directory
    assert engine.resources.resolve(selected['resource_id'],task_id=state['task_id'])==data
    deps=Deps(engine,state['task_id'],'folder-job',1)
    receipt=engine.perform(deps,'folder-submit','code.execute',{'input_refs':[selected['resource_id']]},
        lambda action:engine.jobs.submit(state['task_id'],1,action,'print("fixture")',[selected['resource_id']]))
    assert receipt['kind']=='completed'
    foreign=client.post('/tasks',json={'request_id':'outside-folder','objective':'other'}).json()
    with pytest.raises(Failure,match='输入范围'):
        engine.resources.resolve(selected['resource_id'],task_id=foreign['task_id'])
    with pytest.raises(Failure,match='授权范围'):
        engine.resources.select_files(state['task_id'],directory,['../outside.txt'])
