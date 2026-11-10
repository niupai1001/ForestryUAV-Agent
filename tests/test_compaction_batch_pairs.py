import pytest
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai_harness.compaction import pin

from forestry_agent.context import UsageAwareSlidingWindow


def batch_history(*, retry=False, reused=False):
    history = [ModelRequest(parts=[UserPromptPart('保留这个完整目标')])]
    if reused:
        history += [ModelResponse(parts=[ToolCallPart('read_file', {}, 'call-0')]),
                    ModelRequest(parts=[ToolReturnPart('read_file', 'old result', 'call-0')])]
    history.append(ModelResponse(parts=[ToolCallPart('read_file', {}, f'call-{i}') for i in range(12)]))
    for index in range(12):
        part = (RetryPromptPart('invalid arguments', tool_name='read_file', tool_call_id=f'call-{index}')
                if retry and index == 11 else ToolReturnPart('read_file', f'result-{index}', f'call-{index}'))
        history.append(ModelRequest(parts=[part]))
    history += [ModelResponse(parts=[TextPart('保留来源结论')]), ModelRequest(parts=[UserPromptPart('继续')])]
    return history


@pytest.mark.asyncio
@pytest.mark.parametrize('retry,reused', [(False, False), (True, False), (False, True)])
async def test_native_compaction_retains_large_batch_pairs_and_drops_only_settled_prefix(retry, reused):
    history = batch_history(retry=retry, reused=reused)
    compact = UsageAwareSlidingWindow(max_messages=4, keep_messages=3)
    result = await compact.compact(history, None)
    # Prove the compactor actually trimmed the old settled pair, not a vacuous invariant.
    if reused:
        assert len(result) < len(history)
        assert history[1] not in result and history[2] not in result
    assert result[0] is history[0] and result[-1] is history[-1]
    pending = {}
    completed = []
    for message in result:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                assert part.tool_call_id not in pending
                pending[part.tool_call_id] = part
            elif isinstance(part, (ToolReturnPart, RetryPromptPart)):
                call = pending.pop(part.tool_call_id)
                assert call.tool_name == part.tool_name
                completed.append(part.tool_call_id)
    assert not pending and len(completed) == 12


@pytest.mark.asyncio
async def test_compaction_still_removes_whole_completed_batch_when_it_is_outside_tail():
    history = batch_history()
    history.extend([ModelResponse(parts=[TextPart('later')]), ModelRequest(parts=[UserPromptPart('latest')])])
    result = await UsageAwareSlidingWindow(max_messages=4, keep_messages=2).compact(history, None)
    assert len(result) == 3
    assert result[0] is history[0] and result[-1] is history[-1]
    assert not any(isinstance(part, (ToolCallPart, ToolReturnPart)) for msg in result for part in msg.parts)


@pytest.mark.asyncio
async def test_repaired_boundary_preserves_native_pin_and_emits_one_truthful_receipt():
    history = batch_history(reused=True)
    history.insert(1, ModelRequest(parts=[pin('保留关键证据引用')]))
    result = await UsageAwareSlidingWindow(max_messages=4, keep_messages=4, receipts=True).compact(history, None)
    text = '\n'.join(str(part.content) for msg in result if isinstance(msg, ModelRequest)
                     for part in msg.parts if isinstance(part, UserPromptPart))
    assert '保留关键证据引用' in text and '保留这个完整目标' in text
    assert text.count('was dropped by the harness') == 1
    assert history[2] not in result and history[3] not in result
    assert sum(isinstance(part, ToolCallPart) for msg in result for part in msg.parts) == 12
