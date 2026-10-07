"""Metadata/protocol fault fixtures; these do not prove real pip execution."""
import hashlib
import json

import pytest
from resolvelib import BaseReporter, Resolver
from resolvelib.resolvers import ResolutionImpossible
from test_acquisition import WHEEL, ReleaseFixture, public_task
from test_jobs import ExecutorFixture
from test_network import resolve as approve

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.progress import observation
from forestry_agent.resolution import PROBE_CODE, WheelProvider, requirement, target_tags

TARGET = {'python_full_version': '3.12.12', 'sys_platform': 'linux', 'implementation_name': 'cpython',
    'platform_machine': 'x86_64', 'platform_system': 'Linux', 'platform_release': 'fixture',
    'platform_version': 'fixture', 'platform_python_implementation': 'CPython', 'libc': ['glibc', '2.36'],
    'soabi': 'cpython-312-x86_64-linux-gnu'}


class IndexFixture:
    def __init__(self):
        self.bodies, self.calls = {}, []
        self.after = lambda: None

    def add(self, name, v='1.0', dependencies=(), tag='py3-none-any', python='>=3.8', metadata_name=None, **overrides):
        filename = name.replace('-', '_')+'-'+v+'-'+tag+'.whl'
        url = 'https://files.pythonhosted.org/packages/ab/'+filename
        metadata = '\n'.join(['Metadata-Version: 2.3', 'Name: '+(metadata_name or name), 'Version: '+v,
            'Requires-Python: '+python, *['Requires-Dist: '+d for d in dependencies], '', '']).encode()
        index_url = f'https://pypi.org/simple/{name}/'
        index = json.loads(self.bodies.get(index_url, b'{}')) or {'name': name, 'meta': {'api-version': '1.4'}, 'files': []}
        index['files'].append({'filename': filename, 'url': url, 'yanked': False, 'requires-python': python,
            'hashes': {'sha256': hashlib.sha256(b'bounded-download-fixture-never-installed').hexdigest()},
            'core-metadata': {'sha256': hashlib.sha256(metadata).hexdigest()}, **overrides})
        self.bodies[index_url] = json.dumps(index).encode()
        self.bodies[url+'.metadata'] = metadata
        return index_url, url

    def get(self, url, **kwargs):
        kwargs['guard'](url)
        self.calls.append(url)
        data = self.bodies[url]
        self.after()
        return {'body': data}

    def fetch(self, url, simple=False):
        self.calls.append(url)
        return self.bodies[url]


def selected(fake, roots):
    return Resolver(WheelProvider(TARGET, fake.fetch), BaseReporter()).resolve([requirement(r) for r in roots]).mapping


def test_native_resolver_backtracks_versions_and_transitives():
    fake = IndexFixture()
    fake.add('a', '2.0', ['c>=2']); fake.add('a', '1.0', ['c<2'])
    fake.add('b', '1.0', ['c<2']); fake.add('c', '2.0'); fake.add('c', '1.0')
    mapping = selected(fake, ['a>=1', 'b==1.0'])
    assert {n: str(c.version) for n, c in mapping.items()} == {'a': '1.0', 'b': '1.0', 'c': '1.0'}
    assert any('/a-2.0-' in u for u in fake.calls)


def test_extras_merge_and_markers_use_linux_target_not_windows_host():
    fake = IndexFixture()
    fake.add('a', dependencies=['fast; extra == "fast"', 'safe; extra == "safe"', 'linux; sys_platform == "linux"', 'windows; sys_platform == "win32"'])
    fake.add('b', dependencies=['a[safe]'])
    for n in ['fast', 'safe', 'linux']:
        fake.add(n)
    assert set(selected(fake, ['a[fast]', 'b'])) == {'a', 'b', 'fast', 'safe', 'linux'}
    assert not any('/windows/' in u for u in fake.calls)


def test_unsatisfiable_graph_is_not_partial_success_and_prerelease_is_explicit():
    fake = IndexFixture()
    fake.add('a', dependencies=['b<2']); fake.add('b', '2.0'); fake.add('b', '1.0'); fake.add('b', '3.0b1')
    with pytest.raises(ResolutionImpossible):
        selected(fake, ['a', 'b>=2'])
    assert str(selected(fake, ['b>=1', 'b==3.0b1'])['b'].version) == '3.0b1'
    assert str(selected(fake, ['b'])['b'].version) == '2.0'


