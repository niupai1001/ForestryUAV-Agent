"""One versioned acceptance after general report fixes, preserving the original task."""
import argparse
import json
from pathlib import Path

import discovery_task_once as trial

ROOT = Path('workspace/report-acceptance-20261026')
PREVIOUS = Path('workspace/discovery-task-20261026')


def prepare():
    ROOT.mkdir(parents=True, exist_ok=False)
    original_bytes = (PREVIOUS / 'protocol.json').read_bytes()
    protocol = json.loads(original_bytes)
    source = trial.source_manifest()
    assert source['sha256'] != protocol['source'], 'unchanged software cannot reopen the failed task'
    protocol.update(source=source['sha256'], modes=['static'],
        script_sha256=trial.digest(Path(trial.__file__).read_bytes()),
        acceptance_script_sha256=trial.digest(Path(__file__).read_bytes()),
        previous_protocol_sha256=trial.digest(original_bytes),
        scope='single new-version report acceptance, original objective and input unchanged; not a discovery comparison',
        trigger='relative output guidance, current-task artifact link validation, and source/parameter/hypothesis guidance changed after original failure',
        total_limits={'model_requests': 12, 'actions': 30, 'seconds': 900, 'scientific_jobs': 0})
    raw = (PREVIOUS / 'inputs/result.json').read_bytes()
    assert trial.digest(raw) == protocol['input_sha256']
    (ROOT / 'inputs').mkdir()
    (ROOT / 'inputs/result.json').write_bytes(raw)
    trial.save(ROOT / 'source.json', source)
    trial.save(ROOT / 'protocol.json', protocol)
    print(json.dumps({'prepared': True, 'source': source['sha256']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'run'])
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare()
    else:
        protocol = json.loads((ROOT / 'protocol.json').read_text(encoding='utf-8'))
        assert protocol['acceptance_script_sha256'] == trial.digest(Path(__file__).read_bytes())
        trial.ROOT = ROOT
        trial.run('static')
