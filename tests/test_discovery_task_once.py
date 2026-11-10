import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def runner(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'research/discovery_task_once.py'
    spec = importlib.util.spec_from_file_location('discovery_task_runner_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path / 'trial')
    return module


def test_prepare_never_creates_alias_when_shared_model_is_occupied(runner, monkeypatch):
    calls = []
    def api(endpoint, payload=None):
        calls.append(endpoint)
        return {'models': [{'name': 'other-owner'}]} if endpoint == 'ps' else {'models': []}
    monkeypatch.setattr(runner, 'api', api)
    with pytest.raises(AssertionError, match='occupied'):
        runner.prepare()
    assert 'create' not in calls
    assert (runner.ROOT / 'before.json').exists()
    with pytest.raises(FileExistsError):
        runner.prepare()


def test_same_trial_refuses_before_any_model_api(runner, monkeypatch):
    runner.ROOT.mkdir()
    inputs = runner.ROOT / 'inputs'
    inputs.mkdir()
    (inputs / 'result.json').write_bytes(b'{}')
    runner.save(runner.ROOT / 'protocol.json', {'source': 'frozen',
        'script_sha256': runner.digest(Path(runner.__file__).read_bytes()), 'input_sha256': runner.digest(b'{}')})
    (runner.ROOT / 'static').mkdir()
    monkeypatch.setattr(runner, 'source_manifest', lambda: {'sha256': 'frozen'})
    def forbidden(*args, **kwargs):
        pytest.fail('existing trial reached model service')
    monkeypatch.setattr(runner, 'api', forbidden)
    with pytest.raises(FileExistsError):
        runner.run('static')
