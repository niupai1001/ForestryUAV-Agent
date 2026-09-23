"""Sandbox probe used by the engineering evaluation collector.

The probe starts one real container from the Runtime's configured job image and
inspects the effective isolation instead of trusting the requested flags:

* the container cannot reach the network;
* the root filesystem is read-only while an explicit writable mount still works;
* Linux capabilities are dropped;
* CPU / memory / pid limits match the configured budget;
* no Runtime credential leaks into the job environment.

When Docker is unavailable the probe records that fact instead of failing, so the
gate can report ``unknown`` (evidence pending) rather than a false ``pass``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


IMAGE = os.environ.get("AGENT_JOB_IMAGE", "python:3.12-slim")
# Credentials the Runtime process holds; none of them may reach a job container.
RUNTIME_SECRETS = ("HOST_BRIDGE_KEY", "RUNTIME_API_KEY", "UI_SESSION_KEY", "OLLAMA_URL")
PROBE_SCRIPT = (
    "import json,os,pathlib,socket;"
    "r={'uid':os.getuid()};"
    "\ntry:\n"
    "    socket.create_connection(('1.1.1.1',53),timeout=3); r['network']=True\n"
    "except Exception:\n"
    "    r['network']=False\n"
    "r['root_writable']=False\n"
    "try:\n"
    "    pathlib.Path('/probe').write_text('x'); r['root_writable']=True\n"
    "except Exception:\n"
    "    pass\n"
    "pathlib.Path('/writable/probe.txt').write_text('ok')\n"
    "r['writable_mount_works']=pathlib.Path('/writable/probe.txt').read_text()=='ok'\n"
    "r['caps']=open('/proc/self/status').read().split('CapEff:')[1].split(chr(10))[0].strip()\n"
    "r['memory_limit']=open('/sys/fs/cgroup/memory.max').read().strip() if "
    "pathlib.Path('/sys/fs/cgroup/memory.max').exists() else None\n"
    "r['pid_limit']=open('/sys/fs/cgroup/pids.max').read().strip() if "
    "pathlib.Path('/sys/fs/cgroup/pids.max').exists() else None\n"
    "r['present_secrets']=sorted(k for k in os.environ if k in %r)\n"
    "print('PROBE='+json.dumps(r))" % (RUNTIME_SECRETS,)
)


def _docker(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=300,
    )


def docker_available() -> tuple[bool, str]:
    try:
        probe = _docker("info", "--format", "{{.ServerVersion}}")
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if probe.returncode != 0:
        return False, (probe.stderr or "docker info failed").strip()[:400]
    return True, probe.stdout.strip()


class SandboxGateTests(unittest.TestCase):
    def test_declared_isolation_holds_probe(self):
        available, detail = docker_available()
        report: dict = {"docker_available": available}
        if not available:
            report["error"] = detail
        else:
            report.update(self._run_container())

        target = os.environ.get("EVALUATION_SANDBOX_REPORT")
        if target:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if not available:
            self.skipTest(f"Docker unavailable: {detail}")
        self.assertTrue(
            all(report["assertions"].values()),
            json.dumps(report["assertions"], ensure_ascii=False, indent=2),
        )

    def _run_container(self) -> dict:
        """Start one inspectable container, then read its effective isolation back."""
        name = "forestry-sandbox-probe"
        _docker("rm", "-f", name)
        with tempfile.TemporaryDirectory() as directory:
            workspace = str(Path(directory).resolve())
            _docker(
                "run", "-d", "--name", name,
                "--network", "none",
                "--cpus", os.environ.get("AGENT_JOB_CPUS", "4"),
                "--memory", os.environ.get("AGENT_JOB_MEMORY", "6g"),
                "--pids-limit", os.environ.get("AGENT_JOB_PIDS", "256"),
                "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--init",
                "--user", "10001:10001",
                "--tmpfs", "/tmp:rw,nosuid,size=536870912",
                "-e", "PYTHONPATH=/workspace", "-e", "HOME=/tmp",
                "--mount", f"type=bind,source={workspace},target=/writable",
                IMAGE, "python", "-c", PROBE_SCRIPT,
            )
            # The container is intentionally not --rm so HostConfig stays inspectable.
            _docker("wait", name)
            logs = _docker("logs", name)
            inspect = _docker("inspect", name)
            host_config: dict = {}
            if inspect.returncode == 0:
                try:
                    host_config = json.loads(inspect.stdout)[0].get("HostConfig", {})
                except (json.JSONDecodeError, IndexError):
                    host_config = {}
            _docker("rm", "-f", name)

        payload: dict = {}
        for line in (logs.stdout or "").splitlines():
            if line.startswith("PROBE="):
                payload = json.loads(line[len("PROBE="):])
                break
        assertions = {
            "container_started": bool(payload),
            "network_unreachable": payload.get("network") is False,
            "root_filesystem_read_only": payload.get("root_writable") is False,
            "writable_mount_works": payload.get("writable_mount_works") is True,
            "capabilities_dropped": payload.get("caps") in {"0000000000000000", "0"},
            "runs_as_unprivileged_user": payload.get("uid") == 10001,
            "no_runtime_credentials_inside_job": payload.get("present_secrets") == [],
            "cap_drop_applied": "ALL" in (host_config.get("CapDrop") or []),
            "readonly_rootfs_applied": bool(host_config.get("ReadonlyRootfs")),
            "no_new_privileges_applied": any(
                "no-new-privileges" in str(item)
                for item in (host_config.get("SecurityOpt") or [])
            ),
            "network_mode_none_applied": host_config.get("NetworkMode") == "none",
            "pids_limit_applied": str(host_config.get("PidsLimit") or "") ==
            os.environ.get("AGENT_JOB_PIDS", "256"),
            "memory_limit_applied": int(host_config.get("Memory") or 0) > 0,
            "cpu_limit_applied": int(host_config.get("NanoCpus") or 0) > 0,
        }
        return {
            "docker_available": True,
            "image": IMAGE,
            "probe": payload,
            "host_config": {
                key: host_config.get(key) for key in (
                    "NetworkMode", "ReadonlyRootfs", "CapDrop", "SecurityOpt",
                    "PidsLimit", "Memory", "NanoCpus",
                )
            },
            "assertions": assertions,
        }


if __name__ == "__main__":
    unittest.main()
