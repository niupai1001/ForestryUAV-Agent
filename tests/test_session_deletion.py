"""Destructive-session behavior is exercised only inside pytest's owned workspace."""
import asyncio
import io
import sqlite3
from pathlib import Path

import pytest
from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.step_persistence import ContinuableSnapshot
from test_core import task

from forestry_agent.store import encode, now, uid
from forestry_agent.vision import visual_token_bound

pytestmark=pytest.mark.offline_web


def erase(client, state, **extra):
    return client.request('DELETE',f"/tasks/{state['task_id']}",json={
        'request_id':uid(),'expected_state_version':state['state_version'],'confirm_delete':True,**extra})


def register(client, path, request='source'):
    return client.post('/resources/register',json={'request_id':request,'path':str(path),
                       'classification':'private','read_only':True}).json()['resource_id']


def test_delete_erases_owned_files_rows_checkpoints_and_unshared_media(client,app,tmp_path,monkeypatch):
    original=tmp_path/'original.txt';original.write_text('original')
    external=register(client,original)
    uploaded=client.post('/uploads',params={'filename':'upload.txt','draft_id':'draft','request_id':'upload'},
                         content=b'private upload').json()
    state,_=task(client,input_refs=[external,uploaded['resource_id']])
    other,_=task(client,request_id='other',objective='other conversation')
    task_id=state['task_id'];engine=app.state.engine;root=engine.config.workspace_root
    monkeypatch.setattr(engine.jobs.docker,'inspect',lambda name:None)
    action_id,turn_id,run_id=uid(),uid(),uid()
    with engine.store.transaction() as db:
        event_id=engine.store.user_event(db,'user_message',{'task_id':task_id,'text':'private prompt'})
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',(turn_id,task_id,1,'private prompt','completed',now()))
        db.execute('INSERT INTO turn_requests VALUES(?,?,?)',('message-create',turn_id,event_id))
        db.execute('INSERT INTO agent_runs VALUES(?,?,?)',(run_id,turn_id,now()))
        db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)',(action_id,task_id,turn_id,'call',1,'fs.write_workspace','{}','confirmed','{}'))
        db.execute('INSERT INTO attempts VALUES(?,?,?,?,?)',(uid(),action_id,'confirmed',now(),now()))
        job_id=uid()
        job={'job_id':job_id,'task_id':task_id,'status':'succeeded','directory':str(root/'tasks'/task_id/'jobs'/job_id),
             'action_id':action_id,'container_id':None,'container_name':'forestry-job-'+job_id,'cancel_requested':False}
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(job_id,task_id,action_id,1,'succeeded',encode(job),now()))
    engine.resources.write(task_id,action_id,'result.txt','private result')
    for directory in ('model-inputs','tool-results','image-previews'):
        folder=root/'.forestry'/directory/task_id;folder.mkdir(parents=True,exist_ok=True)
        (folder/'private.txt').write_text('private intermediate')
    async def snapshots():
        for run,conversation,blobs in [(run_id,task_id,[b'A'*70000,b'B'*70000]),('other-run',other['task_id'],[b'A'*70000])]:
            await engine.steps.save_snapshot(ContinuableSnapshot(run_id=run,step_index=0,
                conversation_id=conversation,state='complete',messages=[ModelRequest(parts=[UserPromptPart([
                    BinaryContent(data=blob,media_type='image/png') for blob in blobs])])]))
    asyncio.run(snapshots())
    state=engine.store.task(task_id)
    assert erase(client,state).status_code==200
    assert erase(client,state).json()['deleted'] is True  # Minimal idempotent tombstone.
    assert client.get(f'/tasks/{task_id}').status_code==404
    assert client.get(f"/tasks/{other['task_id']}").status_code==200
    assert original.read_text()=='original'
    assert not (root/'tasks'/task_id).exists()
    assert not Path(uploaded['locator']).exists()
    for directory in ('model-inputs','tool-results','image-previews'):
        assert not (root/'.forestry'/directory/task_id).exists()
    with engine.store.transaction() as db:
        for table in ('tasks','turns','actions','jobs','artifacts','contexts','events','specs','assignments','approvals'):
            key='id' if table=='tasks' else 'task_id'
            assert not db.execute(f'SELECT 1 FROM {table} WHERE {key}=?',(task_id,)).fetchone()
        assert not db.execute('SELECT 1 FROM resources WHERE id IN (?,?)',(external,uploaded['resource_id'])).fetchone()
        assert not db.execute('SELECT 1 FROM requests WHERE instr(response,?)>0',(task_id,)).fetchone()
        assert not db.execute('SELECT 1 FROM user_events WHERE id=?',(event_id,)).fetchone()
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    with sqlite3.connect(root/'.forestry/model-steps.sqlite') as db:
        assert not db.execute('SELECT 1 FROM snapshots WHERE run_id=?',(run_id,)).fetchone()
        assert db.execute('SELECT COUNT(*) FROM media').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM snapshots WHERE run_id=?',('other-run',)).fetchone()[0]==1


