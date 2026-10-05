import pytest
from fastapi.testclient import TestClient

from forestry_agent.app import create_app
from forestry_agent.config import Config, ModelProfile


@pytest.fixture
def app(tmp_path):
    config = Config(workspace_root=tmp_path / "workspace", models={
        "small": ModelProfile(repository="Qwen/Qwen3.5-4B", served_name="qwen3.5:4b")})
    return create_app(config, enable_agent=False)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}) as browser:
        browser.get("/session")
        yield browser
