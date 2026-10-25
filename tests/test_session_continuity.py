"""失败、暂停和取消回合的用户指令必须能被后续回合承接。

这里只验证事实来源与承接路径：会话仓库从已提交消息取历史起点，运行核心把
没有进入原生历史的请求拼进本轮开场，并把中断时未返回的工具调用结清。
"""

import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from test_core import task
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.contracts import Failure


def answer(text):
    return ModelResponse(parts=[ToolCallPart("final_result", {"response": text, "artifact_ids": []}, "answer")])


def prompts_of(messages):
    return [part.content for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, UserPromptPart) and isinstance(part.content, str)]


def events(engine, task_id, kind):
    return [event for event in engine.store.events(task_id) if event["type"] == kind]


def prepared_messages(engine, task_id):
    with engine.store.transaction() as db:
        row = db.execute("SELECT payload FROM contexts WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                         (task_id,)).fetchone()
    manifest = json.loads(row["payload"])
    return json.loads(Path(manifest["prepared_input_ref"]).read_bytes())["messages"]


def assert_calls_paired(messages):
    """每条工具结果都必须能对上一个调用 ID，不能出现孤儿结果。"""
    calls, results = set(), set()
    for message in messages:
        for part in message.get("parts", []):
            if part.get("part_kind") == "tool-call":
                calls.add(part.get("tool_call_id"))
            elif part.get("part_kind") == "tool-return":
                results.add(part.get("tool_call_id"))
    assert results <= calls, results - calls


@pytest.mark.asyncio
async def test_follow_up_after_failed_turn_keeps_that_turns_instruction(client, app, monkeypatch):
    state, _ = task(client)
    engine = app.state.engine
    seen = []

    def model(messages, info):
        seen.append(prompts_of(messages))
        if len(seen) == 1:
            raise Failure("FIXTURE_MODEL_DOWN", "夹具模型故障：首次请求失败", 422)
        return answer("已按第二轮要求继续")

    fixture = FixtureFunctionModel(model, stream_function=stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, connection = original(*args, **kwargs)
        agent.model = fixture
        return agent, connection

    monkeypatch.setattr(engine, "make_agent", make_agent)
    first = engine.submit(state["task_id"], "第一轮：先读出全部输入再报告", "turn-fails")
    await engine.queue[state["task_id"]]
    stopped = events(engine, state["task_id"], "run.stopped")
    assert stopped[-1]["payload"]["turn_id"] == "turn-fails"
    assert stopped[-1]["payload"]["blocker"]["code"] == "FIXTURE_MODEL_DOWN"
    assert engine.store.task(state["task_id"])["status"] == "paused"

    second = engine.submit(state["task_id"], "第二轮：改为只报告摘要", "turn-follow-up")
    await engine.queue[state["task_id"]]
    opening = "\n".join(seen[-1])
    # 失败回合的指令不能被跳过，也不能只剩最初目标。
    assert "第二轮：改为只报告摘要" in opening
    assert "第一轮：先读出全部输入再报告" in opening
    assert "turn-fails" in opening and "FIXTURE_MODEL_DOWN" in opening
    note = events(engine, state["task_id"], "session.carried_requests")[-1]["payload"]
    assert note["turn_id"] == second and note["bridged_from_turn_id"] is None
    assert [item["turn_id"] for item in note["carried"]] == [first]
    assert engine.store.task(state["task_id"])["status"] == "waiting_user"
    # 停止原因留在事件里，不随下个回合清空 blockers 而丢失。
    outcome = engine.sessions.turn_outcomes(state["task_id"], [first])[first]
    assert outcome["error"]["code"] == "FIXTURE_MODEL_DOWN"


def test_carried_block_reports_saved_prefix_and_stop_reason(client, app):
    state, _ = task(client)
    engine = app.state.engine
    task_id = state["task_id"]
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)",
                   ("cut-turn", task_id, 1, "先写一段长说明", "paused", "2026-10-22T00:00:00+00:00"))
    engine.store.change(task_id, "fixture.interrupted_prefix", lambda s, _: s["messages"].append({
        "id": "m1", "role": "assistant", "text": "已收到的部分正文", "artifact_ids": [], "limitations": [],
        "thinking": "", "turn_id": "cut-turn", "spec_revision": 1, "at": "2026-10-22T00:00:00+00:00",
        "interrupted": True}))
    with engine.store.transaction() as db:
        engine.store.event(db, task_id, "run.stopped", {"turn_id": "cut-turn", "status": "paused",
            "blocker": {"code": "MODEL_REQUEST_TIMED_OUT", "message": "单次模型请求超过冻结的墙钟时间预算"}})
    carried = engine.sessions.unrepresented_requests(task_id, "next-turn", after_turn_id=None)
    block = engine.carried_block(task_id, {"revision": 1}, carried)
    assert "[会话承接]" in block and "先写一段长说明" in block
    assert "只保存了 8 字可见正文前缀" in block
    assert "MODEL_REQUEST_TIMED_OUT" in block and "不是完整回答" in block