def test_shared_source_registration_survives_until_last_session_but_original_is_never_deleted(client,app,tmp_path):
    original=tmp_path/'shared.txt';original.write_text('shared')
    ref=register(client,original)
    first,_=task(client,input_refs=[ref])
    second,_=task(client,request_id='second',input_refs=[ref])
    assert erase(client,first).status_code==200
    assert app.state.resources.resolve(ref,task_id=second['task_id'])==original
    assert erase(client,second).status_code==200
    assert original.read_text()=='shared'
    assert not any(r['resource_id']==ref for r in app.state.resources.all())


def test_interrupted_cleanup_freezes_writes_and_retry_finishes(client,app,monkeypatch):
    state,_=task(client)
    sessions=app.state.sessions;original=sessions._remove_path
    def occupied(path):raise PermissionError('owned fixture file in use')
    monkeypatch.setattr(sessions,'_remove_path',occupied)
    response=erase(client,state)
    assert response.status_code==409 and response.json()['error']['code']=='SESSION_CLEANUP_PENDING'
    assert client.get(f"/tasks/{state['task_id']}").status_code==409
    assert client.post(f"/tasks/{state['task_id']}/messages",json={'request_id':'must-not-run',
        'expected_spec_revision':1,'text':'do not execute'}).status_code==409
    monkeypatch.setattr(sessions,'_remove_path',original)
    assert erase(client,state).status_code==200
    assert client.get(f"/tasks/{state['task_id']}").status_code==404


def test_explicit_confirmation_version_and_active_job_protect_data(client,app):
    state,_=task(client)
    assert erase(client,state,confirm_delete=False).status_code==400
    assert erase(client,state,expected_state_version=999).status_code==409
    with app.state.store.transaction() as db:
        action_id=uid()
        db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)',(action_id,state['task_id'],'turn','call',1,'code.execute','{}','confirmed','{}'))
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(uid(),state['task_id'],action_id,1,'running','{}',now()))
    response=erase(client,state)
    assert response.status_code==409 and response.json()['error']['code']=='SESSION_BUSY'
    assert client.get(f"/tasks/{state['task_id']}").status_code==200


def test_redirected_cleanup_root_cannot_delete_external_directory(client,app,tmp_path):
    state,_=task(client);root=app.state.engine.config.workspace_root
    outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('keep')
    # Validate a forbidden resolved path directly; no real symlink privilege is needed.
    from forestry_agent.contracts import Failure
    with pytest.raises(Failure):app.state.sessions._remove_path(str(outside))
    assert (outside/'keep').read_text()=='keep'
    assert not (root/'tasks'/state['task_id']).exists()


def test_visual_budget_uses_actual_padded_size_and_preserves_paid_anchor():
    out=io.BytesIO();Image.new('RGB',(600,600)).save(out,format='PNG')
    image=BinaryContent(data=out.getvalue(),media_type='image/png')
    request=ModelRequest(parts=[UserPromptPart(['describe',image])])
    assert visual_token_bound([request])==489
    paid=ModelResponse(parts=[],usage=RequestUsage(input_tokens=100,output_tokens=10))
    assert visual_token_bound([request,paid])==0
    assert visual_token_bound([request,paid,request])==489
    assert visual_token_bound([ModelRequest(parts=[UserPromptPart([BinaryContent(data=b'invalid',media_type='image/png')])])])==4096


