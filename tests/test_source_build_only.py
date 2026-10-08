"""Build-result authority fixtures; no archive/backend executes on the host."""
import hashlib
import json
from pathlib import Path

import pytest
from test_network import resolve as approve
from test_resolution import IndexFixture, prepared_target
from test_source_environments import source_job

from forestry_agent.contracts import Failure
from forestry_agent.environments import preparation_code
from forestry_agent.progress import observation


def built_fixture(client, app, tmp_path, condition=None):
    _, ref, state, deps, jobs, environments, job_id = source_job(client, app, tmp_path, build_only=True)
    jobs.tick()
    job = jobs.read(job_id)
    output = Path(job['directory'])/'output'
    filename = 'widget-1.0-py3-none-any.whl'
    data = b'protocol-wheel-bytes-not-imported-or-installed'
    metadata = ('Metadata-Version: 2.3\nName: '+('wrong' if condition=='metadata_identity' else 'widget')+
        '\nVersion: 1.0\nRequires-Python: >=3.8\nRequires-Dist: '+
        ('dep @ https://example.com/private.whl' if condition=='direct_dependency' else 'dep>=2')+'\n\n').encode()
    source = job['source_recipe'][0]
    record = {'filename':filename,'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data),
        'source_resource_id':ref,'source_sha256':source['sha256'],
        'metadata_filename':filename+'.metadata','metadata_sha256':hashlib.sha256(metadata).hexdigest(),
        'metadata_size_bytes':len(metadata)}
    if condition=='source_hash':record['source_sha256']='0'*64
    if condition=='wheel_hash':record['sha256']='0'*64
    if condition=='metadata_hash':record['metadata_sha256']='0'*64
    manifest={'schema_version':1,'kind':'source_wheel_build','image_reference':job['image_reference'],
        'python':'3.12.12','network':'none','runtime_installation':False,'sources':job['source_recipe'],
        'wheels':job['environment_recipe'],'built_wheels':[record],'build_frontend':{'isolation':'native_pep517'}}
    if condition=='pretend_installed':manifest['runtime_installation']=True
    (output/filename).write_bytes(data)
    if condition!='missing_metadata':(output/(filename+'.metadata')).write_bytes(metadata)
    (output/'source-build-lock.json').write_text(json.dumps(manifest),encoding='utf-8')
    if condition=='fake_environment':
        (output/'environment.zip').write_bytes(b'backend-pretends-environment')
        (output/'environment-lock.json').write_text(json.dumps({'schema_version':1,'kind':'python_environment',
            'image_reference':job['image_reference'],'installation':'pip_offline_hash_checked','sources':job['source_recipe'],
            'wheels':job['environment_recipe'],'built_wheels':[record],'build_frontend':{'isolation':'native_pep517'}}),encoding='utf-8')
    jobs.docker.container['State'].update(Running=False,Status='exited',ExitCode=0)
    jobs.tick()
    assert jobs.read(job_id)['status']=='succeeded'
    if condition=='wrong_code':
        modified=jobs.read(job_id);modified['code']='print("pretend source build")';jobs.save(modified,'test.fixture')
    return state,deps,jobs,environments,job_id


def test_build_only_returns_actual_wheels_metadata_and_can_install_without_rebuilding(client, app, tmp_path):
    state,deps,jobs,environments,job_id=built_fixture(client,app,tmp_path)
    result=jobs.observation(job_id,state['task_id'])
    assert 'prepared_environment' not in result
    built=result['prepared_wheels']
    assert built['runtime_installation'] is False and len(built['wheel_artifact_ids'])==1
    ref=built['wheel_artifact_ids'][0]
    assert environments.acquired_wheel(state['task_id'],1,ref)['name']=='widget-1.0-py3-none-any.whl'
    reply=app.state.engine.perform(deps,'install-built','environment.prepare',{'wheel_artifact_ids':[ref]},lambda action:
        environments.prepare(state['task_id'],1,action,[],wheel_artifact_ids=[ref]))
    job=jobs.read(reply['job']['job_id'])
    assert not job.get('source_recipe') and job['mounts'][0]['artifact_id']==ref
    assert job['environment_recipe'][0]['sha256']==built['built_wheels'][0]['sha256']
    assert 'build_sources(' not in job['code'] and '--require-hashes' in job['code']
    assert '--only-binary' in job['code']  # Final installation still resolves the full runtime closure.
    with pytest.raises(Failure,match='当前修订'):
        environments.acquired_wheel(state['task_id'],2,ref)


@pytest.mark.parametrize('condition',['source_hash','wheel_hash','metadata_hash','metadata_identity',
    'direct_dependency','missing_metadata','pretend_installed','wrong_code'])
def test_bad_build_products_do_not_become_reusable_wheels(client,app,tmp_path,condition):
    state,_,jobs,environments,job_id=built_fixture(client,app,tmp_path,condition)
    ref=next(r for r in jobs.read(job_id)['artifact_ids'] if jobs.resources.artifact(r)['name'].endswith('.whl'))
    with pytest.raises(Failure):
        jobs.observation(job_id,state['task_id'])
    with pytest.raises(Failure):
        environments.acquired_wheel(state['task_id'],1,ref)


