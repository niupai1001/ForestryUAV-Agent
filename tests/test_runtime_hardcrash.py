"""Kill the actual service interpreter after committed filesystem side effects."""
import json
import subprocess
import sys

import psutil
import pytest
from fastapi.testclient import TestClient

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.contracts import Failure


CHILD = r'''
import json, os, sys
from pathlib import Path
from fastapi.testclient import TestClient
from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import Config
from forestry_agent.store import now

config = Config.model_validate_json(Path(sys.argv[1]).read_text(encoding='utf-8'))
mode = sys.argv[2]
app = create_app(config, enable_agent=False)
with TestClient(app, base_url='http://127.0.0.1', headers={'origin':'http://127.0.0.1'}) as client:
    client.get('/session')
    response = client.post('/tasks', json={'request_id':'crash-task','objective':'保存文件并解释结果','harness_mode':'H0'})
    assert response.status_code == 202, response.text
    task_id = response.json()['task_id']
    engine = app.state.engine
    def running(state, db):
        state.update(status='active', active_turn_id='crash-turn')
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',
                   ('crash-turn', task_id, 1, '保存文件并解释结果', 'running', now()))
    engine.store.change(task_id, 'test.running', running)
    def wait_for_kill():
        print(json.dumps({'pid':os.getpid(), 'task_id':task_id, 'point':mode}), flush=True)
        sys.stdin.read()
        raise RuntimeError('test failed to kill actual owner')
    if mode.startswith('job_'):
        result = engine.perform(Deps(engine, task_id, 'crash-turn', 1), 'job-call', 'code.execute',
            {'code':'raise RuntimeError("fixture")'},
            lambda action: engine.jobs.submit(task_id, 1, action, 'raise RuntimeError("fixture")', [], 60))
        job = engine.jobs.read(result['job']['job_id'])
        job.update(status='failed', exit_code=1, blocker={'code':'CONTROLLED_TEST_FAILURE','message':'fixture'})
        engine.jobs.save(job, 'job.terminal')
        if mode == 'job_claimed':
            assert len(engine.sessions.claim_job_input(task_id, 'crash-turn', 1)) == 1
        wait_for_kill()
    def effect(action_id):
        result = engine.resources.write(task_id, action_id, 'once.txt', 'committed bytes')
        if mode == 'effect_before_result':
            wait_for_kill()
        return result
    engine.perform(Deps(engine, task_id, 'crash-turn', 1), 'once-call', 'fs.write_workspace',
                   {'relative_path':'once.txt', 'content':'committed bytes'}, effect)
    wait_for_kill()
'''


@pytest.mark.parametrize('point', ['effect_before_result', 'result_before_response'])
def test_hardcrash_preserves_real_effect_and_never_replays(app, tmp_path, point):
    config = app.state.engine.config
    config_path = tmp_path / 'child-config.json'
    config_path.write_text(config.model_dump_json(), encoding='utf-8')
    with subprocess.Popen([sys.executable, '-u', '-c', CHILD, str(config_path), point],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True) as child:
        owner = None
        try:
            line = child.stdout.readline()
            assert line, child.stderr.read()
            handshake = json.loads(line)
            assert handshake['point'] == point
            owner = psutil.Process(handshake['pid'])
            assert owner.pid == child.pid or owner.ppid() == child.pid
            with pytest.raises(Failure) as error:
                create_app(config, enable_agent=False)
            assert error.value.code == 'WORKSPACE_IN_USE'
        finally:
            if owner is not None:
                owner.kill()
                owner.wait(timeout=10)
            else:
                child.kill()
            child.wait(timeout=10)

    recovered = create_app(config, enable_agent=False)
    with TestClient(recovered, base_url='http://127.0.0.1'):
        engine, task_id = recovered.state.engine, handshake['task_id']
        state = engine.store.task(task_id)
        assert state['status'] == 'paused' and state['action_count'] == 1
        assert state['model_requests'] == 0
        artifacts = [engine.resources.artifact(ref, task_id) for ref in state['artifact_ids']]
        assert len(artifacts) == 1
        assert engine.resources.task_root(task_id).joinpath('once.txt').read_text() == 'committed bytes'
        replays = []

        def must_not_execute(action_id):
            replays.append(action_id)
            pytest.fail('committed side effect was replayed')

        deps = Deps(engine, task_id, 'crash-turn', 1)
        args = {'relative_path': 'once.txt', 'content': 'committed bytes'}
        if point == 'effect_before_result':
            assert state['blockers'][0]['code'] == 'RECONCILIATION_REQUIRED'
            with pytest.raises(Failure) as error:
                engine.perform(deps, 'once-call', 'fs.write_workspace', args, must_not_execute)
            assert error.value.code == 'RECONCILIATION_REQUIRED'
        else:
            result = engine.perform(deps, 'once-call', 'fs.write_workspace', args, must_not_execute)
            assert result['kind'] == 'completed' and result['artifact_id'] == artifacts[0]['artifact_id']
        assert not replays
        with engine.store.snapshot() as db:
            action = db.execute('SELECT status FROM actions WHERE task_id=?', (task_id,)).fetchone()
            attempt = db.execute('SELECT effect_state FROM attempts').fetchone()
            assert action['status'] == ('pending' if point == 'effect_before_result' else 'confirmed')
            assert attempt['effect_state'] == ('in_progress' if point == 'effect_before_result' else 'confirmed')
        assert len(engine.store.task(task_id)['artifact_ids']) == 1


