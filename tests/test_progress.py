import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturn, ToolReturnPart
from pydantic_ai_harness.tool_output_limits import LocalFileStore
from test_core import task
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps
from forestry_agent.contracts import Failure
from forestry_agent.progress import Progress, observation
from forestry_agent.store import Store, encode


def recorded(progress, deps):
    with progress.store.transaction() as db:
        return [dict(row) for row in db.execute("SELECT * FROM progress_observations WHERE task_id=? AND scope=? AND turn_id=? ORDER BY rowid",
            (deps.task_id, progress.scope(deps), deps.turn_id))]


def save_prepared_context(engine, deps, context_id, returns, *, previous=None):
    messages = [{'kind': 'request', 'parts': [{'part_kind': 'tool-return', 'tool_name': 'read_file',
                  'content': value}]} for value in returns]
    data = encode({'messages': messages}).encode()
    path = engine.config.workspace_root/'.forestry/model-inputs'/deps.task_id/(deps.assignment_id or 'coordinator')/f'{context_id}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    refs = [hashlib.sha256(encode(message).encode()).hexdigest() for message in messages]
    manifest = {'context_manifest_id': context_id, 'task_id': deps.task_id, 'turn_id': deps.turn_id,
                'spec_revision': deps.revision, 'assignment_id': deps.assignment_id,
                'prepared_input_ref': str(path), 'prepared_input_sha256': hashlib.sha256(data).hexdigest(),
                'ordered_message_refs': refs, 'previous_context_ref': previous['context_manifest_id'] if previous else None,
                'prior_message_refs_absent': [ref for ref in previous['ordered_message_refs'] if ref not in refs] if previous else []}
    with engine.store.transaction() as db:
        db.execute('INSERT INTO contexts VALUES(?,?,?,?)', (context_id, deps.task_id, deps.turn_id, encode(manifest)))
    return manifest


def test_compaction_recovery_does_not_reset_stall_count_and_cannot_be_repeated_after_reopen(client, app):
    state, _ = task(client)
    engine = app.state.engine
    deps = Deps(engine, state['task_id'], 'recover-sources', 1)
    sources = [{'text': text, 'resource_id': f'source-{i}', 'version': 'immutable', 'offset': 0, 'next': len(text)}
               for i, text in enumerate(['instructions', 'transactions', 'original program'])]
    for i, value in enumerate(sources):
        engine.progress.record(deps, f'first-{i}', 'read_file', {}, value)
    engine.progress.record(deps, 'bookkeeping', 'write_plan', {}, {'kind': 'completed'})
    prior = save_prepared_context(engine, deps, 'before-drop', sources)
    save_prepared_context(engine, deps, 'after-drop', [], previous=prior)
    deps.context_manifest_id = 'after-drop'
    for i, value in enumerate(sources):
        engine.progress.record(deps, f'recover-{i}', 'read_file', {}, value)
    assert [r['stalled'] for r in recorded(engine.progress, deps)] == [0, 0, 0, 1, 1, 1, 1]
    with engine.store.transaction() as db:
        events = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE kind='progress.observed' AND task_id=?", (deps.task_id,))]
    assert all(e['context_recovered'] and not e['new_observation'] for e in events[-3:])
    assert all(e['context_loss_id'] == 'after-drop' and e['limit'] == 3 for e in events[-3:])
    reopened = Progress(Store(engine.store.path), engine.config)
    reopened.record(deps, 'recover-0', 'read_file', {}, sources[0])  # Exact pending-call replay, not another restoration.
    assert len(recorded(reopened, deps)) == 7
    reopened.record(deps, 'repeat-restoration', 'read_file', {}, sources[0])
    with pytest.raises(Failure, match='上限3'):
        reopened.record(deps, 'repeat-again', 'read_file', {}, sources[1])
    assert [r['stalled'] for r in recorded(reopened, deps)][-2:] == [2, 3]


@pytest.mark.parametrize('invalid', ['still_present', 'changed_snapshot', 'wrong_refs', 'no_loss_lineage',
                                    'other_turn', 'other_role', 'other_revision', 'outside_snapshot_root'])