@pytest.mark.asyncio
async def test_completed_turn_is_inherited_without_repeating_its_request(client, app, monkeypatch):
    state, _ = task(client)
    engine = app.state.engine
    seen = []

    def model(messages, info):
        seen.append(prompts_of(messages))
        return answer("第一轮完成")

    fixture = FixtureFunctionModel(model, stream_function=stream_adapter(model))
    original = engine.make_agent

    def make_agent(*args, **kwargs):
        agent, connection = original(*args, **kwargs)
        agent.model = fixture
        return agent, connection

    monkeypatch.setattr(engine, "make_agent", make_agent)
    engine.submit(state["task_id"], "第一轮：读取输入", "turn-one")
    await engine.queue[state["task_id"]]
    engine.submit(state["task_id"], "第二轮：补充要求", "turn-two")
    await engine.queue[state["task_id"]]
    # 已完成的回合进入原生历史，不需要再作为承接文本重复一次。
    assert not any("[会话承接]" in prompt for prompt in seen[-1])
    assert not events(engine, state["task_id"], "session.carried_requests")
    assert_calls_paired(prepared_messages(engine, state["task_id"]))


def test_interrupted_actions_are_settled_instead_of_blocking_delivery(client, app):
    state, _ = task(client)
    engine = app.state.engine
    task_id = state["task_id"]
    with engine.store.transaction() as db:
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)",
                   ("dead-turn", task_id, 1, "上一轮读文件", "paused", "2026-10-22T00:00:00+00:00"))
        db.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)",
                   ("live-turn", task_id, 1, "正在跑的一轮", "running", "2026-10-22T00:00:01+00:00"))
        db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,NULL)",
                   ("dead-action", task_id, "dead-turn", "call-1", 1, "fs.read", '{"relative_path":"a.txt"}', "pending"))
        db.execute("INSERT INTO attempts VALUES(?,?,?,?,NULL)",
                   ("dead-attempt", "dead-action", "in_progress", "2026-10-22T00:00:00+00:00"))
        # 正在运行的回合不替它下结论。
        db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,NULL)",
                   ("live-action", task_id, "live-turn", "call-2", 1, "fs.read", '{"relative_path":"b.txt"}', "pending"))
    settled = engine.sessions.settle_interrupted_actions(task_id, keep_turn_id="current-turn")
    assert settled == [{"action_id": "dead-action", "turn_id": "dead-turn", "tool": "fs.read", "job_id": None}]
    with engine.store.transaction() as db:
        action = db.execute("SELECT status,result FROM actions WHERE id='dead-action'").fetchone()
        attempt = db.execute("SELECT effect_state,finished_at FROM attempts WHERE id='dead-attempt'").fetchone()
        live = db.execute("SELECT status FROM actions WHERE id='live-action'").fetchone()
    assert action["status"] == "failed" and live["status"] == "pending"
    result = json.loads(action["result"])
    assert result["error"]["code"] == "TURN_INTERRUPTED" and "未确认" in result["error"]["message"]
    assert attempt["effect_state"] == "unconfirmed" and attempt["finished_at"]
    interrupted = events(engine, task_id, "action.interrupted")
    assert interrupted[-1]["payload"]["action_id"] == "dead-action"
    # 结清是幂等的：再跑一次不会重复记账，也不会留下 pending 阻塞交付。
    assert engine.sessions.settle_interrupted_actions(task_id, keep_turn_id="current-turn") == []
