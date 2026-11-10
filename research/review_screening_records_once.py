"""Reclassify saved screening evidence without a service, model or recomputation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from research_run import screening_score  # noqa: E402


def review(suite_path, records):
    suite_bytes = suite_path.read_bytes()
    suite = json.loads(suite_bytes)
    rows = []
    for spec in suite['tasks']:
        path = records/f"task-{spec['id']}.json"
        if not path.exists():
            rows.append({'task':spec['id'],'observation':'not_run'})
            continue
        raw = path.read_bytes()
        record = json.loads(raw)
        assert record['task'] == spec['id'] and record['suite_version'] == suite['suite_version']
        rows.append({'task':spec['id'],'task_id':record['task_id'],
            'source_record_sha256':hashlib.sha256(raw).hexdigest(), 'source_verdict':record.get('verdict'),
            'frozen':record['frozen'], 'wall_seconds':record.get('metrics',{}).get('wall_seconds'),
            'assessment':screening_score(spec,record)})
        assert path.read_bytes() == raw, 'Original record must remain unchanged'
    return {'suite_sha256':hashlib.sha256(suite_bytes).hexdigest(), 'suite_version':suite['suite_version'],
        'scorer_sha256':hashlib.sha256((ROOT/'scripts/research_run.py').read_bytes()).hexdigest(),
        'rows':rows, 'automatic_passed':sum(r.get('assessment',{}).get('automatic_checks_passed',False) for r in rows),
        'full_task_passed':sum(r.get('assessment',{}).get('full_task_passed',False) for r in rows),
        'policy':'metadata-only reclassification; saved checks not re-executed; originals unchanged; no model or scientific jobs',
        'limitations':['No independent semantic or field-accuracy review is implied.',
                       'Blocker codes describe observed failure layers, not proven model capability causes.',
                       'Thinking/context/runtime variants are not a controlled comparison group.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Review already exists; inspect saved evidence instead of rewriting it')
    report = review(ROOT/'research/tasks.research-v1.json',ROOT/'workspace/research-20261021/records')
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'automatic_passed':report['automatic_passed'],'full_task_passed':report['full_task_passed'],
        'records':len(report['rows']),'output':str(args.output)}))