def test_recovery_requires_actual_bound_context_loss_in_same_scope(client, app, invalid):
    state, _ = task(client)
    engine = app.state.engine
    deps = Deps(engine, state['task_id'], 'scoped-recovery', 1)
    value = {'text': 'original immutable source', 'resource_id': 'source', 'version': 'v1'}
    engine.progress.record(deps, 'first', 'read_file', {}, value)
    prior = save_prepared_context(engine, deps, 'source-present', [value])
    current = save_prepared_context(engine, deps, 'source-lost', [value] if invalid == 'still_present' else [], previous=prior)
    if invalid == 'changed_snapshot':
        Path(prior['prepared_input_ref']).write_bytes(b'{"messages":[]}')
    elif invalid == 'wrong_refs':
        prior['ordered_message_refs'] = []
    elif invalid == 'no_loss_lineage':
        current['prior_message_refs_absent'] = []
    elif invalid == 'other_turn':
        prior['turn_id'] = 'another-turn'
    elif invalid == 'other_role':
        prior['assignment_id'] = 'another-role'
    elif invalid == 'other_revision':
        prior['spec_revision'] = 2
    elif invalid == 'outside_snapshot_root':
        outside = engine.config.workspace_root/'outside-prepared.json'
        outside.write_bytes(Path(prior['prepared_input_ref']).read_bytes())
        prior['prepared_input_ref'] = str(outside)
    with engine.store.transaction() as db:
        for manifest in [prior, current]:
            db.execute('UPDATE contexts SET payload=? WHERE id=?', (encode(manifest), manifest['context_manifest_id']))
    deps.context_manifest_id = 'source-lost'
    engine.progress.record(deps, 'repeat', 'read_file', {}, value)
    assert recorded(engine.progress, deps)[-1]['stalled'] == 1
    with engine.store.transaction() as db:
        event = json.loads(db.execute("SELECT payload FROM events WHERE kind='progress.observed' AND task_id=? ORDER BY seq DESC LIMIT 1", (deps.task_id,)).fetchone()[0])
    assert not event.get('context_recovered')


@pytest.mark.asyncio
async def test_native_compaction_can_restore_three_sources_without_premature_pause(client, app, tmp_path):
    refs = []
    originals = {}
    for i in range(3):
        path = tmp_path/f'input-{i}.txt'
        path.write_text(f'Immutable source fact {i}', encoding='utf-8')
        originals[path] = path.read_bytes()
        refs.append(client.post('/resources/register', json={
            'request_id': f'input-{i}', 'path': str(path), 'classification': 'private', 'read_only': True}).json()['resource_id'])
    state, _ = task(client, input_refs=refs, harness_mode='H1')
    engine = app.state.engine
    agent, connection = engine.make_agent('small', 'native-read-recovery', task_id=state['task_id'])
    count = 0

    @agent.tool_plain
    def checkpoint_fixture(index: int) -> dict:
        # Enough actual history to exceed native keep_tokens; tiny histories
        # correctly survive the message-count trigger without any data loss.
        return {'checkpoint': index, 'text': 'x'*3000}

    def model(messages, info):
        nonlocal count
        count += 1
        if count <= 3:
            return ModelResponse(parts=[ToolCallPart('read_file', {'resource_id': refs[count-1]}, f'first-{count}')])
        if count <= 9:
            return ModelResponse(parts=[ToolCallPart('checkpoint_fixture', {'index': count}, f'checkpoint-{count}')])
        if count == 10:
            return ModelResponse(parts=[ToolCallPart('read_file', {'resource_id': ref}, f'restore-{i}') for i, ref in enumerate(refs)])
        return ModelResponse(parts=[ToolCallPart('final_result', {'summary': 'Source windows restored for protocol fixture.',
                                  'artifact_ids': [], 'limitations': ['Not an application acceptance trial.']}, 'finish')])

    try:
        with agent.override(model=FixtureFunctionModel(model)):
            result = await agent.run('协议夹具：保留来源和真实压缩恢复行为。',
                                     deps=Deps(engine, state['task_id'], 'native-read-recovery', 1))
        assert result.output.summary == 'Source windows restored for protocol fixture.' and count == 11
        with engine.store.transaction() as db:
            events = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE kind='progress.observed' AND task_id=?", (state['task_id'],))]
        recovered = [e for e in events if e.get('context_recovered')]
        assert len(recovered) == 3 and all(e['stalled'] == 0 and e['limit'] == 3 and not e['new_observation'] for e in recovered)
        assert all(path.read_bytes() == data for path, data in originals.items())
    finally:
        await connection.aclose()