def test_target_tag_ranking_filters_host_wheels_and_python_constraints():
    fake = IndexFixture()
    fake.add('a', '3.0', tag='cp312-cp312-win_amd64')
    fake.add('a', '2.0', python='>=3.13')
    fake.add('a', '1.0', tag='cp312-cp312-manylinux_2_36_x86_64')
    fake.add('a', '1.0', tag='py3-none-any')
    assert selected(fake, ['a'])['a'].filename.endswith('manylinux_2_36_x86_64.whl')
    assert not any('win_amd64.whl.metadata' in u for u in fake.calls)
    with pytest.raises(Failure):
        target_tags({**TARGET, 'libc': ['musl', '1.2.5']})


@pytest.mark.parametrize('bad', ['x @ https://example.com/x.whl', '../x', '--extra-index-url https://example.com', 'x; unknown_marker=="y"'])
def test_direct_sources_and_invalid_requirements_never_fetch(bad):
    with pytest.raises(Failure):
        requirement(bad)


@pytest.mark.parametrize('condition', ['hash', 'identity', 'direct_dependency', 'duplicate_name'])
def test_core_metadata_hash_identity_and_sources_are_checked(condition):
    fake = IndexFixture()
    _, url = fake.add('a', metadata_name='other' if condition == 'identity' else None,
        dependencies=['b @ https://example.com/b.whl'] if condition == 'direct_dependency' else [])
    if condition in {'hash', 'duplicate_name'}:
        fake.bodies[url+'.metadata'] += b'Name: duplicate\n' if condition == 'duplicate_name' else b'tamper'
        if condition == 'duplicate_name':
            # Put the duplicate in the header, then attest those exact bytes.
            fake.bodies[url+'.metadata'] = fake.bodies[url+'.metadata'].replace(b'\n\nName:', b'\nName:')
            index = json.loads(fake.bodies['https://pypi.org/simple/a/'])
            index['files'][0]['core-metadata']['sha256'] = hashlib.sha256(fake.bodies[url+'.metadata']).hexdigest()
            fake.bodies['https://pypi.org/simple/a/'] = json.dumps(index).encode()
    with pytest.raises(Failure):
        selected(fake, ['a'])


def prepared_target(engine, state):
    """Explicit fake terminal producer for provenance boundary tests only."""
    engine.jobs.docker = ExecutorFixture()
    deps = Deps(engine, state['task_id'], 'resolver-fixture', 1)
    result = engine.perform(deps, 'probe', 'environment.acquire', {'requirements': ['six==1.17.0']},
        lambda action: engine.resolution.resolve(deps, action, ['six==1.17.0']))
    assert result['kind'] == 'completed', result
    job = engine.jobs.read(result['job']['job_id'])
    assert job['code'] == PROBE_CODE and job['mounts'] == []
    artifact = engine.resources.write(state['task_id'], job['action_id'], 'execution-target.json', json.dumps(TARGET))
    job.update(status='succeeded', exit_code=0, image_id='sha256:'+'a'*64, artifact_ids=[artifact['artifact_id']])
    engine.jobs.save(job, 'job.fixture_terminal')
    return deps, artifact


def run_resolution(engine, deps, call, target, roots=None, approval=None):
    args = {'requirements': roots or ['six==1.17.0'], 'target_artifact_id': target['artifact_id'], 'approval_id': approval}
    return engine.perform(deps, call, 'environment.acquire', args,
        lambda action: engine.resolution.resolve(deps, action, args['requirements'], args['target_artifact_id'], approval))


def test_private_resolution_approval_locks_exact_roots_and_target(client, app):
    from test_core import task
    state = task(client, objective='PRIVATE_CONTENT_NOT_SENT')[0]
    engine, fake = app.state.engine, IndexFixture()
    fake.add('six', '1.17.0', tag='py2.py3-none-any')
    engine.acquisition.http = fake
    deps, target = prepared_target(engine, state)
    assert run_resolution(engine, deps, 'proposal', target)['kind'] == 'approval_required'
    grant = engine.network.list(state['task_id'])[0]
    assert fake.calls == [] and 'PRIVATE_CONTENT_NOT_SENT' not in json.dumps(grant['request'])
    assert grant['request']['metadata_only'] and grant['request']['transitive_dependencies']
    assert approve(client, state, grant).status_code == 200
    mismatch = run_resolution(engine, deps, 'changed', target, ['six>=1'], grant['approval_id'])
    assert mismatch['error']['code'] == 'VERSION_CONFLICT' and fake.calls == []
    result = run_resolution(engine, deps, 'resolve', target, approval=grant['approval_id'])
    assert result['kind'] == 'completed' and result['wheels'] == [WHEEL]
    engine.resolution.lock(deps, result['resolution_lock_artifact_id'])
    before = len(fake.calls)
    assert run_resolution(engine, deps, 'resolve', target, approval=grant['approval_id']) == result
    assert len(fake.calls) == before


