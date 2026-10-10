"""Source-resolution/provenance fixtures; no source is unpacked or executed."""
import hashlib
import json
from pathlib import Path

import pytest
from resolvelib import BaseReporter, Resolver
from resolvelib.resolvers import ResolutionImpossible
from test_acquisition import public_task
from test_core import task
from test_jobs import ExecutorFixture
from test_network import resolve as approve
from test_resolution import TARGET, IndexFixture, prepared_target, run_resolution, selected
from test_source_acquisition import SOURCE, SourceFixture

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.progress import observation
from forestry_agent.resolution import SourceRequired, WheelProvider, requirement
from forestry_agent.resources import native_path


class SourceIndexFixture(IndexFixture):
    def __init__(self):
        super().__init__()
        self.release = SourceFixture()

    def add_source(self, name='six', v='1.17.0', **overrides):
        url=f'https://pypi.org/simple/{name}/'
        body=json.loads(self.bodies.get(url,b'{}')) or {'name':name,'meta':{'api-version':'1.4'},'files':[]}
        body['files'].append({'filename':name+'-'+v+'.tar.gz',
            'url':'https://files.pythonhosted.org/packages/ab/'+name+'-'+v+'.tar.gz','yanked':False,
            'requires-python':'>=3.8','hashes':{'sha256':hashlib.sha256(self.release.data).hexdigest()},**overrides})
        self.bodies[url]=json.dumps(body).encode()

    def get(self,url,**kwargs):
        if url in self.bodies:
            return super().get(url,**kwargs)
        response=self.release.get(url,**kwargs)
        self.calls.append(url)
        return response


def test_source_candidate_uses_native_version_constraints_but_never_downloads_metadata_or_archive():
    fake=SourceIndexFixture();fake.add('a','1.0');fake.add_source('a','2.0');fake.add_source('a','3.0b1')
    assert str(selected(fake,['a<2'])['a'].version)=='1.0'
    fake.calls.clear()
    with pytest.raises(SourceRequired) as error:
        selected(fake,['a[feature]>=2'])
    assert str(error.value.candidate.version)=='2.0' and error.value.candidate.extras==frozenset({'feature'})
    assert fake.calls==['https://pypi.org/simple/a/']


def test_same_version_compatible_wheel_precedes_source_and_source_can_be_transitive():
    fake=SourceIndexFixture();fake.add('a','1.0');fake.add_source('a','1.0')
    assert selected(fake,['a==1.0'])['a'].source_distribution is False
    fake=SourceIndexFixture();fake.add('a',dependencies=['b>=2; sys_platform == "linux"'])
    fake.add_source('b','1.0');fake.add_source('b','2.0')
    with pytest.raises(SourceRequired) as error:
        selected(fake,['a'])
    assert error.value.candidate.name=='b' and str(error.value.candidate.version)=='2.0'
    assert not any(u.endswith(('.tar.gz','.tar.gz.metadata')) for u in fake.calls)


@pytest.mark.parametrize('change',[
    {'yanked':''}, {'hashes':{'sha256':'not-a-hash'}}, {'requires-python':'>=3.13'},
    {'filename':'other-1.17.0.tar.gz'}, {'url':'https://127.0.0.1/packages/ab/six-1.17.0.tar.gz'},
    {'url':'https://files.pythonhosted.org/packages/ab/six-1.17.0.tar.gz?secret=value'},
    {'filename':'../six-1.17.0.tar.gz'},
])
def test_source_candidate_rejects_unsafe_yanked_incompatible_or_unbound_entries(change):
    fake=SourceIndexFixture();fake.add_source(**change)
    with pytest.raises(ResolutionImpossible):
        Resolver(WheelProvider(TARGET,fake.fetch),BaseReporter()).resolve([requirement('six==1.17.0')])
    assert fake.calls==['https://pypi.org/simple/six/']


def test_private_source_candidate_and_download_have_separate_exact_approvals(client,app):
    state,_=task(client,objective='PRIVATE_CONTENT_NOT_SENT')
    engine=app.state.engine;fake=SourceIndexFixture();fake.add_source();engine.acquisition.http=fake
    deps,target=prepared_target(engine,state)
    assert run_resolution(engine,deps,'metadata-proposal',target)['kind']=='approval_required' and not fake.calls
    metadata_grant=engine.network.list(state['task_id'])[-1];approve(client,state,metadata_grant).raise_for_status()
    result=run_resolution(engine,deps,'metadata',target,approval=metadata_grant['approval_id'])
    assert result['kind']=='completed' and result['resolution_complete'] is False and result['wheels']==[]
    assert result['source_candidates']==[SOURCE] and len(fake.calls)==1
    lock=result['resolution_lock_artifact_id']
    manifest,_=engine.resolution.lock(deps,lock,source_candidate=True)
    assert manifest['kind']=='python_source_candidate' and manifest['requirements']==['six==1.17.0']
    assert manifest['selected'][0]['sha256']==hashlib.sha256(fake.release.data).hexdigest()
    with pytest.raises(Failure,match='wheel解析锁'):
        engine.resolution.lock(deps,lock)
    summary=engine.acquisition.confirmed_receipts(state['task_id'],1)[0]
    assert summary['operation']=='source_candidate' and summary['resolution_complete'] is False
    assert summary['source_candidates']==[SOURCE] and summary['source_action_id']==result['action_id']
    reused=run_resolution(engine,deps,'reuse-candidate',target)
    assert reused['reused_from_action_id']==result['action_id'] and reused['resolution_complete'] is False
    assert reused['source_candidates']==[SOURCE] and reused['metadata_requests']==0 and len(fake.calls)==1
    assert len(engine.network.list(state['task_id']))==1
    def download(call,approval=None,sources=None):
        args={'source_packages':sources or [SOURCE],'resolution_lock_artifact_id':lock,'approval_id':approval}
        return engine.perform(deps,call,'environment.acquire',args,lambda a:
            engine.acquisition.acquire(deps,a,[],approval_id=approval,resolution_lock_artifact_id=lock,source_packages=args['source_packages']))
    wrong=download('metadata-cannot-download',metadata_grant['approval_id'])
    assert wrong['error']['code']=='VERSION_CONFLICT' and len(fake.calls)==1
    assert download('download-proposal')['kind']=='approval_required' and len(fake.calls)==1
    download_grant=engine.network.list(state['task_id'])[-1]
    assert download_grant['request']['resolution_lock_artifact_id']==lock
    assert 'PRIVATE_CONTENT_NOT_SENT' not in json.dumps(download_grant['request'])
    approve(client,state,download_grant,request_id='approve-download').raise_for_status()
    assert download('different-source',download_grant['approval_id'],[{**SOURCE,'version':'1.16.0'}])['error']['code']=='VERSION_CONFLICT'
    downloaded=download('approved-source',download_grant['approval_id'])
    assert downloaded['kind']=='completed' and len(downloaded['source_artifact_ids'])==1
    assert fake.calls[-1].endswith('.tar.gz') and len(fake.calls)==3
    assert observation('resolve_dependencies',result)==observation('resolve_dependencies',{**result,'resolution_lock_artifact_id':'new-id','metadata_requests':50})


