"""Prepare a separate project WSL runtime; never reset or unregister an existing distro.

This trusted developer setup program is not exposed as a generated-code tool.
Ubuntu's signature and fixed image digest are verified before importing the image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "workspace" / "isolated-executor"
NAME = "forestry-uav-agent-runtime"
IMAGE = "ubuntu-24.04.5-wsl-amd64.wsl"
BASE = "https://releases.ubuntu.com/noble/"
SHA256 = "bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e"
FINGERPRINT = "843938DF228D22F7B3742BC0D94AA3F0EFE21092"
CODE_IMAGE = "public.ecr.aws/docker/library/python@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f"


def emit(**value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def gpg_command():
    gpg = shutil.which("gpg") or "C:/Program Files/Git/usr/bin/gpg.exe"
    home = DIRECTORY / "gnupg"
    home.mkdir(exist_ok=True)
    # Git's MSYS GPG requires a POSIX absolute homedir, unlike native Gpg4win.
    gpg_home = "/" + home.drive[0].lower() + home.as_posix()[2:] if "git/usr/bin" in gpg.replace("\\", "/").lower() else str(home)
    return [gpg, "--homedir", gpg_home, "--batch", "--no-auto-key-retrieve"]


def verify_manifest():
    command = gpg_command()
    subprocess.run([*command, "--import", str(DIRECTORY / "ubuntu-signing-key.asc")],
        check=True, capture_output=True, timeout=30)
    verified = subprocess.run([*command, "--status-fd", "1", "--verify", str(DIRECTORY / "SHA256SUMS.gpg"),
        str(DIRECTORY / "SHA256SUMS")], check=True, capture_output=True, timeout=30)
    status = verified.stdout.decode("utf-8", errors="replace")
    if not any(line.startswith("[GNUPG:] VALIDSIG " + FINGERPRINT + " ") for line in status.splitlines()):
        raise RuntimeError("Publisher fingerprint did not match")
    if f"{SHA256} *{IMAGE}" not in (DIRECTORY / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        raise RuntimeError("Pinned image missing from signed publisher manifest")
    (DIRECTORY / "signature-status.txt").write_text(status, encoding="utf-8")


def download():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=45, trust_env=False, follow_redirects=False) as client:
        for name in ["SHA256SUMS", "SHA256SUMS.gpg"]:
            response = client.get(BASE + name)
            response.raise_for_status()
            if len(response.content) > 65536:
                raise RuntimeError("Oversized publisher metadata")
            (DIRECTORY / name).write_bytes(response.content)
        response = client.get("https://keyserver.ubuntu.com/pks/lookup", params={"op": "get", "search": "0x" + FINGERPRINT})
        response.raise_for_status()
        if len(response.content) > 65536:
            raise RuntimeError("Oversized signing key")
        key = DIRECTORY / "ubuntu-signing-key.asc"
        key.write_bytes(response.content)
        verify_manifest()
        target = DIRECTORY / IMAGE
        if not target.is_file() or digest(target) != SHA256:
            temporary = target.with_suffix(".partial")
            total = 0
            with client.stream("GET", BASE + IMAGE) as response, temporary.open("wb") as output:
                response.raise_for_status()
                for chunk in response.iter_bytes(1024 * 1024):
                    total += len(chunk)
                    if total > 512 * 1024 * 1024:
                        raise RuntimeError("Image exceeds download budget")
                    output.write(chunk)
                    if total % (32 * 1024 * 1024) == 0:
                        emit(phase="download", bytes=total)
            if digest(temporary) != SHA256:
                raise RuntimeError("Downloaded image digest mismatch; partial preserved")
            temporary.replace(target)
        manifest = {"schema_version": 1, "distribution": NAME, "install_directory": str(DIRECTORY / "wsl"),
            "image": IMAGE, "url": BASE + IMAGE, "sha256": SHA256, "bytes": target.stat().st_size,
            "signature_fingerprint": FINGERPRINT, "signature_verified": True, "image_verified": True,
            "executor_ready": False}
        (DIRECTORY / "image-lock.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        emit(phase="verified", **manifest)


def import_distribution():
    import winreg
    target, location = DIRECTORY / IMAGE, (DIRECTORY / "wsl").resolve()
    if not target.is_file() or digest(target) != SHA256:
        raise RuntimeError("Run signed download verification before importing")
    verify_manifest()
    lock = json.loads((DIRECTORY / "image-lock.json").read_text(encoding="utf-8"))
    if lock.get("signature_fingerprint") != FINGERPRINT or lock.get("signature_verified") is not True:
        raise RuntimeError("Verified publisher signature is required")
    if not location.is_relative_to(ROOT.resolve()) or location.is_symlink():
        raise RuntimeError("Install location must remain inside this project")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\Microsoft\\Windows\\CurrentVersion\\Lxss") as root:
        for index in range(winreg.QueryInfoKey(root)[0]):
            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as key:
                if winreg.QueryValueEx(key, "DistributionName")[0] == NAME:
                    actual = Path(winreg.QueryValueEx(key, "BasePath")[0].removeprefix("\\\\?\\")).resolve()
                    if actual != location:
                        raise RuntimeError("Same-name distribution is owned elsewhere; refusing to overwrite")
                    emit(phase="already_registered", distribution=NAME, location=str(actual))
                    return
    if location.exists() and any(location.iterdir()):
        raise RuntimeError("Nonempty import location is preserved; inspect before proceeding")
    location.mkdir(parents=True, exist_ok=True)
    emit(phase="import_started", distribution=NAME, location=str(location))
    result = subprocess.run(["wsl.exe", "--import", NAME, str(location), str(target), "--version", "2"],
        capture_output=True, timeout=300, check=False)
    (DIRECTORY / "import-stdout.bin").write_bytes(result.stdout)
    (DIRECTORY / "import-stderr.bin").write_bytes(result.stderr)
    emit(phase="import_returned", returncode=result.returncode)
    result.check_returncode()


def record_lock(config_path):
    from forestry_agent.config import load_config
    from forestry_agent.jobs import Docker
    from forestry_agent.store import now
    import_distribution()  # Rechecks signature, hash and registered project ownership.
    config = load_config(config_path)
    if config.docker_distribution != NAME:
        raise RuntimeError("Lock only the project-owned distribution")
    docker = Docker(config)
    version = docker.call("version", "--format", "{{json .}}")
    info = docker.call("info", "--format", "{{json .}}")
    image = docker.call("image", "inspect", config.code_image)
    if any(x["code"] for x in [version, info, image]):
        raise RuntimeError("Executor/image probe failed; readiness is not established")
    packages = subprocess.run(["wsl.exe", "--distribution", NAME, "--user", "root", "--exec", "/usr/bin/env",
        "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root", "dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"],
        check=True, capture_output=True, timeout=30)
    details = json.loads(image["stdout"])[0]
    inventory = dict(line.split("\t", 1) for line in packages.stdout.decode().splitlines())
    lock = {"schema_version": 1, "observed_at": now(), "distribution": NAME,
        "rootfs": json.loads((DIRECTORY / "image-lock.json").read_text()), "packages": inventory,
        "docker": json.loads(version["stdout"]), "daemon": json.loads(info["stdout"]),
        "image": {k: details[k] for k in ["Id", "RepoDigests", "Architecture", "Os", "Config", "RootFS"]},
        "code_image": config.code_image, "socket": config.docker_host,
        "daemon_available": True, "general_stage_accepted": False, "scientific_accuracy": "not_assessed"}
    output = DIRECTORY / "runtime-lock.json"
    output.write_text(json.dumps(lock, indent=2, ensure_ascii=False), encoding="utf-8")
    emit(phase="runtime_locked", output=str(output), image_id=details["Id"], daemon_available=True,
        general_stage_accepted=False)


def setup_guest(stage):
    from forestry_agent.jobs import Docker
    import_distribution()
    command = ["wsl.exe", "--distribution", NAME, "--user", "root", "--exec", "/usr/bin/env",
        "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "HOME=/root"]
    if stage == "install":
        command.extend(["/bin/sh", Docker.wsl_path(ROOT / "scripts" / "setup_wsl_executor.sh")])
    else:
        command.extend(["docker", "--config", Docker.wsl_path(DIRECTORY / "docker-config"),
            "--host", "unix:///var/run/docker.sock", "pull", CODE_IMAGE])
    subprocess.run(command, check=True, timeout=300)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["download", "import", "install", "pull", "lock"])
    parser.add_argument("--config", default="config/app.wsl.example.yaml")
    arguments = parser.parse_args()
    if arguments.stage == "download":
        download()
    elif arguments.stage == "import":
        import_distribution()
    elif arguments.stage == "lock":
        record_lock(arguments.config)
    else:
        setup_guest(arguments.stage)
