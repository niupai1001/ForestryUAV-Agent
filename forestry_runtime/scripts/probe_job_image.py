"""Ask the host bridge what the job image can actually import.

The answer that matters for the capability task set is not "is numpy installed in
this repository's virtualenv" but "can model-written code import numpy inside the
container the bridge starts".  Those are different images, and when the bridge
falls back to its default `python:3.12-slim` the second answer is "no" for every
raster library -- which reads downstream as an Agent that refuses to do
straightforward work.

    python scripts/probe_job_image.py rasterio numpy gdal

Run it after starting the bridge and after any image rebuild.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import urllib.request

DEFAULT_MODULES = ("rasterio", "numpy", "scipy", "sklearn", "gdal")

# These are absent on purpose, and the task set depends on it:
#   sklearn  `capability.supervised` exists to exercise dependency_install and the
#            verification that must pass before dependent code may start;
#   gdal     rasterio is the image's raster reader, so an Agent that insists on
#            GDAL is doing something the environment cannot satisfy.
EXPECTED_ABSENT = frozenset({"sklearn", "gdal"})


def bridge_key(project_root: Path) -> str:
    for line in (project_root / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("HOST_BRIDGE_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("HOST_BRIDGE_KEY is not set in .env")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="*", default=list(DEFAULT_MODULES))
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    data_root = Path(os.environ.get(
        "RUNTIME_DATA_HOST_ROOT", str(project_root / "data")
    ))
    workspace = data_root / "workspaces" / "probe"
    (workspace / ".runtime" / "deps").mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(
        args.base_url + "/jobs/run",
        data=json.dumps({
            "workspace": str(workspace).replace("\\", "/"),
            "chat_id": "probe",
            "modules": list(args.modules),
            "timeout_seconds": 120,
        }).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + bridge_key(project_root),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        reader = getattr(exc, "read", None)
        print("jobs/run failed:", type(exc).__name__, exc)
        if reader:
            print(reader().decode("utf-8", "replace")[:600])
        return 2

    print("job image:", payload.get("image"), "| pythonpath:", payload.get("pythonpath"))
    missing = []
    for row in payload.get("modules") or []:
        name = str(row.get("module"))
        ok = bool(row.get("importable"))
        note = ""
        if not ok and name in EXPECTED_ABSENT:
            note = "  (absent on purpose)"
        print(f"  {'OK  ' if ok else 'MISS'} {name:<12} "
              f"version={row.get('version')} {'' if ok else str(row.get('error'))[:60]}{note}")
        if not ok and name not in EXPECTED_ABSENT:
            missing.append(name)
    if missing:
        print("\nThe job image cannot import:", ", ".join(missing))
        print("Set AGENT_JOB_IMAGE to the image the Runtime is built from and restart "
              "the bridge with scripts/start-host-bridge.ps1.")
        return 1
    print("\nEvery module the task set needs is importable in the job image.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
