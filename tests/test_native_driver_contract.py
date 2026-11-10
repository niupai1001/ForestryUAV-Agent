"""The same runtime contracts, run against route A with native tool discovery.

This is not a substitute for the separate driver-B prototype or real-model trials.
"""
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['multisession', 'job_completion', 'cancel_resume', 'discovery'])
async def test_route_a_native_discovery_shared_runtime_contract(client, app, monkeypatch, scenario):
    app.state.engine.config.tool_discovery_mode = 'native_bm25'
    if scenario == 'multisession':
        from test_multisession_runtime import test_other_session_answers_while_first_session_tool_is_waiting
        await test_other_session_answers_while_first_session_tool_is_waiting(client, app, monkeypatch)
    elif scenario == 'job_completion':
        from test_job_continuation import test_completion_resumes_once_and_keeps_original_activity_budget
        await test_completion_resumes_once_and_keeps_original_activity_budget(client, app, monkeypatch, False)
    elif scenario == 'cancel_resume':
        from test_cancelled_batch import test_cancel_drains_started_effect_and_pairs_unstarted_sibling
        await test_cancel_drains_started_effect_and_pairs_unstarted_sibling(client, app, monkeypatch)
    else:
        from test_tool_discovery import test_discovery_activates_next_step_and_respects_permissions
        await test_discovery_activates_next_step_and_respects_permissions(client, app, monkeypatch, False,
            discovery_mode='native_bm25')
