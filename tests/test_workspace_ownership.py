import asyncio
import subprocess
import sys
import threading

import psutil
import pytest
from test_core import task

from forestry_agent.app import create_app
from forestry_agent.contracts import Failure
from forestry_agent.runtime.ownership import workspace_writer


def test_process_lock_survives_stale_file_and_releases_on_actual_process_death(tmp_path):
    code = """
import sys
import os
from pathlib import Path
from forestry_agent.runtime.ownership import workspace_writer
with workspace_writer(Path(sys.argv[1])):
    print('owned', os.getpid(), flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen([sys.executable, '-u', '-c', code, str(tmp_path)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True) as child:
        owner = None
        try:
            message, pid = child.stdout.readline().split()
            assert message == 'owned'
            owner = psutil.Process(int(pid))
            assert owner.pid == child.pid or owner.ppid() == child.pid
            with pytest.raises(Failure, match='同一工作区') as error:
                with workspace_writer(tmp_path):
                    pytest.fail('live process lost exclusive ownership')
            assert error.value.code == 'WORKSPACE_IN_USE'
            with workspace_writer(tmp_path / 'independent'):
                pass
        finally:
            # Windows venv python.exe may be a redirector, not the lock owner.
            if owner is not None:
                owner.kill()
                owner.wait(timeout=10)
            else:
                child.kill()
            child.wait(timeout=10)
    assert (tmp_path / '.forestry-service.lock').exists()
    with workspace_writer(tmp_path):
        pass


def test_second_app_cannot_construct_or_reconcile_live_workspace(client, app):
    state, _ = task(client)
    before = app.state.store.task(state['task_id'])
    with pytest.raises(Failure) as error:
        create_app(app.state.engine.config, enable_agent=False)
    assert error.value.code == 'WORKSPACE_IN_USE'
    assert app.state.store.task(state['task_id']) == before


@pytest.mark.asyncio
async def test_preconstructed_app_cannot_enter_lifespan_of_live_owner(app, monkeypatch):
    other = create_app(app.state.engine.config, enable_agent=False)
    calls = []
    monkeypatch.setattr(other.state.sessions, 'recover', lambda: calls.append('recovered'))
    async with app.router.lifespan_context(app):
        with pytest.raises(Failure) as error:
            async with other.router.lifespan_context(other):
                pytest.fail('second owner entered')
        assert error.value.code == 'WORKSPACE_IN_USE' and calls == []
    async with other.router.lifespan_context(other):
        assert calls == ['recovered']


@pytest.mark.asyncio
async def test_startup_failure_releases_owner_after_cleanup(app, monkeypatch):
    def fail():
        raise RuntimeError('recovery failed')
    monkeypatch.setattr(app.state.sessions, 'recover', fail)
    with pytest.raises(RuntimeError, match='recovery failed'):
        async with app.router.lifespan_context(app):
            pytest.fail('failed startup yielded')
    assert app.state.engine.closing
    with workspace_writer(app.state.engine.config.workspace_root):
        pass


@pytest.mark.asyncio
async def test_shutdown_keeps_owner_until_job_thread_commits(app, monkeypatch):
    engine = app.state.engine
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()
    root = engine.config.workspace_root

    def tick():
        entered.set()
        if not release.wait(8):
            raise RuntimeError('test failed to release tick')
        with engine.store.transaction() as db:
            engine.store.user_event(db, 'test.tick_committed', {})
        committed.set()

    monkeypatch.setattr(engine.jobs, 'tick', tick)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    engine.jobs.start()
    close = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        close = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0.05)
        close.cancel()
        await asyncio.sleep(0.05)
        assert not close.done() and not committed.is_set()
        with pytest.raises(Failure):
            with workspace_writer(root):
                pytest.fail('new owner overlapped old tick')
    finally:
        release.set()
        if close is None:
            await lifespan.__aexit__(None, None, None)
        else:
            await asyncio.gather(close, return_exceptions=True)
    assert committed.is_set() and engine.jobs.worker.done()
    with workspace_writer(root):
        with engine.store.snapshot() as db:
            assert db.execute("SELECT COUNT(*) FROM user_events WHERE kind='test.tick_committed'").fetchone()[0] == 1
