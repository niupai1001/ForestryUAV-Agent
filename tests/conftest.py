import pytest
from fastapi.testclient import TestClient

from forestry_agent.app import create_app
from forestry_agent.config import Config, ModelProfile


@pytest.fixture
def app(tmp_path, monkeypatch, request):
    # API/unit fixtures have no real Docker worker. Deterministic measurements
    # keep them distinct from the separately recorded real WSL admission cases.
    monkeypatch.setattr('forestry_agent.jobs.execution_profile', lambda config: {
        'observed_at': 'unit-fixture', 'host_ram_available': 64 * 2**30,
        'executor_ram_available': 64 * 2**30, 'executor_ram_total': 64 * 2**30,
        'executor_cpu_threads': 8, 'workspace_disk_free': 64 * 2**30,
        'source': 'unit_fixture', 'vram_request_supported': 0})
    # Legacy network contracts explicitly opt in; the production default remains off.
    config = Config(workspace_root=tmp_path / "workspace", search_enabled=not request.node.get_closest_marker('offline_web'), models={
        "small": ModelProfile(repository="Qwen/Qwen3.5-4B", served_name="qwen3.5:4b")})
    return create_app(config, enable_agent=False)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}) as browser:
        browser.get("/session")
        yield browser