def test_pending_upload_is_owned_and_deleted_without_becoming_a_model_input(client,app):
    state,_=task(client)
    uploaded=client.post('/uploads',params={'filename':'pending.txt','draft_id':'pending-draft',
        'request_id':'pending-upload','task_id':state['task_id']},content=b'pending owned upload')
    assert uploaded.status_code==201
    data=uploaded.json()
    assert data['owner_task_id']==state['task_id']
    assert app.state.store.task(state['task_id'])['input_refs']==[]
    assert erase(client,state).status_code==200
    assert not Path(data['locator']).exists()


def test_batch_visual_input_is_a_bounded_index_not_two_silently_selected_images(app,monkeypatch):
    vision=app.state.engine.vision
    def must_not_decode(*args,**kwargs):raise AssertionError('Batch must not eagerly decode images')
    monkeypatch.setattr(vision,'prepare',must_not_decode)
    attachments=[{'resource_id':uid(),'name':f'img-{i}.png','kind':'file','preview_url':'unused'} for i in range(100)]
    prompt=vision.message_input('task','large duplicated list',attachments,enabled=True,original_prompt='compare all images')
    assert isinstance(prompt,str) and prompt.startswith('compare all images')
    assert '100张图片' in prompt and 'list_inputs' in prompt and 'view_image' in prompt
    assert attachments[7]['resource_id'] in prompt and attachments[8]['resource_id'] not in prompt
    assert len(prompt.encode())<2200


@pytest.mark.asyncio
async def test_100_image_refs_page_and_native_read_fit_window_without_eager_pixels(client,app,tmp_path):
    import json

    from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
    from test_roles import FixtureFunctionModel

    from forestry_agent.agent import Deps
    image=tmp_path/'batch-image.png';Image.new('RGB',(32,32),'green').save(image)
    refs=[register(client,image,request=f'batch-{i}') for i in range(100)]
    app.state.engine.config.science_image='sha256:'+'a'*64
    state,_=task(client,objective='分析这批图片',input_refs=refs,harness_mode='H2')
    engine=app.state.engine;turn_id=uid()
    with engine.store.transaction() as db:
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',(turn_id,state['task_id'],1,state['objective'],'running',now()))
    prompt=engine.vision.message_input(state['task_id'],'do not inject everything',state['initial_attachments'],
        enabled=True,original_prompt=state['objective'])
    calls=0
    def model(messages,info):
        nonlocal calls
        calls+=1
        returns=[p for m in messages if isinstance(m,ModelRequest) for p in m.parts if isinstance(p,ToolReturnPart)]
        if calls==1:
            assert not any(isinstance(item,BinaryContent) for m in messages if isinstance(m,ModelRequest)
                for p in m.parts if isinstance(p,UserPromptPart) and isinstance(p.content,list) for item in p.content)
            return ModelResponse(parts=[ToolCallPart('list_inputs',{'offset':80,'limit':20},'page')])
        if calls==2:
            result=returns[-1].content
            assert result['total']==100 and len(result['resources'])==20 and result['has_more'] is False
            return ModelResponse(parts=[ToolCallPart('view_image',{'resource_id':refs[-1]},'view')])
        assert any(isinstance(item,BinaryContent) for m in messages if isinstance(m,ModelRequest)
            for p in m.parts if isinstance(p,UserPromptPart) and isinstance(p.content,list) for item in p.content)
        return ModelResponse(parts=[TextPart('已按需读回末页的一张图片，其余尚未查看。')])
    agent,connection=engine.make_agent(state['model_profile'],turn_id,state['config_snapshot'],state['task_id'])
    try:
        with agent.override(model=FixtureFunctionModel(model)):
            result=await agent.run(prompt,deps=Deps(engine,state['task_id'],turn_id,1))
        assert '其余尚未查看' in result.output.response and calls==3
        with engine.store.transaction() as db:
            contexts=[json.loads(row[0]) for row in db.execute('SELECT payload FROM contexts WHERE task_id=?',(state['task_id'],))]
        assert len(contexts)==3
        assert all(context['estimated_tokens_upper_bound']<=28672 for context in contexts)
    finally:await connection.aclose()