def test_candidate_official_hash_change_stops_before_archive_and_tampering_invalidates_facts(client,app):
    state=public_task(client);engine=app.state.engine;fake=SourceIndexFixture();fake.add_source();engine.acquisition.http=fake
    deps,target=prepared_target(engine,state);result=run_resolution(engine,deps,'source-candidate',target)
    lock=result['resolution_lock_artifact_id'];fake.release.entry['digests']['sha256']='0'*64
    stopped=engine.perform(deps,'changed-release','environment.acquire',{'source_packages':[SOURCE]},lambda a:
        engine.acquisition.acquire(deps,a,[],source_packages=[SOURCE],resolution_lock_artifact_id=lock))
    assert stopped['error']['code']=='VERSION_CONFLICT' and fake.calls[-1].endswith('/json')
    assert not any(u.endswith('.tar.gz') for u in fake.calls)
    artifact=engine.resources.artifact(lock,state['task_id']);Path(artifact['locator']).write_text('{}',encoding='utf-8')
    assert not any(r['operation']=='source_candidate' for r in engine.acquisition.confirmed_receipts(state['task_id'],1))
    with pytest.raises(Failure,match='校验和'):
        engine.resolution.lock(deps,lock,source_candidate=True)


@pytest.mark.parametrize('identity', ['same','other_name','other_version','equivalent_version'])
def test_built_wheel_identity_is_bound_to_confirmed_acquired_source(client,app,identity):
    state=public_task(client);engine=app.state.engine;engine.acquisition.http=SourceFixture()
    deps=Deps(engine,state['task_id'],'source-identity',1)
    acquired=engine.perform(deps,'download','environment.acquire',{'source_packages':[SOURCE]},lambda a:
        engine.acquisition.acquire(deps,a,[],source_packages=[SOURCE]))
    ref=acquired['source_artifact_ids'][0];jobs=engine.jobs;jobs.docker=ExecutorFixture()
    prepared=engine.perform(deps,'build','environment.prepare',{'source_artifact_ids':[ref],'build_only':True},lambda a:
        engine.environments.prepare(state['task_id'],1,a,[],source_artifact_ids=[ref],build_only=True))
    job_id=prepared['job']['job_id'];jobs.tick();job=jobs.read(job_id);out=Path(job['directory'])/'output'
    name='other' if identity=='other_name' else 'six';v='2.0' if identity=='other_version' else '1.17' if identity=='equivalent_version' else '1.17.0'
    filename=f'{name}-{v}-py3-none-any.whl';wheel=b'fake-wheel-not-installed'
    metadata=f'Metadata-Version: 2.3\nName: {name}\nVersion: {v}\n\n'.encode()
    record={'filename':filename,'sha256':hashlib.sha256(wheel).hexdigest(),'size_bytes':len(wheel),
        'source_resource_id':ref,'source_sha256':job['source_recipe'][0]['sha256'],
        'metadata_filename':filename+'.metadata','metadata_sha256':hashlib.sha256(metadata).hexdigest(),'metadata_size_bytes':len(metadata)}
    manifest={'schema_version':1,'kind':'source_wheel_build','runtime_installation':False,'network':'none','python':'3.12.12',
        'image_reference':job['image_reference'],'sources':job['source_recipe'],'wheels':job['environment_recipe'],
        'built_wheels':[record],'build_frontend':{'isolation':'native_pep517'}}
    native_path(out/filename).write_bytes(wheel);native_path(out/(filename+'.metadata')).write_bytes(metadata)
    (out/'source-build-lock.json').write_text(json.dumps(manifest),encoding='utf-8')
    jobs.docker.container['State'].update(Running=False,Status='exited',ExitCode=0);jobs.tick()
    if identity in {'other_name','other_version'}:
        with pytest.raises(Failure,match='源码构建锁'):
            jobs.observation(job_id,state['task_id'])
    else:
        assert jobs.observation(job_id,state['task_id'])['prepared_wheels']['built_wheels'][0]['name']=='six'
