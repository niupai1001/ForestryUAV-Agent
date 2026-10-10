"""Fault fixtures; real resource measurements are recorded by the WSL smoke."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_jobs import ExecutorFixture, submit

from forestry_agent.admission import assess
from forestry_agent.jobs import Jobs


def events(manager, task_id, kind):
    with manager.store.transaction() as db:
        return db.execute('SELECT count(*) FROM events WHERE task_id=? AND kind=?', (task_id, kind)).fetchone()[0]


def test_each_resource_is_required_including_executor_memory(app):
    config = app.state.engine.config
    request = config.execution_limits()
    profile = app.state.engine.jobs.resource_probe(config)
    assert assess(request, profile, config)['decision'] == 'admitted'
    for key, reason in [('host_ram_available', 'host_ram'), ('executor_ram_available', 'executor_ram'),
                        ('workspace_disk_free', 'workspace_disk')]:
        result = assess(request, {**profile, key: 0}, config)
        assert result['decision'] == 'wait' and result['missing'] == [reason]
    assert assess({**request, 'cpu_threads': 32}, profile, config)['effective_cpu_threads'] == 8


def test_wait_dedup_recovery_keeps_frozen_limits_and_one_job(client, app):
    fake = ExecutorFixture()
    state, manager, job_id = submit(client, app, fake)
    baseline = manager.resource_probe(manager.config)
    manager.resource_probe = lambda _: {**baseline, 'executor_ram_available': 0}
    manager.tick()
    first = manager.read(job_id)
    assert first['status'] == 'queued' and first['blocker']['code'] == 'RESOURCE_WAIT'
    assert fake.commands == [] and not Path(first['directory']).exists()
    manager.tick()
    assert events(manager, state['task_id'], 'job.resource_wait') == 1
    manager.config.max_code_cpus = 24
    manager.config.max_code_memory_bytes *= 2
    manager.config.max_code_output_bytes *= 2
    recovered = Jobs(manager.config, manager.store, manager.resources, fake, resource_probe=lambda _: baseline)
    recovered.tick()
    current = recovered.read(job_id)
    assert current['status'] == 'running' and current['blocker'] is None
    assert current['resource_request'] == first['resource_request']
    create = next(args for args in fake.commands if args[0] == 'create')
    assert create[create.index('--memory') + 1] == str(first['resource_request']['ram_bytes'])
    assert create[create.index('--cpus') + 1] == str(first['resource_request']['cpu_threads'])
    assert sum(args[0] == 'create' for args in fake.commands) == 1
    assert 'RLIMIT_FSIZE' in (Path(first['directory']) / 'run.py').read_text()
    listed = client.get(f"/tasks/{state['task_id']}/jobs").json()
    assert listed['jobs'][0]['job_id'] == job_id and 'code' not in listed['jobs'][0]
    assert client.get(f"/tasks/{state['task_id']}/jobs?offset=-1").status_code == 400


@pytest.mark.parametrize('already_exited', [False, True])
def test_oversized_output_requires_confirmed_stop_and_never_seals(client, app, already_exited):
    app.state.engine.config.max_code_output_bytes = 1024
    fake = ExecutorFixture()
    state, manager, job_id = submit(client, app, fake)
    # The task snapshot was made after this ceiling was chosen.
    manager.tick()
    output = Path(manager.read(job_id)['directory']) / 'output'
    (output / 'a').write_bytes(b'a' * 800)
    (output / 'b').write_bytes(b'b' * 800)
    if already_exited:
        fake.container['State'].update(Status='exited', Running=False, ExitCode=0)
    else:
        fake.refuse_stop = True
        manager.tick()
        pending = manager.read(job_id)
        assert pending['status'] == 'running' and pending['blocker']['code'] == 'RECONCILIATION_REQUIRED'
        assert pending['resource_stop_requested']['output_bytes'] == 1600
        # Retained stop intent survives a manager restart and output deletion.
        (output / 'b').unlink()
        fake.refuse_stop = False
        manager = Jobs(manager.config, manager.store, manager.resources, fake)
    manager.tick()
    job = manager.read(job_id)
    assert job['status'] == 'failed' and job['blocker']['code'] == 'RESOURCE_EXHAUSTED'
    assert job['artifact_ids'] == [] and app.state.store.task(state['task_id'])['artifact_ids'] == []
    assert fake.container['State']['Running'] is False


def test_artifact_copy_wait_resumes_collection_without_reexecution(client, app, monkeypatch):
    fake = ExecutorFixture()
    state, manager, job_id = submit(client, app, fake)
    manager.tick()
    output = Path(manager.read(job_id)['directory']) / 'output' / 'result'
    output.write_bytes(b'bounded-result')
    fake.container['State'].update(Status='exited', Running=False, ExitCode=0)
    monkeypatch.setattr('forestry_agent.jobs.shutil.disk_usage', lambda _: SimpleNamespace(free=0))
    manager.tick()
    waiting = manager.read(job_id)
    assert waiting['status'] == 'collecting' and waiting['blocker']['code'] == 'RESOURCE_WAIT'
    assert waiting['admission']['missing'] == ['artifact_copy_disk']
    manager.tick()
    assert events(manager, state['task_id'], 'job.resource_wait') == 1
    monkeypatch.setattr('forestry_agent.jobs.shutil.disk_usage', lambda _: SimpleNamespace(free=64 * 2**30))
    manager.tick()
    result = manager.read(job_id)
    assert result['status'] == 'succeeded' and result['blocker'] is None
    assert len(result['artifact_ids']) == 1 and result['output_bytes'] == 14
    assert sum(args[0] == 'create' for args in fake.commands) == 1
    assert sum(args[0] == 'start' for args in fake.commands) == 1