def test_repeated_and_cyclic_observations_survive_reopening_and_frozen_config(client, app):
    state, _ = task(client)
    engine = app.state.engine
    deps = Deps(engine, state["task_id"], "durable-progress", 1)
    assert state["config_snapshot"]["generation"]["max_unchanged_decisions"] == 3
    for index, text in enumerate(["fact-A", "fact-B", "fact-A", "fact-B"]):
        engine.progress.record(deps, f"call-{index}", "read_file", {"offset": index % 2}, {"text": text})
    reopened = Progress(Store(engine.store.path), engine.config.model_copy(update={"max_unchanged_decisions": 99}))
    with pytest.raises(Failure, match="上限3") as error:
        reopened.record(deps, "call-4", "read_file", {"offset": 0}, {"text": "fact-A"})
    assert error.value.code == "NO_PROGRESS"
    assert [r["stalled"] for r in recorded(reopened, deps)] == [0, 0, 1, 2, 3]
    with reopened.store.transaction() as db, pytest.raises(Failure, match="不会重置"):
        reopened.check(db, deps)


def test_failure_variations_and_empty_eof_are_not_new_progress_and_replay_is_unique(client, app):
    state, _ = task(client)
    deps = Deps(app.state.engine, state["task_id"], "failed-repair", 1)
    progress = app.state.engine.progress
    failed = {"kind": "failed", "error": {"code": "EXECUTION_FAILED"}}
    progress.record(deps, "one", "code_execute", {"code": "bad one"}, failed)
    progress.record(deps, "one", "code_execute", {"code": "bad one"}, failed)
    with pytest.raises(Failure, match="载荷变化"):
        progress.record(deps, "one", "code_execute", {"code": "changed"}, failed)
    progress.record(deps, "two", "code_execute", {"code": "bad two"}, {"kind": "failed", "error": {"code": "SCHEMA_INVALID"}})
    with pytest.raises(Failure, match="连续3"):
        progress.record(deps, "three", "read_tool_result_window", {"offset": 9000}, {"text": "", "next": 9000, "has_more": False})
    assert len(recorded(progress, deps)) == 3


def test_pagination_new_job_state_and_scope_isolation_do_not_stop_legitimate_work(client, app):
    state, _ = task(client)
    progress = app.state.engine.progress
    root = Deps(app.state.engine, state["task_id"], "root-progress", 1)
    for index in range(12):
        # Identical text in a different byte window is still a new observation.
        progress.record(root, f"page-{index}", "read_artifact", {"offset": index * 4000},
            {"text": "repeating source", "offset": index * 4000, "next": (index + 1) * 4000})
    for index, status in enumerate(["queued", "running", "succeeded"]):
        progress.record(root, f"job-{index}", "wait_job", {"job_id": "job"},
            {"job": {"job_id": "job", "status": status, "updated_at": str(index)}})
    child = replace(root, assignment_id="isolated-child", turn_id="child-turn")
    for index in range(3):
        try:
            progress.record(child, str(index), "fixture", {}, {"kind": "failed"})
        except Failure as error:
            assert index == 2 and error.code == "NO_PROGRESS"
    with progress.store.transaction() as db:
        progress.check(db, root)
        progress.check(db, replace(child, assignment_id="another-child"))
        progress.check(db, replace(child, revision=2))
    assert all(r["stalled"] == 0 for r in recorded(progress, root))


