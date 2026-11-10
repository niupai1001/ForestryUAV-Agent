"""Original software regressions with an explicit, locally verified target tokenizer.

Responses remain synthetic. These tests do not count as Qwen task-success trials.
"""
import hashlib
from pathlib import Path

import pytest

from forestry_agent.runtime.tokenization import local_tokenizer


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['native_facts', 'source_history'])
async def test_original_context_regressions_with_bound_qwen_tokenizer(client, app, monkeypatch, tmp_path, scenario):
    profile = app.state.engine.config.models['small']
    manifest = Path.home() / '.ollama/models/manifests/registry.ollama.ai/library/qwen3.5/4b'
    if not manifest.exists():
        pytest.skip('local Qwen35 tokenizer asset unavailable; not model failure')
    identity = {'backend': 'ollama', 'served_name': profile.served_name,
                'digest': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    counter, reason = local_tokenizer(profile, identity)
    if counter is None:
        pytest.skip(reason)
    store = app.state.store
    original = store.create_task

    def create(payload, snapshot, **kwargs):
        # Explicit target-tokenizer configuration; model output is still a fixture.
        snapshot = {**snapshot, 'model_identity': identity, 'validation_backend': 'software_fixture'}
        return original(payload, snapshot, **kwargs)

    monkeypatch.setattr(store, 'create_task', create)
    if scenario == 'native_facts':
        from test_native_loop import test_named_artifacts_and_recent_job_records_are_scoped_bounded_native_facts
        await test_named_artifacts_and_recent_job_records_are_scoped_bounded_native_facts(client, app, 'H2')
    else:
        from test_progress import test_recent_source_bodies_survive_changed_instructions_larger_than_old_tail
        await test_recent_source_bodies_survive_changed_instructions_larger_than_old_tail(client, app, tmp_path)
    with store.snapshot() as db:
        rows = db.execute('SELECT payload FROM contexts').fetchall()
        assert rows
        import json
        assert any(json.loads(row['payload']).get('text_tokenizer_identity', '').startswith('qwen35-bpe:') for row in rows)
