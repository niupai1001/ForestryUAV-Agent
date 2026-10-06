"""Trusted developer fixture download; never exposed as an Agent tool.

Fixed public release metadata, no task inputs, no package installation/execution.
The official JSON SHA binds fixture bytes; it is not an independent signature.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx

from forestry_agent.store import now

ROOT = Path(__file__).resolve().parent.parent
RELEASES = {
    "numpy": ("2.2.6", ["numpy-2.2.6-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "numpy-2.2.6-cp312-cp312-win_amd64.whl"]),
    "python-dateutil": ("2.9.0.post0", ["python_dateutil-2.9.0.post0-py2.py3-none-any.whl"]),
    "six": ("1.17.0", ["six-1.17.0-py2.py3-none-any.whl"]),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["pypi", "tuna"], default="pypi")
    args = parser.parse_args()
    directory = ROOT/"workspace"/"dependency-fixtures"
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    with httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False, follow_redirects=False) as client:
        for name, (version, filenames) in RELEASES.items():
            url = f"https://pypi.org/pypi/{name}/{version}/json"
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > 2*1024*1024:
                raise RuntimeError("Release metadata exceeded 2 MiB")
            release = response.json()
            if release["info"]["version"] != version:
                raise RuntimeError("Release version mismatch")
            metadata = directory/(name+"-"+version+"-"+str(uuid4())+".json")
            metadata.write_bytes(response.content)
            for filename in filenames:
                entry = next(e for e in release["urls"] if e["filename"] == filename and e["packagetype"] == "bdist_wheel")
                download, expected = entry["url"], entry["digests"]["sha256"]
                if not download.startswith("https://files.pythonhosted.org/packages/") or not re.fullmatch("[0-9a-f]{64}", expected):
                    raise RuntimeError("Unexpected official wheel URL or SHA256")
                if entry["yanked"] or entry["size"] > 40*1024*1024:
                    raise RuntimeError("Yanked or oversized fixture wheel")
                destination = directory/filename
                selected = download if args.source == "pypi" else download.replace(
                    "https://files.pythonhosted.org/", "https://pypi.tuna.tsinghua.edu.cn/", 1)
                cached = destination.exists()
                if destination.exists():
                    if destination.stat().st_size != entry["size"] or hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
                        raise RuntimeError("Preserving existing mismatched fixture: "+str(destination))
                else:
                    staging = directory/(".download-"+str(uuid4()))
                    executable = shutil.which("curl.exe" if os.name == "nt" else "curl")
                    if not executable:
                        raise RuntimeError("Developer fixture download requires curl for a hard total timeout")
                    clean = {k: os.environ[k] for k in ["PATH", "SystemRoot", "WINDIR", "TEMP", "TMP"] if k in os.environ}
                    result = subprocess.run([executable, "--disable", "--silent", "--show-error", "--fail",
                        "--proxy", "", "--proto", "=https", "--connect-timeout", "10", "--max-time", "120",
                        "--max-filesize", str(40*1024*1024), "--output", str(staging), selected],
                        env=clean, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=130, check=False)
                    if result.returncode:
                        raise RuntimeError("Fixture download failed; partial file preserved: "+result.stderr[:4096].decode(errors="replace"))
                    if staging.stat().st_size != entry["size"] or hashlib.sha256(staging.read_bytes()).hexdigest() != expected:
                        raise RuntimeError("Wheel size/SHA mismatch; partial file preserved")
                    os.replace(staging, destination)
                record = {"name": name, "version": version, "filename": filename, "url": download,
                    "sha256": expected, "size_bytes": entry["size"], "metadata_url": url,
                    "metadata_snapshot": str(metadata), "metadata_sha256": hashlib.sha256(response.content).hexdigest(),
                    "requested_download_source": args.source, "download_source": None if cached else args.source,
                    "download_url": None if cached else selected,
                    "acquisition": "existing_file_verified" if cached else "https_download_verified",
                    "path": str(destination), "observed_at": now()}
                records.append(record)
                print(json.dumps({"filename": filename, "sha256": expected, "size_bytes": entry["size"]}), flush=True)
    path = directory/("fixture-lock-"+str(uuid4())+".json")
    path.write_text(json.dumps({"kind": "developer_public_fixture_download", "agent_download": False,
        "package_execution": False, "wheels": records}, indent=2), encoding="utf-8")
    print(path, flush=True)


if __name__ == "__main__":
    main()