@pytest.mark.parametrize('change', ['pause', 'revision', 'revoke'])
def test_resolution_stops_before_next_transitive_request(client, app, change):
    from test_core import task
    state = task(client)[0]
    engine, fake = app.state.engine, IndexFixture()
    fake.add('six', '1.17.0', ['other']); fake.add('other')
    engine.acquisition.http = fake
    deps, target = prepared_target(engine, state)
    run_resolution(engine, deps, 'proposal', target)
    grant = engine.network.list(state['task_id'])[0]
    approve(client, state, grant)
    def changed():
        with engine.store.transaction() as db:
            if change == 'revoke':
                record = json.loads(db.execute('SELECT payload FROM approvals WHERE id=?', (grant['approval_id'],)).fetchone()[0])
                record['status'] = 'revoked'
                db.execute("UPDATE approvals SET status='revoked',payload=? WHERE id=?", (json.dumps(record), record['approval_id']))
            else:
                current = engine.store.read_task(db, state['task_id'])
                current['pause_requested'] = change == 'pause'
                current['spec_revision'] += change == 'revision'
                engine.store.save_task(db, current, current['state_version'])
    fake.after = changed
    result = run_resolution(engine, deps, 'execute', target, approval=grant['approval_id'])
    assert result['kind'] == 'failed' and len(fake.calls) == 1


def test_download_is_bound_to_resolution_lock_and_target_reused(client, app):
    state = public_task(client)
    engine, fake = app.state.engine, IndexFixture()
    fake.add('six', '1.17.0', tag='py2.py3-none-any'); engine.acquisition.http = fake
    deps, target = prepared_target(engine, state)
    result = run_resolution(engine, deps, 'resolve', target)
    lock = result['resolution_lock_artifact_id']
    reused = engine.perform(deps, 'probe-again', 'environment.acquire', {}, lambda a: engine.resolution.resolve(deps, a, ['six']))
    assert reused['target_artifact_id'] == target['artifact_id']
    with engine.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM jobs WHERE task_id=?', (state['task_id'],)).fetchone()[0] == 1
    engine.acquisition.http = ReleaseFixture()
    downloaded = engine.perform(deps, 'download', 'environment.acquire', {},
        lambda a: engine.acquisition.acquire(deps, a, [WHEEL], resolution_lock_artifact_id=lock))
    assert downloaded['kind'] == 'completed'
    engine.acquisition.http.entry['digests']['sha256'] = 'b'*64
    changed = engine.perform(deps, 'download-changed', 'environment.acquire', {},
        lambda a: engine.acquisition.acquire(deps, a, [WHEEL], resolution_lock_artifact_id=lock))
    assert changed['error']['code'] == 'VERSION_CONFLICT'
    assert len(engine.acquisition.http.calls) == 3  # two successful fetches, one changed metadata
    wrong = engine.perform(deps, 'partial-set', 'environment.acquire', {},
        lambda a: engine.acquisition.acquire(deps, a, [{**WHEEL, 'version': '1.16.0'}], resolution_lock_artifact_id=lock))
    assert wrong['error']['code'] == 'VERSION_CONFLICT'
    assert observation('resolve_dependencies', result) == observation('resolve_dependencies', {**result,
        'action_id': 'new', 'resolution_lock_artifact_id': 'new', 'metadata_requests': 20})


def test_compressed_index_wire_bytes_share_the_whole_resolution_budget(client, app, monkeypatch):
    import forestry_agent.resolution as module
    monkeypatch.setattr(module, 'MAX_TOTAL_BYTES', 2048)
    class WireFixture(IndexFixture):
        def get(self, url, **kwargs):
            result = super().get(url, **kwargs)
            result['wire_bytes'] = 1024 if kwargs.get('simple_json') else len(result['body'])
            if result['wire_bytes'] > kwargs['max_bytes']:
                raise Failure('RESOURCE_EXHAUSTED', 'compressed index wire budget fixture', 422)
            return result
    fake = WireFixture()
    fake.add('six', '1.17.0', ['other'], tag='py2.py3-none-any'); fake.add('other')
    engine, state = app.state.engine, public_task(client)
    engine.acquisition.http = fake
    deps, target = prepared_target(engine, state)
    result = run_resolution(engine,deps,'wire-limited-resolution',target)
    assert result['kind'] == 'failed' and result['error']['code'] == 'RESOURCE_EXHAUSTED'
    assert len(fake.calls) == 3
    with engine.store.transaction() as db:
        artifacts = [json.loads(r[0]) for r in db.execute('SELECT payload FROM artifacts WHERE task_id=?',(state['task_id'],))]
    assert not any(a['name']=='resolution-lock.json' for a in artifacts)


