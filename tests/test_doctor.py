import importlib
from types import SimpleNamespace

import pytest

from forestry_agent.config import Config, ModelProfile

module = importlib.import_module("forestry_agent.doctor")


@pytest.mark.asyncio
@pytest.mark.parametrize("installed,search,docker,startup,prerequisites", [
    (["small"], True, True, True, False),
    (["small", "large"], True, True, True, True),
    (["small", "large"], False, False, True, False),
    (["large"], True, True, False, False),
])
async def test_endpoint_probes_never_certify_general_agent(monkeypatch, tmp_path, installed, search, docker, startup, prerequisites):
    config = Config(workspace_root=tmp_path, search_enabled=True, models={key: ModelProfile(repository="fixture/" + key, served_name=key)
                                                 for key in ["small", "large"]})
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"models": [{"name": key, "digest": key} for key in installed]})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(module.platform, "platform", lambda: "fixture-system")
    def process(command, **kwargs):
        assert command[0] == "nvidia-smi"
        return SimpleNamespace(returncode=0, stdout="fixture-gpu", stderr="")
    monkeypatch.setattr(module.subprocess, "run", process)
    monkeypatch.setattr(module.Docker, "call", lambda *args: {
        "code": 0 if docker else 1, "stdout": "fixture-version" if docker else "",
        "stderr": "" if docker else "unavailable"})
    monkeypatch.setattr(module.PublicHTTP, "get", lambda *args, **kwargs: {
        "body": b'<div class="result c-container" mu="https://docs.example.org/"><h3>reference</h3></div>' if search else b"<html>Error</html>",
        "headers": {"content-type": "text/html"}})
    report = await module.doctor(config)
    assert report["startup_ready"] is startup
    assert report["general_prerequisites_ready"] is prerequisites
    assert report["general_ready"] is False
    assert report["general_validation"] == "not_assessed_by_doctor"
    assert bool(report["general_prerequisite_blockers"]) is not prerequisites
    if "large" not in installed:
        assert "model:large" in report["general_prerequisite_blockers"]
    if not search:
        assert "search" in report["general_prerequisite_blockers"]
    if not docker:
        assert "restricted_code" in report["general_prerequisite_blockers"]
    assert report["capabilities"]["restricted_code"]["transport"] == "docker_cli"
    assert report["capabilities"]["restricted_code"]["version"] == ("fixture-version" if docker else None)


@pytest.mark.asyncio
async def test_unprobed_search_is_not_a_ready_prerequisite(monkeypatch, tmp_path):
    config = Config(workspace_root=tmp_path, search_enabled=True, models={"small": ModelProfile(repository="fixture/small", served_name="small")})
    monkeypatch.setattr(module.PublicHTTP, "get", lambda *args, **kwargs: pytest.fail("unrequested network probe"))
    # Other endpoint failures are legitimate diagnostic results; no actual service is needed.
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"models": []})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="fixture"))
    monkeypatch.setattr(module.Docker, "call", lambda *args: {"code": 1, "stdout": "", "stderr": "fixture"})
    report = await module.doctor(config, probe_search=False)
    assert report["capabilities"]["search"]["status"] == "not_probed"
    assert "search" in report["general_prerequisite_blockers"]
    assert not report["startup_ready"] and not report["general_ready"]
