"""Inspection authority fixtures; backend/archive execution is container-only."""
import json
from pathlib import Path

import pytest
from test_core import task
from test_environments import wheel
from test_source_build_only import built_fixture
from test_source_environments import source_job

from forestry_agent.contracts import Failure
from forestry_agent.environments import preparation_code
from forestry_agent.resources import native_path


def inspection_fixture(client, app, tmp_path, mode='static', fault=None):
    source, ref, state, _deps, jobs, environments, job_id = source_job(
        client,app,tmp_path,inspect_build=mode)
    jobs.tick()
    job=jobs.read(job_id)
    project={'source_resource_id':ref,'source_sha256':job['source_recipe'][0]['sha256'],
        'static_requires':['setuptools>=40.8.0'],'dynamic_requires':[] if mode=='static' else ['wheel>=0.45'],
        'build_backend':'setuptools.build_meta:__legacy__','backend_path':[],
        'frontend':None if mode=='static' else {'name':'pyproject-hooks','version':'1.2.0','hook':'get_requires_for_build_wheel'}}
    manifest={'schema_version':1,'kind':'source_build_requirements','inspection':mode,
        'image_reference':job['image_reference'],'python':'3.12.12','network':'none',
        'sources':job['source_recipe'],'wheels':job['environment_recipe'],'projects':[project],
        'runtime_installation':False}
    if fault=='source':project['source_sha256']='0'*64
    if fault=='url':project['static_requires']=['backend @ https://example.com/private.whl']
    if fault=='purpose':manifest['runtime_installation']=True
    if fault=='dynamic_in_static':project['dynamic_requires']=['wheel']
    if fault=='frontend':project['frontend']['version']='unknown'
    if fault=='backend_path':project['backend_path']=[5]
    output=native_path(Path(job['directory'])/'output')
    (output/'build-requirements.json').write_text(json.dumps(manifest),encoding='utf-8')
    if fault=='fake_environment':
        (output/'environment.zip').write_bytes(b'backend fabricated installed environment')
        (output/'environment-lock.json').write_text('{}',encoding='utf-8')
    jobs.docker.container['State'].update(Running=False,Status='exited',ExitCode=0)
    jobs.tick()
    if fault=='code':
        changed=jobs.read(job_id);changed['code']='print("pretend inspection")';jobs.save(changed,'test.fixture')
    return source,state,jobs,environments,job_id


@pytest.mark.parametrize('mode',['static','dynamic'])
def test_inspection_returns_requirements_bound_to_current_real_job_and_source(client,app,tmp_path,mode):
    source,state,jobs,environments,job_id=inspection_fixture(client,app,tmp_path,mode)
    observed=jobs.observation(job_id,state['task_id'])
    assert 'prepared_environment' not in observed and 'prepared_wheels' not in observed
    found=observed['build_requirements']
    assert found['inspection']==mode and found['runtime_installation'] is False
    assert found['requirements']==['setuptools>=40.8.0']+([] if mode=='static' else ['wheel>=0.45'])
    assert found['frontend_requirements']==['pyproject-hooks==1.2.0','packaging==25.0']
    compact=jobs.model_receipt(observed)
    assert 'projects' not in compact['build_requirements'] and found['projects']
    assert compact['build_requirements']['requirements']==found['requirements']
    with pytest.raises(Failure):environments.requirements(state['task_id'],2,jobs.read(job_id)['action_id'])
    source.write_bytes(b'changed source')
    with pytest.raises(Failure):environments.requirements(state['task_id'],1,jobs.read(job_id)['action_id'])


@pytest.mark.parametrize('mode,fault',[('static','source'),('static','url'),('static','purpose'),
    ('static','dynamic_in_static'),('dynamic','frontend'),('dynamic','code'),('static','backend_path')])
def test_unverified_inspection_cannot_propose_dependencies(client,app,tmp_path,mode,fault):
    _,state,jobs,_environments,job_id=inspection_fixture(client,app,tmp_path,mode,fault)
    with pytest.raises(Failure):jobs.observation(job_id,state['task_id'])


def test_inspection_cannot_masquerade_as_installed_environment_or_built_wheel(client,app,tmp_path):
    _,state,jobs,environments,job_id=inspection_fixture(client,app,tmp_path,fault='fake_environment')
    result=jobs.observation(job_id,state['task_id'])
    assert result['build_requirements']['runtime_installation'] is False
    job=jobs.read(job_id)
    ref=next(r for r in job['artifact_ids'] if jobs.resources.artifact(r)['name']=='environment.zip')
    with pytest.raises(Failure,match='检查不能'):environments.resolve(state['task_id'],1,ref)
    with pytest.raises(Failure):environments.built(state['task_id'],1,job['action_id'])
    manifest=jobs.resources.artifact(result['build_requirements']['artifact_id'])
    native_path(manifest['locator']).write_text('{}',encoding='utf-8')
    with pytest.raises(Failure,match='校验和'):jobs.observation(job_id,state['task_id'])


def test_inspection_modes_cannot_be_submitted_without_source_or_with_build_only(app):
    for mode,build in [('invalid',False),('static',True),('dynamic',False)]:
        with pytest.raises(Failure):app.state.engine.environments.prepare('none',1,'none',[],inspect_build=mode,build_only=build)
    for mode in ('static','dynamic'):
        code=preparation_code([],'image',[{'resource_id':'source','filename':'s.zip'}],inspect_build=mode)
        compile(code,'trusted-inspection-worker','exec')  # Syntax only, never execute on host.
        assert len(code.encode())<65536


def test_exact_historical_source_build_remains_valid_but_modified_code_does_not(client,app,tmp_path):
    state,_,jobs,environments,job_id=built_fixture(client,app,tmp_path)
    job=jobs.read(job_id)
    job['code']=preparation_code(job['environment_recipe'],job['image_reference'],job['source_recipe'],
        build_only=True,_legacy=True)
    jobs.save(job,'test.legacy_fixture')
    assert environments.built(state['task_id'],1,job['action_id'])['wheel_artifact_ids']
    job['code']+='\nprint("changed")';jobs.save(job,'test.modified_fixture')
    with pytest.raises(Failure):environments.built(state['task_id'],1,job['action_id'])


@pytest.mark.parametrize('frontend',['none','backend_only','wrong_frontend_version'])
def test_dynamic_missing_frontend_has_actionable_failure_and_no_job(client,app,tmp_path,frontend):
    _,ref=wheel(client,tmp_path,'fixture.tar.gz','source')
    refs=[]
    if frontend!='none':
        names=['setuptools-80.9.0-py3-none-any.whl'] if frontend=='backend_only' else [
            'pyproject_hooks-1.1.0-py3-none-any.whl','packaging-25.0-py3-none-any.whl']
        for name in names:
            _,value=wheel(client,tmp_path,name,'verified-protocol-fixture-'+name)
            refs.append(value)
    state,_=task(client,input_refs=[ref,*refs])
    with pytest.raises(Failure,match='尚未提交作业') as failure:
        app.state.engine.environments.prepare(state['task_id'],1,'missing-frontend',refs,
            source_refs=[ref],inspect_build='dynamic')
    assert failure.value.code=='PRECONDITION_MISSING'
    with app.state.store.transaction() as db:
        assert not db.execute('SELECT 1 FROM jobs').fetchone()
