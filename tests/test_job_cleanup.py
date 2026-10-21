import pytest
from test_jobs import ExecutorFixture, submit

from forestry_agent.contracts import Failure

pytestmark=pytest.mark.offline_web


class CleanupExecutor(ExecutorFixture):
    def call(self,*args):
        if args[0]=='logs':
            self.commands.append(args)
            return {'code':0,'stdout':'saved output','stderr':'saved diagnostic','window':'tail'}
        result=super().call(*args)
        if args[0]=='rm':self.container=None
        return result


def test_terminal_cleanup_archives_logs_removes_container_and_duplicate_output_but_keeps_result(client,app):
    fake=CleanupExecutor()
    state,manager,job_id=submit(client,app,fake)
    manager.tick()
    job=manager.read(job_id)
    from pathlib import Path
    output=Path(job['directory'])/'output'
    (output/'result.txt').write_text('correct result')
    fake.container['State'].update(Running=False,Status='exited',ExitCode=0)
    manager.tick()
    job=manager.read(job_id)
    assert job['status']=='succeeded' and job['artifact_ids']
    assert manager.cleanup_terminal(job) is True
    assert fake.container is None and not output.exists()
    artifact=app.state.resources.artifact(job['artifact_ids'][0],state['task_id'])
    assert Path(artifact['locator']).read_text()=='correct result'
    assert manager.logs(job_id,state['task_id'])['stderr']=='saved diagnostic'
    before=list(fake.commands)
    assert manager.cleanup_terminal(manager.read(job_id)) is False
    assert before==fake.commands
    assert not any(c[0]=='kill' for c in fake.commands)


def test_terminal_cleanup_refuses_running_or_wrong_identity_container(client,app):
    fake=CleanupExecutor();_,manager,job_id=submit(client,app,fake)
    manager.tick();job=manager.read(job_id)
    job['status']='cancelled';manager.save(job,'fixture.terminal')
    with pytest.raises(Failure):manager.cleanup_terminal(job)
    fake.container['State']['Running']=False
    fake.container['Config']['Labels']['forestry.job']='unrelated'
    with pytest.raises(Failure):manager.cleanup_terminal(job)
    assert not any(c[0]=='rm' for c in fake.commands)


def test_session_deletion_removes_its_stopped_container_even_before_maintenance(client,app,monkeypatch):
    from test_session_deletion import erase
    fake=CleanupExecutor();state,manager,_job_id=submit(client,app,fake)
    monkeypatch.setattr(app.state.engine.jobs,'docker',fake)
    manager.tick()
    fake.container['State'].update(Running=False,Status='exited',ExitCode=0)
    manager.tick()
    assert erase(client,app.state.store.task(state['task_id'])).status_code==200
    assert fake.container is None
    assert not any(c[0]=='kill' for c in fake.commands)


def test_maintenance_keeps_output_that_is_registered_as_a_source(client,app):
    from pathlib import Path

    from test_session_deletion import register
    fake=CleanupExecutor();_,manager,job_id=submit(client,app,fake)
    manager.tick();job=manager.read(job_id)
    output=Path(job['directory'])/'output'/'used.txt';output.write_text('retain source')
    ref=register(client,output)
    fake.container['State'].update(Running=False,Status='exited',ExitCode=0)
    manager.tick()
    assert manager.cleanup_terminal(manager.read(job_id)) is True
    assert fake.container is None
    assert app.state.resources.resolve(ref).read_text()=='retain source'
    assert manager.read(job_id)['cleanup']['output_retained_as_source'] is True
