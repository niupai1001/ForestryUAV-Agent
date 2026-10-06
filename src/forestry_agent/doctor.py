from __future__ import annotations

import asyncio
import importlib.metadata
import platform
import shutil
import subprocess

import httpx
import psutil

from .config import Config
from .store import now


async def doctor(config: Config, *, probe_search: bool = True) -> dict:
    config.workspace_root.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(config.workspace_root)
    report = {"schema_version": 1, "observed_at": now(), "system": platform.platform(),
        "python": platform.python_version(), "cpu_threads": psutil.cpu_count(),
        "ram_bytes": psutil.virtual_memory().total, "ram_available_bytes": psutil.virtual_memory().available,
        "disk": {"path": str(config.workspace_root), "total_bytes": disk.total, "free_bytes": disk.free},
        "models": {}, "capabilities": {}, "packages": {}, "gpus": [],
        "low_resource_profile": "not_selected", "domain_scale": "untested"}
    for package in ["pydantic-ai-slim", "pydantic-ai-harness", "fastapi", "pydantic", "httpx"]:
        report["packages"][package] = importlib.metadata.version(package)
    try:
        gpu = await asyncio.to_thread(subprocess.run,
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=False)
        if gpu.returncode == 0:
            report["gpus"] = [line.strip() for line in gpu.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        try:
            response = await client.get(config.ollama_base_url + "/api/tags")
            response.raise_for_status()
            installed = {m["name"]: m for m in response.json()["models"]}
            for key, profile in config.models.items():
                match = installed.get(profile.served_name)
                report["models"][key] = {"profile": profile.model_dump(),
                    "status": "available_not_tool_verified" if match else "unavailable",
                    "digest": match.get("digest") if match else None,
                    "precision": match.get("details", {}).get("quantization_level") if match else None,
                    "capabilities": match.get("capabilities", []) if match else []}
        except (httpx.HTTPError, KeyError, ValueError) as error:
            report["capabilities"]["local_models"] = {"status": "failed", "message": str(error)}
        if config.search_base_url and probe_search:
            try:
                response = await client.get(config.search_base_url.rstrip("/") + "/search",
                                            params={"q": "OpenDroneMap", "format": "json"})
                response.raise_for_status()
                if not isinstance(response.json().get("results"), list):
                    raise TypeError("Search response does not include a results array")
                report["capabilities"]["search"] = {"status": "available"}
            except (httpx.HTTPError, ValueError, TypeError) as error:
                report["capabilities"]["search"] = {"status": "failed", "message": str(error)}
        else:
            report["capabilities"]["search"] = {"status": "not_probed" if config.search_base_url else "not_configured"}
    try:
        command = await asyncio.to_thread(subprocess.run,
            ["docker", "--config", str(config.workspace_root / ".forestry" / "docker"),
             "--host", config.docker_host, "version",
             "--format", "{{.Server.Version}}"], capture_output=True, text=True, timeout=10, check=False)
        report["capabilities"]["restricted_code"] = {
            "status": "available" if command.returncode == 0 else "unavailable",
            "version": command.stdout.strip() or None, "message": command.stderr.strip()[:1500]}
    except (OSError, subprocess.TimeoutExpired) as error:
        report["capabilities"]["restricted_code"] = {"status": "unavailable", "message": str(error)}
    report["capabilities"]["file_tools"] = {"status": "available"}
    report["capabilities"]["domain"] = {"status": "not_implemented", "stage": 2}
    report["general_ready"] = any(m["status"] == "available_not_tool_verified" for m in report["models"].values())
    return report

