"""One real offline Job: completed output, later logging failure, read/reuse only.

Operator fixture, no model or host execution of the submitted code. An existing
directory is never reused to reset evidence or silently dispatch the same Job.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.jobs import TERMINAL
from forestry_agent.provenance import runtime_manifest
from forestry_agent.store import now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    config = load_config(args.config).model_copy(update={'workspace_root': directory / 'runtime'})
    record = {'started_at': now(), 'trigger': 'New failed-Job output collection and bounded log-tail behavior',
              'scope': 'One operator-controlled real container; not a native model workflow or scientific validation',
              'runtime': runtime_manifest(config), 'model_invoked': False}
    target = directory / 'evidence.json'
    def save():
        temporary = target.with_suffix('.next.json')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)
    save()
    app = create_app(config, enable_agent=False)
    engine = app.state.engine
    manager = engine.jobs
    with TestClient(app, base_url='http://127.0.0.1', headers={'origin': 'http://127.0.0.1'}) as client:
        client.get('/session').raise_for_status()
        response = client.post('/tasks', json={'request_id': 'partial-output-task',
            'objective': '诊断计算后的日志错误，读回并复用已有结果交付报告，不重复计算。',
            'harness_mode': 'H0', 'constraints': {'allowed_capabilities': [
                'code.execute', 'job.observe', 'artifact.read', 'fs.write_workspace']}})
        record['task_create_response'] = {'status': response.status_code, 'body': response.json()}
        save()
        response.raise_for_status()
        tid = response.json()['task_id']
        deps = Deps(engine, tid, 'partial-output-operator-turn', 1)
        code = '''import json, sys
from pathlib import Path
values = [2.0, 4.0]
result = {"count": len(values), "sum": sum(values)}
Path('/output/result.json').write_text(json.dumps(result), encoding='utf-8')
Path('/output/calculation.py').write_text('values = [2.0, 4.0]\\nresult = {"count": len(values), "sum": sum(values)}\\n', encoding='utf-8')
print('earlier output ' * 6000, flush=True)
print('CALCULATION_COMPLETED_ONCE', flush=True)
sys.stderr.write('long wrapper frame ' * 500 + '\\n')
raise NameError("wrapper logging after calculation")
'''
        submitted = engine.perform(deps, 'one-code-job', 'code.execute', {'code': code, 'input_refs': []},
            lambda action: manager.submit(tid, 1, action, code, [], 60))
        assert submitted['kind'] == 'completed', submitted
        jid = submitted['job']['job_id']
        record.update(task_id=tid, job_id=jid, submitted=submitted)
        save()
        deadline = time.monotonic() + 120
        while True:
            manager.tick()
            job = manager.read(jid, tid)
            record['job'] = manager.public(job)
            save()
            if job['status'] in TERMINAL:
                break
            if time.monotonic() >= deadline:
                record['observation_expired_live_job_preserved'] = True
                save()
                raise RuntimeError('Inspect recorded same Job/container; do not redispatch')
            time.sleep(0.5)
        observed = engine.perform(deps, 'observe-existing-job', 'job.observe', {'job_id': jid},
            lambda _: manager.observation(jid, tid))
        assert observed['kind'] == 'completed', observed
        record['observation'] = observed
        outputs = {engine.resources.artifact(ref, tid)['name']: engine.resources.artifact(ref, tid)
                   for ref in job['artifact_ids']}
        result_artifact = outputs['result.json']
        readback = engine.perform(deps, 'read-existing-result', 'artifact.read',
            {'artifact_id': result_artifact['artifact_id'], 'offset': 0},
            lambda _: engine.resources.read_artifact(tid, result_artifact['artifact_id']))
        actual = json.loads(readback['text'])
        assert actual == {'count': 2, 'sum': 6.0}
        report_text = (f'# 已有结果复用\n\n作业 {jid} 仍为 failed，exit_code=1。\n'
                       f'结果 {result_artifact["artifact_id"]} / SHA256 {result_artifact["sha256"]} '
                       f'已读回：count={actual["count"]}，sum={actual["sum"]}。\n'
                       '计算后包装日志 NameError，保留失败；报告复用已有结果，没有新计算作业。\n'
                       '操作者软件夹具，不是原生模型自主完成或科学精度证明。\n')
        report = engine.perform(deps, 'write-reuse-report', 'fs.write_workspace',
            {'relative_path': 'reuse-report.md', 'content': report_text},
            lambda action: {k: v for k, v in engine.resources.write(tid, action, 'reuse-report.md', report_text).items()
                            if k != 'locator'})
        report_read = engine.perform(deps, 'read-reuse-report', 'artifact.read',
            {'artifact_id': report['artifact_id'], 'offset': 0},
            lambda _: engine.resources.read_artifact(tid, report['artifact_id']))
        container = manager.docker.inspect(job['container_name'])
        with engine.store.transaction() as db:
            count = db.execute('SELECT COUNT(*) FROM jobs WHERE task_id=?', (tid,)).fetchone()[0]
        checks = {
            'one_job_no_calculation_replay': count == 1 and container['Id'] == job['container_id'],
            'actual_failed_exit_and_frozen_outputs': job['status'] == 'failed' and job['exit_code'] == 1 and job['partial_outputs'] and len(job['artifact_ids']) == 2,
            'result_readback_matches': actual == {'count': 2, 'sum': 6.0},
            'exception_tail_visible_and_marked': 'NameError: wrapper logging after calculation' in observed['logs']['stderr'] and observed['logs']['stderr_truncated'] and observed['logs']['window'] == 'tail',
            'stdout_tail_keeps_completion_once': observed['logs']['text'].count('CALCULATION_COMPLETED_ONCE') == 1 and observed['logs']['stdout_truncated'],
            'report_reuses_actual_result_and_discloses_failure': report_read['text'] == report_text and not report_read['has_more'],
            'same_existing_restricted_worker': not container['State']['Running'] and container['Config']['User'] == '65534:65534' and container['HostConfig']['NetworkMode'] == 'none' and container['HostConfig']['ReadonlyRootfs'],
            'original_code_identity': hashlib.sha256(code.encode()).hexdigest() == job['code_sha256'],
            'model_requests_zero': engine.store.task(tid)['model_requests'] == 0,
        }
        record.update(checks=checks, container=container, readback=readback, report=report, report_readback=report_read,
                      finished_at=now())
        save()
        print(json.dumps({'evidence': str(target), 'checks_passed': all(checks.values()),
                          'job_id': jid, 'status': job['status'], 'no_model_run': True}, ensure_ascii=False), flush=True)
        assert all(checks.values()), checks


if __name__ == '__main__':
    main()
