"""Request ownership includes stream consumption, but excludes external waits."""
import asyncio
from types import SimpleNamespace

import pytest

from forestry_agent.runtime.model_adapter import LocalModelAdapter


def context(name):
    return SimpleNamespace(deps=SimpleNamespace(scope=SimpleNamespace(task_id=name),
        context_manifest_id=name))


@pytest.mark.asyncio
async def test_request_slot_covers_consumption_and_releases_before_external_wait():
    adapter = LocalModelAdapter(None, lambda *args: None)
    guard = adapter.request_guard(budget_seconds=5)
    consuming, close_stream, external_wait, finish_tool = (asyncio.Event() for _ in range(4))
    calls = []

    async def first_stream(request):
        consuming.set()
        await close_stream.wait()
        calls.append('stream_closed')

    async def first_turn():
        await guard(context('a'), request_context=None, handler=first_stream)
        external_wait.set()
        await finish_tool.wait()

    async def second_stream(request):
        calls.append('second_request')

    first = asyncio.create_task(first_turn())
    second = None
    try:
        await asyncio.wait_for(consuming.wait(), 2)
        second = asyncio.create_task(guard(context('b'), request_context=None, handler=second_stream))
        await asyncio.sleep(0)
        assert not calls and not second.done()
        close_stream.set()
        await asyncio.wait_for(external_wait.wait(), 2)
        await asyncio.wait_for(second, 2)
        assert calls == ['stream_closed', 'second_request'] and not first.done()
    finally:
        close_stream.set()
        finish_tool.set()
        await first
        if second is not None:
            await second


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_active', [True, False])
async def test_cancellation_releases_slot_without_leaking_queued_owner(cancel_active):
    adapter = LocalModelAdapter(None, lambda *args: None)
    guard = adapter.request_guard(budget_seconds=5)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def stream(request):
        entered.set()
        await finish.wait()

    active = asyncio.create_task(guard(context('a'), request_context=None, handler=stream))
    queued = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        queued = asyncio.create_task(guard(context('b'), request_context=None, handler=stream))
        await asyncio.sleep(0)
        target = active if cancel_active else queued
        target.cancel()
        with pytest.raises(asyncio.CancelledError):
            await target
        finish.set()
        await asyncio.gather(active, queued, return_exceptions=True)

        async def next_stream(request):
            return 'available'

        assert await asyncio.wait_for(guard(context('c'), request_context=None,
            handler=next_stream), 2) == 'available'
    finally:
        finish.set()
        for task in (active, queued):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (active, queued) if task is not None),
            return_exceptions=True)
