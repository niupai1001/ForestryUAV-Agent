"""Run unchanged four behavior contracts against the independent loop and A."""
import importlib.util
from pathlib import Path
import sys

import pytest

# Experimental candidates are explicitly run, not collected as production tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tests'))
import conftest as fixtures  # noqa: E402

app, client = fixtures.app, fixtures.client


def driver_b_factory(engine):
    path = Path(__file__).resolve().parents[1]/'research/driver_b.py'
    spec = importlib.util.spec_from_file_location('isolated_driver_b',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.factory(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize('driver', ['A','B'])
@pytest.mark.parametrize('scenario', ['multisession','job_completion','cancel_resume','discovery'])
async def test_shared_runtime_contract(client,app,monkeypatch,driver,scenario):
    from test_native_driver_contract import test_route_a_native_discovery_shared_runtime_contract
    if driver == 'B':
        monkeypatch.setattr(app.state.engine,'make_agent',driver_b_factory(app.state.engine))
    await test_route_a_native_discovery_shared_runtime_contract(client,app,monkeypatch,scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['permission','frozen_batch'])
async def test_driver_b_discovery_negative_boundaries(client,app,monkeypatch,case):
    monkeypatch.setattr(app.state.engine,'make_agent',driver_b_factory(app.state.engine))
    if case == 'permission':
        from test_tool_discovery import test_discovery_activates_next_step_and_respects_permissions
        await test_discovery_activates_next_step_and_respects_permissions(
            client,app,monkeypatch,True,discovery_mode='native_bm25')
    else:
        from test_tool_discovery import test_search_cannot_activate_a_hidden_tool_inside_the_same_batch
        await test_search_cannot_activate_a_hidden_tool_inside_the_same_batch(
            client,app,monkeypatch,discovery_mode='native_bm25')
