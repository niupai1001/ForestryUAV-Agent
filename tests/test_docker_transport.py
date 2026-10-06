import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from forestry_agent.config import Config, ModelProfile
from forestry_agent.contracts import Failure
from forestry_agent.jobs import Docker

module = importlib.import_module("forestry_agent.jobs")


def config(**kwargs):
    return Config(models={"small": ModelProfile(repository="fixture/small", served_name="small")}, **kwargs)


def test_wsl_argv_preserves_scoped_mounts_without_a_shell(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: name)
    docker = Docker(config(workspace_root=Path("C:/project space/工作"),
        docker_distribution="forestry-test", docker_host="unix:///var/run/docker.sock"))
    command = docker.command("create", "--mount", "type=bind,src=E:\\private data\\树.csv,dst=/inputs/source,readonly",
        "--mount", "type=bind,source=C:\\output,dst=/output", "--network", "none", "image-id", "python", "-I", "/run.py")
    assert command[:7] == ["wsl.exe", "--distribution", "forestry-test", "--user", "root", "--exec", "/usr/bin/env"]
    assert command[7:10] == ["-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root"]
    assert command[10:15] == ["docker", "--config", "/mnt/c/project space/工作/.forestry/docker", "--host", "unix:///var/run/docker.sock"]
    assert "type=bind,src=/mnt/e/private data/树.csv,dst=/inputs/source,readonly" in command
    assert "type=bind,source=/mnt/c/output,dst=/output" in command
    assert command[-6:] == ["--network", "none", "image-id", "python", "-I", "/run.py"]
    assert not any(arg in {"sh", "bash", "cmd", "-c"} for arg in command)


@pytest.mark.parametrize("path", ["relative.csv", "C:relative.csv", "\\\\server\\private\\file", "/linux/file"])
def test_wsl_rejects_nonlocal_or_relative_host_mounts(path):
    with pytest.raises(Failure) as error:
        Docker.wsl_path(path)
    assert error.value.code == "PRECONDITION_MISSING"


@pytest.mark.parametrize("name,endpoint", [
    ("--other", "unix:///var/run/docker.sock"), ("test;whoami", "unix:///var/run/docker.sock"),
    ("test", "tcp://127.0.0.1:2375"), ("test", "npipe:////./pipe/dockerDesktopLinuxEngine"),
])
def test_wsl_requires_literal_distribution_and_guest_socket(name, endpoint):
    with pytest.raises(ValidationError):
        config(docker_distribution=name, docker_host=endpoint)


def test_native_transport_is_explicit_and_never_inherits_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(module.shutil, "which", lambda name: "C:/trusted/docker.exe")
    for name in ["DOCKER_HOST", "DOCKER_AUTH_CONFIG", "HTTP_PROXY", "WSLENV", "FORESTRY_SECRET"]:
        monkeypatch.setenv(name, "synthetic-never-send")
    observed = {}

    def process(command, **kwargs):
        observed.update(command=command, options=kwargs)
        kwargs["stdout"].write(b"x" * 100000)
        kwargs["stderr"].write(b"e" * 5000)
        return SimpleNamespace(returncode=19)

    monkeypatch.setattr(module.subprocess, "run", process)
    result = Docker(config(workspace_root=tmp_path)).call("inspect", "job")
    assert observed["command"] == ["C:/trusted/docker.exe", "--config", str(tmp_path / ".forestry" / "docker"),
        "--host", "npipe:////./pipe/dockerDesktopLinuxEngine", "inspect", "job"]
    assert set(observed["options"]["env"]) <= {"PATH", "SystemRoot", "WINDIR", "TEMP", "TMP"}
    assert "synthetic-never-send" not in observed["options"]["env"].values()
    assert observed["options"]["cwd"] == tmp_path
    assert result == {"code": 19, "stdout": "x" * 65536, "stderr": "e" * 4096}
