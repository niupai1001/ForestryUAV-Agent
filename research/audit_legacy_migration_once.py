"""Read a frozen real v4 backup, migrate only copies, and inspect all native runs."""
import asyncio
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic_ai_harness.step_persistence import SqliteStepStore

from forestry_agent.agent import Engine
from forestry_agent.config import load_config
from forestry_agent.contracts import Failure
from forestry_agent.jobs import Jobs
from forestry_agent.resources import Resources
from forestry_agent.store import Store, encode

SOURCE = Path('workspace/conversation-upgrade-backup-20261014-imagery-prosail-20261021')
OUTPUT = Path('workspace/legacy-migration-audit-20261026')


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def logical_rows(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        result = {}
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_versions' ORDER BY name"):
            quoted = '"' + table.replace('"', '""') + '"'
            count = len(db.execute('SELECT * FROM ' + quoted + ' LIMIT 0').description)
            hashed, rows = hashlib.sha256(), 0
            for row in db.execute('SELECT * FROM ' + quoted + ' ORDER BY ' + ','.join(str(i+1) for i in range(count))):
                values = [{'blob_sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)}
                          if isinstance(value, bytes) else value for value in row]
                hashed.update((encode(values) + '\n').encode())
                rows += 1
            result[table] = {'rows': rows, 'sha256': hashed.hexdigest()}
        return result


async def main():
    OUTPUT.mkdir(exist_ok=False)
    root = OUTPUT.resolve() / 'runtime'
    metadata = root / '.forestry'
    metadata.mkdir(parents=True)
    report = {'scope': 'real backup copy migration/read compatibility, no source mutation or execution',
              'source': str(SOURCE), 'models': 0, 'jobs_dispatched': 0, 'tasks': [], 'runs': [], 'complete': False}
    try:
        identities = {}
        originals = {}
        for name in ['state.sqlite', 'model-steps.sqlite']:
            source = SOURCE / name
            assert not any((SOURCE / (name + suffix)).exists() and (SOURCE / (name + suffix)).stat().st_size
                           for suffix in ['-wal', '-journal']), 'nonempty WAL needs a coherent snapshot, never ignore it'
            identities[name] = sha(source)
            originals[name] = logical_rows(source)
            shutil.copy2(source, metadata / name)
        report['source_sha256'] = identities
        state_path = metadata / 'state.sqlite'
        store = Store(state_path)
        once = logical_rows(state_path)
        for table, expected in originals['state.sqlite'].items():
            assert once[table] == expected, 'legacy rows changed: ' + table
        Store(state_path)
        assert logical_rows(state_path) == once, 'repeat initialization changed logical rows'
        with store.snapshot() as db:
            raw_tasks = [json.loads(row[0]) for row in db.execute('SELECT state FROM tasks')]
            deleted = {row[0] for row in db.execute('SELECT task_id FROM session_deletions')}
            report['schema_versions'] = [row[0] for row in db.execute('SELECT version FROM schema_versions ORDER BY version')]
            report['foreign_key_violations'] = [tuple(row) for row in db.execute('PRAGMA foreign_key_check')]
        config = load_config('config/app.local.yaml').model_copy(update={'workspace_root': root})
        manager = Jobs(config, store, Resources(store, root))
        for state in raw_tasks:
            try:
                assert store.task(state['task_id']) == state
                listing = manager.list(state['task_id'])
                report['tasks'].append({'task_id': state['task_id'], 'status': state['status'],
                                        'visible_jobs': len(listing['jobs']), 'messages': len(state['messages'])})
            except Failure as error:
                if state['task_id'] not in deleted or error.code != 'SESSION_DELETING':
                    raise
                report['tasks'].append({'task_id': state['task_id'], 'status': 'deletion_marker_preserved'})
        native = SqliteStepStore(database=metadata / 'model-steps.sqlite')
        runs = await native.list_runs()
        for run in runs:
            snapshot = await native.latest_snapshot(run_id=run.run_id, include_interrupted=True)
            report['runs'].append({'run_id': run.run_id, 'latest_snapshot': snapshot is not None,
                'state': snapshot.state if snapshot else None, 'messages': len(snapshot.messages) if snapshot else 0,
                'unresolved_calls': len(Engine.open_tool_calls(snapshot.messages)) if snapshot else None})
        after_steps = logical_rows(metadata / 'model-steps.sqlite')
        for table, expected in originals['model-steps.sqlite'].items():
            assert after_steps[table] == expected, 'native historical rows changed: ' + table
        assert {name: sha(SOURCE / name) for name in identities} == identities
        report['legacy_tables'] = originals
        report['source_unchanged'] = True
        report['artifact_locators_rebased'] = False
        report['complete'] = True
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)[:2000]}
        raise
    finally:
        with (OUTPUT / 'report.json').open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(json.dumps({'complete': report['complete'], 'tasks': len(report['tasks']),
                          'runs': len(report['runs']), 'error': report.get('error')}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
