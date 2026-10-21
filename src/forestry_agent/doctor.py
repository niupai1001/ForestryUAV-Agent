from __future__ import annotations

import asyncio
import importlib.metadata
import platform
import shutil
import subprocess

import httpx
import psutil

from .config import Config
from .contracts import Failure
from .jobs import Docker
from .network import PublicHTTP, decode_text, search_request, search_results
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
    base = config.search_configuration().get("base_url")
    report["capabilities"]["search"] = {"status": ("disabled" if not config.search_enabled else
        "not_probed" if base else "not_configured"), "provider": config.search_provider}
    if base and probe_search:
        try:
            url, origins = search_request(config.search_provider, base, "OpenDroneMap")
            response = await asyncio.to_thread(PublicHTTP(timeout_seconds=10).get, url, allowed_origins=origins)
            results = search_results(config.search_provider, decode_text(response["body"], response["headers"].get("content-type", "")))
            report["capabilities"]["search"].update(status="available" if results else "empty_not_verified", result_count=len(results))
        except (Failure, OSError, ValueError) as error:
            report["capabilities"]["search"].update(status="failed", message=str(error))
    try:
        command = await asyncio.to_thread(Docker(config).call, "version", "--format", "{{.Server.Version}}")
        report["capabilities"]["restricted_code"] = {
            "status": "available" if command["code"] == 0 else "unavailable",
            "version": command["stdout"].strip() or None, "message": command["stderr"].strip()[:1500],
            "transport": "wsl_cli" if config.docker_distribution else "docker_cli",
            "distribution": config.docker_distribution}
    except (Failure, OSError, subprocess.TimeoutExpired) as error:
        report["capabilities"]["restricted_code"] = {"status": "unavailable", "message": str(error)}
    report["capabilities"]["file_tools"] = {"status": "available"}
    report["capabilities"]["domain"] = {"status": "configured" if config.science_image else "not_configured",
        "image": config.science_image, "validation": "see_frozen_delivery_evidence", "stage": 2}
    report["capabilities"]["imagery"] = {"status": "configured" if config.imagery_image else "not_configured",
        "image": config.imagery_image, "orthomosaic_image": config.orthomosaic_image,
        "validation": "see_IMAGERY-PROSAIL-MIGRATION-20261021-zh.md"}
    installed_profiles = {key for key, value in report["models"].items()
                          if value["status"] == "available_not_tool_verified"}
    report["startup_ready"] = config.default_model in installed_profiles
    missing = ["model:" + key for key in config.models if key not in installed_profiles]
    missing.extend(name for name in (["search"] if config.search_enabled else []) + ["restricted_code"]
                   if report["capabilities"][name]["status"] != "available")
    report["general_prerequisites_ready"] = not missing
    report["general_prerequisite_blockers"] = missing
    # Endpoint/weight probes never prove tools, recovery, UI or task acceptance.
    report["general_ready"] = False
    report["general_validation"] = "not_assessed_by_doctor"
    return report