def test_built_wheel_bytes_and_metadata_are_rechecked_before_queued_install(client,app,tmp_path):
    state,deps,jobs,environments,job_id=built_fixture(client,app,tmp_path)
    built=jobs.observation(job_id,state['task_id'])['prepared_wheels']
    ref=built['wheel_artifact_ids'][0]
    reply=app.state.engine.perform(deps,'queued-install','environment.prepare',{'wheel_artifact_ids':[ref]},lambda action:
        environments.prepare(state['task_id'],1,action,[],wheel_artifact_ids=[ref]))
    artifact=jobs.resources.artifact(built['built_wheels'][0]['metadata_artifact_id'])
    Path(artifact['locator']).write_text('tampered metadata',encoding='utf-8')
    with pytest.raises(Failure,match='校验和'):
        jobs.may_start(jobs.read(reply['job']['job_id']))


def test_build_only_without_source_is_rejected_and_worker_is_syntax_only(client,app):
    with pytest.raises(Failure,match='授权源码'):
        app.state.engine.environments.prepare('unused-task',1,'unused-action',[],build_only=True)
    with app.state.store.transaction() as db:
        assert not db.execute('SELECT 1 FROM jobs').fetchone()
    code=preparation_code([],'image', [{'resource_id':'source','filename':'s.tar.gz'}],build_only=True)
    compile(code,'trusted-build-launcher','exec')
    assert 'export_build_result(' in code and 'raise SystemExit(0)' in code


def test_backend_cannot_claim_runtime_installation_by_writing_an_environment_archive(client,app,tmp_path):
    state,_,jobs,environments,job_id=built_fixture(client,app,tmp_path,'fake_environment')
    observed=jobs.observation(job_id,state['task_id'])
    assert observed['prepared_wheels']['runtime_installation'] is False and 'prepared_environment' not in observed
    ref=next(r for r in jobs.read(job_id)['artifact_ids'] if jobs.resources.artifact(r)['name']=='environment.zip')
    with pytest.raises(Failure,match='仅构建'):
        environments.resolve(state['task_id'],1,ref)


def test_actual_built_metadata_drives_runtime_closure_and_only_remote_wheels_are_downloaded(client,app,tmp_path):
    state,_,jobs,_environments,job_id=built_fixture(client,app,tmp_path)
    built=jobs.observation(job_id,state['task_id'])['prepared_wheels']
    refs=built['wheel_artifact_ids']
    engine=app.state.engine
    fake=IndexFixture();fake.add('dep','2.0')
    engine.acquisition.http=fake
    deps,target=prepared_target(engine,state)
    target=target['artifact_id']
    def run(call,approval=None):
        return engine.perform(deps,call,'environment.acquire',{'requirements':['widget==1.0'],
            'target_artifact_id':target,'built_wheel_artifact_ids':refs,'approval_id':approval},lambda action:
            engine.resolution.resolve(deps,action,['widget==1.0'],target,approval,built_wheel_artifact_ids=refs))
    proposal=run('source-runtime-proposal')
    assert proposal['kind']=='approval_required' and not fake.calls
    grant=engine.network.list(state['task_id'])[-1]
    assert json.loads(grant['request']['value'])['built_wheels'][0]['artifact_id']==refs[0]
    approve(client,state,grant).raise_for_status()
    result=run('source-runtime-resolve',grant['approval_id'])
    assert result['kind']=='completed' and result['built_wheel_artifact_ids']==refs
    assert result['wheels']==[{'name':'dep','version':'2.0','filename':'dep-2.0-py3-none-any.whl'}]
    assert not any('/widget/' in url for url in fake.calls)
    lock,_=engine.resolution.lock(deps,result['resolution_lock_artifact_id'])
    assert {p['name'] for p in lock['selected']}=={'widget','dep'}
    local=next(p for p in lock['selected'] if p['name']=='widget')
    assert local['sha256']==built['built_wheels'][0]['sha256']
    summary=engine.acquisition.confirmed_receipts(state['task_id'],1)[0]
    assert summary['built_wheels']==result['built_wheels'] and summary['source_action_id']==result['action_id']
    download=engine.perform(deps,'remote-download-proposal','environment.acquire',{'wheels':result['wheels']},lambda action:
        engine.acquisition.acquire(deps,action,result['wheels'],resolution_lock_artifact_id=result['resolution_lock_artifact_id']))
    assert download['kind']=='approval_required'
    req=json.loads(engine.network.list(state['task_id'])[-1]['request']['value'])
    assert req['wheels']==result['wheels']  # The source-derived wheel cannot be substituted with a public download.
    repeated={**result,'built_wheel_artifact_ids':['new-id'],
        'built_wheels':[{**p,'artifact_id':'new-id'} for p in result['built_wheels']]}
    assert observation('resolve_dependencies',result)==observation('resolve_dependencies',repeated)
    metadata=jobs.resources.artifact(built['built_wheels'][0]['metadata_artifact_id'])
    Path(metadata['locator']).write_text('changed metadata',encoding='utf-8')
    with pytest.raises(Failure,match='校验和'):
        engine.resolution.lock(deps,result['resolution_lock_artifact_id'])