def test_resolution_counts_redirect_hops_against_the_request_limit(client, app, monkeypatch):
    import forestry_agent.resolution as module
    monkeypatch.setattr(module,'MAX_REQUESTS',2)
    class HopFixture(IndexFixture):
        def get(self,url,**kwargs):
            assert kwargs['max_hops']==2
            result = super().get(url,**kwargs)
            result['hops'] = [{"url":url},{"url":url}]
            return result
    fake = HopFixture();fake.add('six','1.17.0',tag='py2.py3-none-any')
    engine,state = app.state.engine,public_task(client)
    engine.acquisition.http = fake
    deps,target = prepared_target(engine,state)
    result = run_resolution(engine,deps,'hop-limited-resolution',target)
    assert result['kind']=='failed' and result['error']['code']=='RESOURCE_EXHAUSTED'
    assert len(fake.calls)==1


def test_probe_requires_code_capability_and_forged_target_is_rejected(client, app):
    engine = app.state.engine
    state = public_task(client, constraints={'allowed_capabilities': ['environment.acquire']})
    deps = Deps(engine, state['task_id'], 'denied-probe', 1)
    denied = engine.perform(deps, 'probe', 'environment.acquire', {}, lambda a: engine.resolution.resolve(deps, a, ['six']))
    assert denied['error']['code'] == 'PERMISSION_DENIED'
    artifact = engine.resources.write(state['task_id'], 'fabricated', 'execution-target.json', json.dumps(TARGET))
    with pytest.raises(Failure):
        engine.resolution.target(deps, artifact['artifact_id'])


def test_unused_direct_dependency_marker_is_ignored_but_selected_url_is_rejected():
    fake = IndexFixture()
    fake.add('a', dependencies=['other @ https://example.com/other.whl ; sys_platform == "win32"'])
    assert set(selected(fake, ['a'])) == {'a'}
    assert len(fake.calls) == 2


def test_yanked_empty_reason_and_missing_core_hash_are_never_selected():
    fake = IndexFixture()
    fake.add('a', '3.0', yanked='')
    fake.add('a', '2.0', **{'core-metadata': True})
    fake.add('a', '1.0')
    assert str(selected(fake, ['a'])['a'].version) == '1.0'
    assert not any('-3.0-' in u or '-2.0-' in u for u in fake.calls)


def test_resolution_request_bound_and_target_tamper_stop_without_partial_lock(client, app, monkeypatch):
    import forestry_agent.resolution as module
    state = public_task(client)
    engine, fake = app.state.engine, IndexFixture()
    fake.add('six', '1.17.0', ['other']); fake.add('other')
    engine.acquisition.http = fake
    deps, target = prepared_target(engine, state)
    monkeypatch.setattr(module, 'MAX_REQUESTS', 2)
    stopped = run_resolution(engine, deps, 'bounded', target)
    assert stopped['error']['code'] == 'RESOURCE_EXHAUSTED' and len(fake.calls) == 2
    with engine.store.transaction() as db:
        assert not db.execute("SELECT 1 FROM artifacts WHERE task_id=? AND json_extract(payload,'$.name')='resolution-lock.json'", (state['task_id'],)).fetchone()
    from forestry_agent.resources import native_path
    native_path(target['locator']).write_text('{}')
    changed = run_resolution(engine, deps, 'tamper', target)
    assert changed['kind'] == 'failed' and len(fake.calls) == 2


def test_child_cannot_start_target_probe_or_fetch_metadata(client, app):
    state = public_task(client)
    engine = app.state.engine
    deps = Deps(engine, state['task_id'], 'child-fixture', 1, assignment_id='unprivileged-child')
    with pytest.raises(Failure) as error:
        engine.resolution.resolve(deps, 'not-started', ['six'])
    assert error.value.code == 'PERMISSION_DENIED'