def test_envelope_changes_do_not_hide_repetition_but_source_fields_remain_meaningful():
    citation = {"url": "https://example.org/", "source_sha256": "raw", "snapshot_artifact_id": "old", "observed_at": "old"}
    first = {"action_id": "old", "kind": "completed", "text": '{"created_at":"real user fact"}', "citation": citation, "text_artifact_id": "old"}
    second = {**first, "action_id": "new", "citation": {**citation, "snapshot_artifact_id": "new", "observed_at": "new"}, "text_artifact_id": "new"}
    assert observation("read_web_page", first) == observation("read_web_page", second)
    assert observation("read_web_page", first) != observation("read_web_page", {**first, "text": '{"created_at":"changed real user fact"}'})
    leaf = {"tool": "web.read", "result": first}
    nested = {"tool": "action.inspect", "result": {"tool": "action.inspect", "result": leaf}}
    assert observation("inspect_action", leaf) == observation("inspect_action", nested)
    assert observation("read_tool_result", ToolReturn("same", content="new fact")) != observation("read_tool_result", ToolReturn("same", content="different fact"))


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_call_id", [False, True])
async def test_native_loop_stops_after_confirmed_repeated_writes_and_restart_has_no_extra_request(client, app, monkeypatch, reuse_call_id):
    state, _ = task(client)
    engine = app.state.engine
    requests = 0
    def model(messages, info):
        nonlocal requests
        requests += 1
        if "不要重复写" in (info.instructions or ""):
            saved = engine.store.task(state["task_id"])["artifact_ids"][-1]
            return ModelResponse(parts=[ToolCallPart("final_result", {
                "summary": "按纠正说明停止重复写，已有产物保留", "artifact_ids": [saved]}, "corrected-answer")])
        return ModelResponse(parts=[ToolCallPart("write_file", {"relative_path": "same.md", "content": "same confirmed bytes"}, "same-call-id" if reuse_call_id else f"write-{requests}")])
    fixture = FixtureFunctionModel(model, stream_function=stream_adapter(model))
    original = engine.make_agent
    def make_agent(*args, **kwargs):
        agent, connection = original(*args, **kwargs)
        agent.model = fixture
        return agent, connection
    monkeypatch.setattr(engine, "make_agent", make_agent)
    engine.submit(state["task_id"], "协议夹具：重复写入相同内容", "repeated-effects")
    await engine.queue[state["task_id"]]
    await asyncio.sleep(0)
    stopped = engine.store.task(state["task_id"])
    assert stopped["status"] == "paused" and stopped["blockers"][0]["code"] == "NO_PROGRESS"
    effects = 1 if reuse_call_id else 4
    assert requests == 4 and stopped["action_count"] == effects and len(stopped["artifact_ids"]) == effects
    with engine.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM actions WHERE task_id=? AND status='pending'", (state["task_id"],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM attempts WHERE effect_state='confirmed'").fetchone()[0] == effects
    engine.progress = Progress(Store(engine.store.path), engine.config)
    engine.submit(state["task_id"], "恢复相同逻辑回合", "repeated-effects")
    await engine.queue[state["task_id"]]
    assert requests == 4 and engine.store.task(state["task_id"])["model_requests"] == 4
    assert engine.store.task(state["task_id"])["status"] == "paused"
    await asyncio.sleep(0)
    response = await asyncio.to_thread(client.post, f"/tasks/{state['task_id']}/resume", json={
        "request_id": "no-reset", "expected_state_version": engine.store.task(state["task_id"])["state_version"]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "NO_PROGRESS"
    response = await asyncio.to_thread(client.post, f"/tasks/{state['task_id']}/messages", json={
        "request_id": "correction", "expected_spec_revision": 1, "text": "不要重复写，引用现有文件说明结果即可"})
    assert response.status_code == 200 and response.json()["status"] == "paused"
    response = await asyncio.to_thread(client.post, f"/tasks/{state['task_id']}/resume", json={
        "request_id": "resume-correction", "expected_state_version": response.json()["state_version"]})
    assert response.status_code == 200
    with engine.store.transaction() as db:
        assert db.execute("SELECT status FROM turns WHERE id='repeated-effects'").fetchone()[0] == "stopped"
        next_turn = db.execute("SELECT turn_id FROM turn_requests WHERE request_id='correction'").fetchone()[0]
    assert engine.start_queued(state["task_id"]) == next_turn
    await engine.queue[state["task_id"]]
    continued = engine.store.task(state["task_id"])
    assert continued["status"] == "waiting_user" and continued["spec_revision"] == 1
    assert requests == 5 and continued["model_requests"] == 5 and continued["action_count"] == effects
    assert len(continued["artifact_ids"]) == effects
    assert recorded(engine.progress, Deps(engine, state["task_id"], "repeated-effects", 1))[-1]["stalled"] == 3


@pytest.mark.asyncio
async def test_native_empty_overflow_window_loop_stops_with_original_bytes_preserved(client, app):
    state, _ = task(client)
    engine = app.state.engine
    agent, connection = engine.make_agent("small", "eof-loop", task_id=state["task_id"])
    content = "immutable fixture line\n" * 1000
    @agent.tool_plain
    def large_fixture() -> str:
        return content
    seen = {}
    calls = 0
    def model(messages, info):
        nonlocal calls
        calls += 1
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("large_fixture", {}, "source")])
        if "handle" not in seen:
            seen["handle"] = returns[-1].metadata["overflow_handle"]
        return ModelResponse(parts=[ToolCallPart("read_tool_result_window", {"handle": seen["handle"], "offset": 999999}, f"eof-{calls}")])
    try:
        with agent.override(model=FixtureFunctionModel(model)), pytest.raises(Failure, match="连续3"):
            await agent.run("检测反复读取末尾", deps=Deps(engine, state["task_id"], "eof-loop", 1))
        assert calls == 4
        native = LocalFileStore(base_dir=engine.config.workspace_root / ".forestry" / "tool-results" / state["task_id"])
        assert (await native.read(seen["handle"])).decode() == content
        with engine.store.transaction() as db:
            records = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE task_id=? AND kind='progress.observed'", (state["task_id"],))]
        assert [r["stalled"] for r in records] == [0, 1, 2, 3]
    finally:
        await connection.aclose()
