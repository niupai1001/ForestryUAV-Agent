"""One isolated real Docker restart/cancel seam; no model or scientific computation."""
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.jobs import Docker, TERMINAL
from forestry_agent.store import now, uid

ROOT = Path('workspace/executor-seam-20261026')


def main():
    ROOT.mkdir(exist_ok=False)
    config = load_config('config/app.local.yaml').model_copy(deep=True)
    config.workspace_root = ROOT.resolve() / 'runtime'
    config.max_actions_per_task = 4
    report = {'scope': 'real executor seam, not autonomous Agent or scientific validation',
              'models': 0, 'scientific_jobs': 0, 'jobs': [], 'completed': False}
    def save(name, value):
        with (ROOT / name).open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
    def client(app):
        return TestClient(app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'})
    def post(browser, path, payload):
        response = browser.post(path, json=payload)
        response.raise_for_status()
        return response.json()
    def submit(app, task_id, code, label):
        engine = app.state.engine
        turn_id = uid()
        with engine.store.transaction() as db:
            db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)', (turn_id, task_id, 1, label, 'completed', now()))
        result = engine.perform(Deps(engine, task_id, turn_id, 1), label, 'code.execute', {'code': code},
            lambda action: engine.jobs.submit(task_id, 1, action, code, [], 120))
        assert result['kind'] == 'completed', result
        job_id = result['job']['job_id']
        report['jobs'].append(job_id)
        save(label + '-submission.json', result)
        engine.jobs.tick()
        observed = engine.jobs.read(job_id)
        save(label + '-started.json', observed)
        assert observed['status'] == 'running', observed.get('blocker')
        assert engine.jobs.docker.inspect(observed['container_name'])['State']['Running']
        return job_id
    def await_terminal(manager, job_id):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            manager.tick()
            job = manager.read(job_id)
            if job['status'] in TERMINAL:
                return job
            time.sleep(1)
        raise RuntimeError('owned Job did not settle within seam observation budget: ' + job_id)
    try:
        docker = Docker(config)
        report['version'] = docker.call('version', '--format', '{{.Server.Version}}')
        save('executor-version.json', report['version'])
        assert report['version']['code'] == 0, 'executor unavailable; do not install or restart it'
        report['image'] = docker.call('image', 'inspect', config.code_image, '--format', '{{.Id}}')
        save('executor-image.json', report['image'])
        assert report['image']['code'] == 0, 'pinned image absent; no pull in this trial'
        save('config.json', config.model_dump(mode='json'))
        first = create_app(config, enable_agent=False)
        with client(first) as browser:
            browser.get('/session')
            task = post(browser, '/tasks', {'request_id': 'executor-seam',
                'objective': '验证后台作业跨应用重建保留唯一计算，另核对取消与归档', 'harness_mode': 'H0'})
            task_id = task['task_id']
            report['task_id'] = task_id
            job_id = submit(first, task_id,
                "import time; print('restart seam started',flush=True); time.sleep(20); "
                "open('/output/resume-proof.txt','w').write('same Job survived application restart')", 'restart-seam')
        # Only the application owner is closed; its labelled Docker Job survives.
        second = create_app(config, enable_agent=False)
        with client(second) as browser:
            browser.get('/session')
            manager = second.state.engine.jobs
            restored = manager.read(job_id)
            save('restarted-job.json', restored)
            assert restored['job_id'] == job_id
            completed = await_terminal(manager, job_id)
            save('restart-terminal.json', completed)
            assert completed['status'] == 'succeeded' and len(completed['artifact_ids']) == 1
            artifact = second.state.resources.artifact(completed['artifact_ids'][0], task_id)
            assert Path(artifact['locator']).read_text() == 'same Job survived application restart'
            current = second.state.store.task(task_id)
            post(browser, f'/tasks/{task_id}/resume', {'request_id': 'explicit-seam-next-step',
                                                    'expected_state_version': current['state_version']})
            cancelled_id = submit(second, task_id,
                "import time; print('cancel seam started',flush=True); time.sleep(60); "
                "open('/output/must-not-complete.txt','w').write('unexpected completion')", 'cancel-seam')
            intent = post(browser, f'/tasks/{task_id}/jobs/{cancelled_id}/cancel', {'request_id': 'cancel-owned-seam'})
            save('cancel-intent.json', intent)
            assert intent['cancel_requested'] and intent['status'] == 'running'
            cancelled = await_terminal(manager, cancelled_id)
            save('cancel-terminal.json', cancelled)
            assert cancelled['status'] == 'cancelled' and not cancelled['artifact_ids']
            for ref in report['jobs']:
                job = manager.read(ref)
                manager.cleanup_terminal(job)
                save(ref + '-cleanup.json', manager.read(ref))
                assert manager.docker.inspect(job['container_name']) is None
            with second.state.store.snapshot() as db:
                assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 2
            report['artifacts'] = [artifact]
            report['state'] = second.state.store.task(task_id)
            assert report['state']['model_requests'] == 0
            report['completed'] = True
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        save('report.json', report)
        print(json.dumps({'completed': report['completed'], 'jobs': report['jobs'],
                          'error': report.get('error')}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
