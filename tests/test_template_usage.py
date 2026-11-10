import json

import pytest
from pydantic_ai.usage import RequestUsage
from test_core import task

from forestry_agent.store import encode


@pytest.mark.parametrize('visual,reported', [(False, 80), (True, 500), (False, 0)])
def test_template_delta_does_not_attribute_unknown_vision_to_text_or_zero_cost(client, app, visual, reported):
    state, _ = task(client)
    value = {'task_id': state['task_id'], 'turn_id': 'measurement', 'estimated_tokens_upper_bound': 1000,
        'available_input_tokens': 1000, 'wire_template_measurement': {
            'status': 'partial' if visual else 'estimated', 'text_tokens': 100,
            'total_tokens': None if visual else 110,
            'vision_tokens_status': 'unknown' if visual else 'not_present'}}
    with app.state.store.transaction() as db:
        db.execute('INSERT INTO contexts VALUES(?,?,?,?)', ('template-usage', state['task_id'], 'measurement', encode(value)))
    app.state.engine.contexts.measured('template-usage', RequestUsage(input_tokens=reported, output_tokens=1))
    with app.state.store.snapshot() as db:
        saved = json.loads(db.execute("SELECT payload FROM contexts WHERE id='template-usage'").fetchone()[0])
    if visual or not reported:
        assert saved['wire_template_delta_tokens'] is None
        assert saved['wire_template_estimate_covers_usage'] is None
    else:
        assert saved['wire_template_delta_tokens'] == -20
        assert saved['wire_template_estimate_covers_usage'] is True
    assert 'actual_vision_tokens' not in saved
