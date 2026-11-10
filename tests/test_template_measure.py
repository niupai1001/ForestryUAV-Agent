from copy import deepcopy

from test_tokenization import fixture_model

from forestry_agent.config import ModelProfile
from forestry_agent.runtime.tokenization import local_tokenizer


def tokenizer(tmp_path, template):
    root, identity = fixture_model(tmp_path, template=template)
    counter, _ = local_tokenizer(ModelProfile(repository='fixture', served_name='fixture'), identity, root)
    return counter


def test_template_uses_actual_tool_and_history_shapes_without_mutating_request(tmp_path):
    counter = tokenizer(tmp_path, "{{ tools|tojson }}:{% for m in messages %}{{ m.role }}:{{ m.content }}"
        "{% for c in m.tool_calls|default([]) %}{{ c.function.arguments|tojson }}{% endfor %}{% endfor %}"
        ":{{ enable_thinking }}:{{ add_generation_prompt }}")
    payload = {'messages': [{'role': 'user', 'content': 'aaaa'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {
            'name': 'inspect', 'arguments': '{"value": 4}'}}]}, {'role': 'tool', 'content': '4'}],
        'tools': [{'type': 'function', 'function': {'name': 'inspect'}}], 'reasoning_effort': 'none'}
    original = deepcopy(payload)
    rendered = counter.render_wire(payload)
    assert 'inspect' in rendered and '{"value": 4}' in rendered and 'False:True' in rendered
    measured = counter.measure_wire(payload)
    assert measured['status'] == 'estimated' and measured['total_tokens'] >= measured['text_tokens']
    assert payload == original


def test_visual_template_never_reports_zero_or_complete_visual_cost(tmp_path):
    counter = tokenizer(tmp_path, '{% for m in messages %}{{ m.role }}{% endfor %}')
    result = counter.measure_wire({'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,fixture'}}]}]})
    assert result['status'] == 'partial' and result['total_tokens'] is None
    assert result['vision_tokens_status'] == 'unknown'


def test_unsupported_template_is_unknown_instead_of_a_zero_estimate(tmp_path):
    counter = tokenizer(tmp_path, '{{ messages.__class__.__mro__ }}')
    result = counter.measure_wire({'messages': [{'role': 'user', 'content': 'a'}]})
    assert result['status'] == 'unknown' and result['total_tokens'] is None
