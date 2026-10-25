"""长作业的登记、等待与清理顺序。

只验证本轮补上的事实：作业能被称呼和定位、旧记录缺字段标未知、终态通知按事件
身份去重、Worker 自己的日志在删除输出前保存、单次等待到期如实返回仍在运行。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_job_cleanup import CleanupExecutor
from test_jobs import ExecutorFixture, submit

from forestry_agent.jobs import Jobs
from forestry_agent.runtime.job_tools import register_job_tools
from forestry_agent.runtime.tools import ToolRegistry

pytestmark = pytest.mark.offline_web


def test_submission_registers_readable_name_source_message_and_call(client, app):
    state, manager, job_id = submit(client, app, ExecutorFixture())
    job = manager.read(job_id)
    assert job["readable_name"] == "代码作业"
    assert job["request_turn_id"] == "test-turn"
    assert job["call_id"] == "code-call"
    assert job["attempt_id"]
    assert job["logical_activity"] == "test-turn"
    ref = Jobs.job_ref(job)
    assert ref["job_id"] == job_id and ref["status"] == "queued"
    assert "unknown_fields" not in ref


def test_scientific_and_preparation_jobs_have_their_own_readable_names():
    assert Jobs.readable_name("code.execute", "imagery") == "影像处理"
    assert Jobs.readable_name("code.execute", "orthomosaic") == "正射拼接"
    assert Jobs.readable_name("code.execute", "scientific") == "科学计算"
    assert Jobs.readable_name("environment.prepare") == "环境准备"
    assert Jobs.readable_name("environment.prepare", source_inspection="static") == "源码检查"


def test_old_records_report_unknown_fields_instead_of_guessing(client, app):
    _, manager, job_id = submit(client, app, ExecutorFixture())
    job = manager.read(job_id)
    for key in ("readable_name", "request_turn_id", "call_id", "attempt_id", "logical_activity"):
        job.pop(key)
    ref = Jobs.job_ref(job)
    assert ref["readable_name"] is None and ref["request_turn_id"] is None
    assert set(ref["unknown_fields"]) == {"readable_name", "request_turn_id", "call_id",
                                          "attempt_id", "logical_activity"}


def test_terminal_notification_is_deduplicated_by_event_identity(client, app):
    _, manager, job_id = submit(client, app, ExecutorFixture())
    job = manager.read(job_id)
    job["status"] = "succeeded"
    manager.save(job, "job.terminal")
    manager.save(manager.read(job_id), "job.terminal")
    with app.state.store.transaction() as db:
        events = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM events WHERE kind='job.terminal' AND json_extract(payload,'$.job_id')=?",
            (job_id,))]
    assert len(events) == 1 and events[0]["notification_key"] == f"{job_id}:succeeded"


def test_worker_logs_are_saved_before_output_is_removed(client, app):
    fake = CleanupExecutor()
    state, manager, job_id = submit(client, app, fake)
    manager.tick()
    job = manager.read(job_id)
    from pathlib import Path
    output = Path(job["directory"]) / "output"
    (output / "processing.log").write_text("ODM stage: opensfm\nnodes: 1200\n", encoding="utf-8")
    (output / "result.txt").write_text("correct result", encoding="utf-8")
    fake.container["State"].update(Running=False, Status="exited", ExitCode=0)
    manager.tick()
    assert manager.cleanup_terminal(manager.read(job_id)) is True
    assert not output.exists()
    logs = manager.logs(job_id, state["task_id"])
    assert logs["stderr"] == "saved diagnostic" and logs["worker"]["files"][0]["name"] == "processing.log"
    assert "opensfm" in logs["worker"]["files"][0]["text"]
    # 归档文件被改动即不可信，不返回内容。
    path = Path(job["directory"]) / "output-logs.json"
    path.write_text("{}", encoding="utf-8")
    from forestry_agent.contracts import Failure
    with pytest.raises(Failure, match="校验失败"):
        manager.logs(job_id, state["task_id"])


def test_cancelling_the_activity_marks_each_open_job(client, app):
    _, manager, job_id = submit(client, app, ExecutorFixture())
    assert manager.cancel_active(manager.read(job_id)["task_id"])[0]["cancel_requested"] is True
    assert manager.read(job_id)["cancel_requested"] is True
    with app.state.store.transaction() as db:
        kinds = [row[0] for row in db.execute(
            "SELECT kind FROM events WHERE json_extract(payload,'$.job_id')=? ORDER BY seq", (job_id,))]
    assert "job.cancel_requested" in kinds
    # 已有终态的作业不再登记取消意图，重复取消也不重复写事件。
    job = manager.read(job_id)
    job["status"] = "succeeded"
    manager.save(job, "job.terminal")
    assert manager.cancel_active(job["task_id"]) == []


def test_wait_job_returns_running_instead_of_holding_the_turn(client, app):
    _, manager, job_id = submit(client, app, ExecutorFixture())
    registry = ToolRegistry()
    register_job_tools(registry, engine=app.state.engine, jobs=manager,
                       roles=SimpleNamespace(authorize=lambda *a, **k: None), store=app.state.store,
                       journal=lambda ctx, kind, args, operation, suffix=None: {"kind": "completed", **(operation(None) or {})})
    wait_job = next(spec.func for spec in registry.specs() if spec.name == "wait_job")
    deps = SimpleNamespace(task_id=manager.read(job_id)["task_id"], turn_id="wait-turn", revision=1,
                           assignment_id=None)
    result = asyncio.run(wait_job(SimpleNamespace(deps=deps), job_id, 1))
    assert result["kind"] == "running" and result["still_running"] is True
    assert result["job"]["status"] == "queued" and result["waited_seconds"] >= 1
    assert "不要重复提交同一作业" in result["next"]
    assert manager.read(job_id)["status"] == "queued"