@pytest.mark.parametrize('point', ['job_pending', 'job_claimed'])
def test_hardcrash_keeps_terminal_delivery_identity_without_new_job(app, tmp_path, point):
    config = app.state.engine.config
    config_path = tmp_path / 'job-child-config.json'
    config_path.write_text(config.model_dump_json(), encoding='utf-8')
    with subprocess.Popen([sys.executable, '-u', '-c', CHILD, str(config_path), point],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True) as child:
        owner = None
        try:
            line = child.stdout.readline()
            assert line, child.stderr.read()
            handshake = json.loads(line)
            assert handshake['point'] == point
            owner = psutil.Process(handshake['pid'])
            assert owner.pid == child.pid or owner.ppid() == child.pid
        finally:
            if owner is not None:
                owner.kill()
                owner.wait(timeout=10)
            else:
                child.kill()
            child.wait(timeout=10)

    task_id = handshake['task_id']
    recovered = create_app(config, enable_agent=False)
    with TestClient(recovered, base_url='http://127.0.0.1'):
        engine = recovered.state.engine
        with engine.store.snapshot() as db:
            deliveries = db.execute('SELECT * FROM job_deliveries').fetchall()
            assert len(deliveries) == 1
            original = dict(deliveries[0])
            assert original['state'] == ('pending' if point == 'job_pending' else 'needs_attention')
            assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
        job = engine.jobs.read(original['job_id'])
        assert job['status'] == 'failed' and job['container_id'] is None
        engine.jobs.save(job, 'job.terminal')
        with engine.store.transaction() as db:
            db.execute("UPDATE turns SET status='paused' WHERE id='crash-turn'")
            db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)",
                       ('recovered-consumer', task_id, 1, '读取已有作业失败', 'running', job['updated_at']))
        claimed = engine.sessions.claim_job_input(task_id, 'recovered-consumer', 1)
        assert len(claimed) == 1 and claimed[0]['event_id'] == original['event_id']
        assert claimed[0]['status'] == 'failed'
        engine.jobs.acknowledge_terminal(job['job_id'], task_id, 'recovered-consumer')
        assert engine.sessions.claim_job_input(task_id, 'recovered-consumer', 1) == []
        events = engine.store.events(task_id)
        assert len([event for event in events if event['type'] == 'job.terminal']) == 1
        assert len([event for event in events if event['type'] == 'job.delivery_consumed']) == 1
        state = engine.store.task(task_id)
        assert state['model_requests'] == 0 and state['action_count'] == 1
    again = create_app(config, enable_agent=False)
    with TestClient(again, base_url='http://127.0.0.1'):
        with again.state.store.snapshot() as db:
            delivery = dict(db.execute('SELECT * FROM job_deliveries').fetchone())
            assert delivery['state'] == 'consumed' and delivery['event_id'] == original['event_id']
            assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
