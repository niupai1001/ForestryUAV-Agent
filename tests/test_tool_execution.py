"""工具执行通道：结构化事实、障碍识别与调度边界。

这些用例只验证本轮新增的行为：进展判定读已声明维度而不是包装文本；跨工具的
同一个阻碍可被识别；执行许可默认串行、只读有界并行、取消后不再开始。
"""

import asyncio
import json

import pytest
from test_core import task

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.runtime.evidence import facts
from forestry_agent.runtime.projection import model_view, ui_view
from forestry_agent.runtime.scheduling import (EXCLUSIVE, PARALLEL, SERIAL, ToolScheduler)


def test_dimensions_separate_input_range_and_outcome():
    first = {"resource_id": "r1", "version": "v1", "offset": 0, "next": 4000, "text": "same body"}
    wider = {**first, "offset": 4000, "next": 8000}
    other = {**first, "text": "different body"}
    a, b, c = facts("read_file", first), facts("read_file", wider), facts("read_file", other)
    assert a.dimensions == ("input", "range", "outcome")
    # 只移动读取范围：范围变了，输入身份与结果摘要是同一份。
    assert b.changed_from(a) == ("range",)
    assert a.value("input") == b.value("input")
    # 正文变化才算结果变化。
    assert c.changed_from(a) == ("outcome",)


def test_handles_and_timestamps_are_not_dimensions():
    first = {"kind": "completed", "action_id": "a1", "attempt_id": "t1", "created_at": "1",
             "artifact_ids": ["f1"], "job": {"job_id": "j", "status": "running", "updated_at": "1"}}
    later = {**first, "action_id": "a2", "attempt_id": "t2", "created_at": "2",
             "job": {**first["job"], "updated_at": "9"}}
    assert facts("wait_job", first).facets == facts("wait_job", later).facets
    # 作业状态变化是真变化。
    done = {**first, "job": {**first["job"], "status": "succeeded"}}
    assert facts("wait_job", done).changed_from(facts("wait_job", first)) == ("job",)


def test_projection_keeps_model_result_and_limits_ui_metadata():
    result = {"kind": "completed", "action_id": "a1", "job": {"job_id": "j-1", "status": "running"},
              "artifact_ids": ["f1", "f2"], "text": "long source body"}
    assert model_view("read_file", result) is result
    view = ui_view("read_file", result)
    assert view["readable_name"] == "读取文件" and view["artifact_count"] == 2
    assert "text" not in view and "action_id" not in view


def test_repeating_obstacle_is_named_across_different_tools(client, app):
    state, _ = task(client)
    deps = Deps(app.state.engine, state["task_id"], "obstacle-turn", 1)
    progress = app.state.engine.progress
    blocker = {"kind": "failed", "error": {"code": "PRECONDITION_MISSING", "message": "缺少高程单位声明"}}
    progress.record(deps, "one", "inspect_environment", {}, blocker)
    verdict = progress.record(deps, "two", "read_document", {}, blocker)
    assert verdict.new is False and verdict.obstacle_repeats == 2
    with pytest.raises(Failure) as stopped:
        progress.record(deps, "three", "search_knowledge", {}, blocker)
    assert stopped.value.code == "NO_PROGRESS"
    assert "同一阻碍已重复出现" in stopped.value.message and "PRECONDITION_MISSING" in stopped.value.message


def test_identical_request_repeat_is_flagged(client, app):
    state, _ = task(client)
    deps = Deps(app.state.engine, state["task_id"], "repeat-turn", 1)
    progress = app.state.engine.progress
    value = {"text": "fixed body", "resource_id": "r1", "offset": 0, "next": 10}
    progress.record(deps, "one", "read_file", {"resource_id": "r1", "offset": 0}, value)
    verdict = progress.record(deps, "two", "read_file", {"resource_id": "r1", "offset": 0}, value)
    assert verdict.new is False and verdict.repeated_request is True and verdict.stalled == 1
    with progress.store.transaction() as db:
        event = json.loads(db.execute("SELECT payload FROM events WHERE kind='progress.observed' "
            "AND task_id=? ORDER BY seq DESC LIMIT 1", (state["task_id"],)).fetchone()[0])
    assert event["repeated_request"] is True and event["changed_dimensions"] == []


@pytest.mark.asyncio
async def test_scheduler_serializes_by_default_and_bounds_parallel_reads():
    scheduler = ToolScheduler(parallel_reads=2)
    active = {"serial": 0, "parallel": 0, "peak_serial": 0, "peak_parallel": 0}

    async def work(mode):
        async with scheduler.enter(mode, key="task"):
            active[mode] += 1
            active["peak_" + mode] = max(active["peak_" + mode], active[mode])
            await asyncio.sleep(0.02)
            active[mode] -= 1

    await asyncio.gather(*[work(SERIAL) for _ in range(3)])
    assert active["peak_serial"] == 1
    await asyncio.gather(*[work(PARALLEL) for _ in range(5)])
    assert active["peak_parallel"] == 2


@pytest.mark.asyncio
async def test_gates_are_per_activity_not_global():
    scheduler = ToolScheduler(parallel_reads=1)

    async def hold():
        async with scheduler.enter(EXCLUSIVE, key="a"):
            await asyncio.sleep(0.2)

    async def other():
        async with scheduler.enter(PARALLEL, key="b"):
            return "done"

    # 活动 a 的独占调用不阻塞活动 b 的读取。
    assert await asyncio.gather(hold(), other()) == [None, "done"]


@pytest.mark.asyncio
async def test_exclusive_waits_for_readers_and_nested_calls_run_inside():
    scheduler = ToolScheduler(parallel_reads=2)
    order = []

    async def reader():
        async with scheduler.enter(PARALLEL, key="t"):
            order.append("read-start")
            await asyncio.sleep(0.05)
            order.append("read-end")

    async def writer():
        await asyncio.sleep(0.01)
        async with scheduler.enter(EXCLUSIVE, key="t"):
            order.append("write")

    await asyncio.gather(reader(), writer())
    assert order == ["read-start", "read-end", "write"]
    # 已持有许可时，嵌套调用直接放行，不会和父调用争同一把锁。
    async with scheduler.enter(EXCLUSIVE, key="t"):
        async with scheduler.enter(PARALLEL, key="t"):
            assert scheduler.view("t").writer is True


@pytest.mark.asyncio
async def test_cancelled_activity_starts_no_new_call():
    scheduler = ToolScheduler(parallel_reads=1)
    started = []

    async def call():
        async with scheduler.enter(PARALLEL, key="t"):
            started.append(1)

    scheduler.close()
    with pytest.raises(Failure) as stopped:
        await call()
    assert stopped.value.code == "EXECUTION_STOPPED" and started == []
    scheduler.reopen()
    await call()
    assert started == [1]
