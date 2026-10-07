import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from test_core import task
from test_roles import FixtureFunctionModel, stream_adapter

from forestry_agent.agent import Deps


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
async def test_rejected_text_preserves_native_retry_instead_of_accepting_none(client, app, recover):
    state, _ = task(client, constraints={"allowed_capabilities": []})
    engine, requests = app.state.engine, []
    agent, connection = engine.make_agent("small", "invalid-output-recovery", state["config_snapshot"], state["task_id"])
    def model(messages, info):
        requests.append(messages)
        if recover and len(requests) > 1:
            return ModelResponse(parts=[ToolCallPart("final_result", {"summary": "已纠正输出格式", "artifact_ids": []}, "valid")])
        return ModelResponse(parts=[TextPart("这不是JSON，但声称任务已经完成。")])
    try:
        with agent.override(model=FixtureFunctionModel(model, stream_function=stream_adapter(model))):
            if recover:
                result = await agent.run("按原生输出协议回答", deps=Deps(engine, state["task_id"], "invalid-output-recovery", 1))
                assert result.output.summary == "已纠正输出格式" and len(requests) == 2
            else:
                with pytest.raises(UnexpectedModelBehavior, match="retries"):
                    await agent.run("按原生输出协议回答", deps=Deps(engine, state["task_id"], "invalid-output-recovery", 1))
                assert len(requests) == 4  # Initial request + the unchanged three retries.
        events = engine.store.events(state["task_id"])
        assert any(e["type"] == "model.output_rejected" for e in events)
        assert not any(e["type"] == "action.started" for e in events)
    finally:
        await connection.aclose()
