import pytest
from pydantic_ai.models.test import TestModel
from test_core import task

from forestry_agent.agent import Deps


@pytest.mark.asyncio
async def test_native_hooks_output_schema_and_checkpoint_compatible(client, app):
    state, _ = task(client)
    engine = app.state.engine
    agent, connection = engine.make_agent("small", "native-test-turn")
    try:
        model = TestModel(call_tools=[], custom_output_args={
            "summary": "框架协议夹具", "artifact_ids": [], "limitations": ["非真实模型试次"]})
        with agent.override(model=model):
            result = await agent.run("验证协议", deps=Deps(engine, state["task_id"], "native-test-turn", 1))
        assert result.output.summary == "框架协议夹具"
        assert engine.store.task(state["task_id"])["model_requests"] == 1
        events = engine.store.events(state["task_id"])
        response = next(e for e in events if e["type"] == "model.response_observed")
        assert response["payload"]["tool_names"] == ["final_result"]
        assert response["payload"]["output_calls"][0]["arguments"]
        assert await engine.steps.latest_snapshot(run_id="native-test-turn") is not None
    finally:
        await connection.aclose()
