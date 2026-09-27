"""Phase 1 acceptance: the execution environment is stated, measured and bounded.

Three claims are checked here, each of them a failure that was observed rather than
imagined:

* the environment probe runs the image a *code job* will run. Those differ as soon
  as an install commits an image, and a check that answers about a different image
  than the one that executes is worse than no check, because it looks like evidence.
* temporary space is a real directory on the host with a measured capacity, not a
  512 MiB tmpfs. The tmpfs was the capacity a real install ran out of, on a host
  with tens of gigabytes free.
* a capacity failure names the path that ran out and the quota it ran out of, so
  "the scratch disk inside the container is full" cannot be reported as "the
  requirement is unbuildable" or "the host disk is full".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from host_bridge.server import (
    CONTAINER_FACTS_MARKER,
    ENVIRONMENT_MARKER,
    BridgeError,
    Service,
    dependency_install_command,
    environment_probe_command,
)
from runtime.capabilities.environment.service import install_resource_evidence
from runtime.capabilities.runtime import RuntimeTools
from runtime.lifecycle import Sessions
from runtime.workspace import WorkspaceRegistry
from shared.job_diagnostics import parse_resource_failure


class _Docker:
    """Record the argv of every docker call and answer the ones a start needs."""

    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args and args[0] == "ps":
            return subprocess.CompletedProcess(list(args), 0, "", "")
        if args and args[0] == "run":
            return subprocess.CompletedProcess(list(args), 0, "container-id\n", "")
        return subprocess.CompletedProcess(list(args), 0, "", "")

    def run_argv(self) -> list[str]:
        for call in self.calls:
            if call and call[0] == "run":
                return list(call)
        raise AssertionError("no docker run was issued")


class ExecutionEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = Sessions(self.root)
        self.chat_id = str(uuid.uuid4())
        self.sessions.create("alice", self.chat_id)
        self.store = self.sessions.acquire("alice", self.chat_id)
        self.sessions.release(self.chat_id)
        self.registry = WorkspaceRegistry(self.root)
        self.workspace = self.registry.workspace(self.store)
        self.box = RuntimeTools(self.store, "alice", [], self.registry, "")

    def tearDown(self):
        self.temp.cleanup()

    def _service(self) -> Service:
        service = object.__new__(Service)
        service.key = "k" * 32
        service.data_root = self.root
        service.image = "python:3.12-slim"
        service.cpu, service.memory, service.pids = "4", "6g", "256"
        service.max_jobs = 1
        service.image_record_name = "job-image"
        service.job_lock = __import__("threading").RLock()
        service._committed = {}
        service.scratch_dir_name = "scratch"
        service.min_free_bytes = 512 * 1024 * 1024
        service.code_tmp_bytes = 2 * 1024 * 1024 * 1024
        return service

    # ------------------------------------------------------------ image identity

    def test_environment_probe_runs_the_image_code_jobs_will_use(self):
        service = self._service()
        record = self.workspace / ".runtime" / "job-image"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("forestry-job:abc123\n", encoding="utf-8")

        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = list(argv)
            facts = json.dumps({"python": "3.12.0", "space": {"/tmp": {"path": "/tmp"}}})
            payload = (
                f"\n{CONTAINER_FACTS_MARKER}{facts}\n"
                f"\n{ENVIRONMENT_MARKER}{json.dumps([])}\n"
            )
            return subprocess.CompletedProcess(list(argv), 0, payload, "")

        with patch("host_bridge.server.subprocess.run", side_effect=fake_run):
            report = service.job_run({"workspace": str(self.workspace), "modules": []})

        argv = captured["argv"]
        self.assertIn("forestry-job:abc123", argv)
        self.assertNotIn("python:3.12-slim", argv)
        self.assertEqual(report["image"], "forestry-job:abc123")
        self.assertEqual(report["base_image"], "python:3.12-slim")
        self.assertEqual(report["image_source"], "workspace-install")
        self.assertEqual(report["container"]["python"], "3.12.0")

    def test_environment_probe_reports_base_image_before_any_install(self):
        service = self._service()
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = list(argv)
            return subprocess.CompletedProcess(
                list(argv), 0, f"\n{ENVIRONMENT_MARKER}{json.dumps([])}\n", "",
            )

        with patch("host_bridge.server.subprocess.run", side_effect=fake_run):
            report = service.job_run({"workspace": str(self.workspace), "modules": []})
        self.assertIn("python:3.12-slim", captured["argv"])
        self.assertEqual(report["image_source"], "configured")

    # --------------------------------------------------------------- scratch

    def test_install_scratch_is_on_disk_and_mounted_at_tmp(self):
        service = self._service()
        docker = _Docker()
        with patch("host_bridge.server.docker", side_effect=docker):
            service.job_start({
                "workspace": str(self.workspace), "job_id": "job_" + "a" * 32,
                "kind": "install", "packages": ["rasterio"],
            })
        argv = " ".join(docker.run_argv())
        scratch = self.workspace / ".runtime" / "scratch"
        self.assertTrue(scratch.is_dir())
        self.assertIn(f"source={scratch}", argv)
        self.assertIn("target=/tmp", argv)
        self.assertIn("target=/scratch", argv)
        self.assertNotIn("size=536870912", argv)

    def test_started_job_reports_the_capacity_it_was_admitted_against(self):
        service = self._service()
        docker = _Docker()
        with patch("host_bridge.server.docker", side_effect=docker):
            result = service.job_start({
                "workspace": str(self.workspace), "job_id": "job_" + "b" * 32,
                "kind": "install", "packages": ["rasterio"],
            })
        scratch = result["scratch"]
        self.assertEqual(scratch["path"], str(self.workspace / ".runtime" / "scratch"))
        self.assertGreater(scratch["free_bytes"], 0)
        self.assertEqual(scratch["required_free_bytes"], service.min_free_bytes)

    def test_a_host_without_room_refuses_the_job_and_says_why(self):
        service = self._service()
        usage = shutil._ntuple_diskusage(total=10**9, used=10**9 - 10**7, free=10**7)
        with patch("host_bridge.server.shutil.disk_usage", return_value=usage):
            with self.assertRaises(BridgeError) as caught:
                service._check_disk_headroom(self.workspace, "install")
        error = caught.exception
        self.assertEqual(error.code, "disk_space_low")
        self.assertFalse(error.retryable)
        self.assertEqual(error.extra["free_bytes"], 10**7)
        self.assertEqual(error.extra["required_free_bytes"], service.min_free_bytes)
        self.assertIn("job scratch", str(error))

    # ------------------------------------------------------------- diagnostics

    def test_pip_errno_28_is_read_as_a_capacity_failure_with_its_path(self):
        log = (
            "Collecting rasterio\n"
            "ERROR: Could not install packages due to an OSError: "
            "[Errno 28] No space left on device: '/tmp/pip-build-4xq/rasterio'\n"
        )
        found = parse_resource_failure(log)
        self.assertEqual(found["kind"], "no_space_left_on_device")
        self.assertEqual(found["errno"], 28)
        self.assertEqual(found["exhausted_path"], "/tmp/pip-build-4xq/rasterio")

    def test_a_log_without_a_resource_statement_produces_no_diagnosis(self):
        self.assertIsNone(parse_resource_failure("Successfully installed rasterio"))
        self.assertIsNone(install_resource_evidence({"output": "Successfully installed"}))

    def test_install_failure_names_the_exhausted_path_and_its_capacity(self):
        observation = {
            "output": "ERROR: [Errno 28] No space left on device: '/scratch/tmp/pip-x'\n",
            "resource": {
                "kind": "no_space_left_on_device",
                "errno": 28,
                "exhausted_path": "/scratch/tmp/pip-x",
                "tmpdir": "/scratch/tmp",
                "container_space": {
                    "/scratch": {
                        "path": "/scratch", "total_bytes": 536870912,
                        "free_bytes": 0, "used_bytes": 536870912,
                    },
                },
            },
        }
        evidence = install_resource_evidence(observation)
        self.assertEqual(evidence["exhausted_path"], "/scratch/tmp/pip-x")
        self.assertIn("512.0 MiB", evidence["detail"])
        self.assertEqual(evidence["filesystems"][0]["total_human"], "512.0 MiB")

    def test_failed_install_reports_a_storage_code_not_a_generic_failure(self):
        from runtime.capabilities.environment.service import InstallNotVerified

        self.box.workspace = self.workspace
        observation = {
            "state": "failed", "terminal": True, "exit_code": 1,
            "output": "ERROR: [Errno 28] No space left on device: '/scratch/tmp/pip-x'\n",
            "resource": {
                "kind": "no_space_left_on_device", "errno": 28,
                "exhausted_path": "/scratch/tmp/pip-x",
                "container_space": {"/scratch": {
                    "path": "/scratch", "total_bytes": 268435456, "free_bytes": 0,
                    "used_bytes": 268435456}},
            },
        }
        with self.assertRaises(InstallNotVerified) as caught:
            self.box._finish_install("job_" + "c" * 32, ["rasterio"], ["rasterio"], observation)
        failure = caught.exception.failure_details
        self.assertEqual(failure["code"], "install_failed_no_space")
        self.assertEqual(failure["resource"]["exhausted_path"], "/scratch/tmp/pip-x")
        self.assertIn("256.0 MiB", failure["message"])

    def test_environment_check_states_image_dependencies_and_scratch(self):
        self.box.workspace = self.workspace
        probe = {
            "image": "forestry-job:abc", "base_image": "python:3.12-slim",
            "image_source": "workspace-install", "dependency_mount": "/deps",
            "pythonpath": "/workspace/.runtime/deps", "scratch_mount": "/scratch",
            "scratch_host_path": str(self.workspace / ".runtime" / "scratch"),
            "exit_code": 0, "modules": [],
            "space": [{"path": "/scratch", "total_bytes": 10**11, "free_bytes": 9 * 10**10,
                       "total_human": "93.1 GiB", "free_human": "83.8 GiB"}],
            "limits": {"cpu_count": 4, "cgroup_memory_bytes": 6442450944},
            "container": {"python": "3.12.11", "platform": "linux"},
        }
        with patch.object(type(self.box), "_probe_modules", return_value=probe):
            result = self.box.environment_check(modules=[])
        environment = result["execution_environment"]
        self.assertEqual(environment["job_image"], "forestry-job:abc")
        self.assertEqual(environment["image_source"], "workspace-install")
        self.assertEqual(environment["scratch_capacity"]["free_bytes"], 9 * 10**10)
        self.assertEqual(environment["limits"]["cgroup_memory_bytes"], 6442450944)

    # ----------------------------------------------------------------- staging

    @staticmethod
    def _grant_row(database: Path, grant_id: str, host: Path) -> None:
        conn = sqlite3.connect(database)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, owner TEXT NOT NULL,"
                " chat_id TEXT NOT NULL, host_path TEXT NOT NULL, access TEXT NOT NULL,"
                " basis TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,"
                " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                " UNIQUE(owner, chat_id, host_path, access))"
            )
            conn.execute(
                "INSERT OR REPLACE INTO grants(id,owner,chat_id,host_path,access,basis)"
                " VALUES(?,?,?,?,?,?)",
                (grant_id, "alice", "chat-1", str(host), "read", "user asked"),
            )
            conn.commit()
        finally:
            conn.close()

    def _staging_body(self, service, grant_id, host, path):
        body = {
            "grant_id": grant_id, "chat_id": "chat-1", "root": str(host), "access": "read",
            "path": path, "workspace": str(self.workspace),
        }
        body["grant_token"] = hmac.new(
            service.key.encode(), f"chat-1\n{host}\nread".encode(), hashlib.sha256,
        ).hexdigest()
        return body

    def test_a_granted_source_file_is_staged_into_the_workspace(self):
        service = self._service()
        host = self.root / "hostdata"
        host.mkdir()
        payload = b"II*\x00 staged-tiff-bytes"
        (host / "flight.tif").write_bytes(payload)
        grant_id = "grant_" + "d" * 32
        self._grant_row(self.root / "workspaces.sqlite3", grant_id, host)

        staged = service.fs_stage(self._staging_body(service, grant_id, host, "flight.tif"))
        target = self.workspace / staged["staged_path"]
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(staged["name"], "flight.tif")
        self.assertTrue(staged["read_only"])

        again = service.fs_stage(self._staging_body(service, grant_id, host, "flight.tif"))
        self.assertTrue(again["reused"])
        self.assertEqual(again["content_id"], staged["content_id"])

    def test_staging_refuses_a_path_outside_the_grant(self):
        service = self._service()
        host = self.root / "hostdata"
        host.mkdir()
        grant_id = "grant_" + "e" * 32
        self._grant_row(self.root / "workspaces.sqlite3", grant_id, host)
        with self.assertRaises(ValueError):
            service.fs_stage(self._staging_body(service, grant_id, host, "../outside.tif"))


if __name__ == "__main__":
    unittest.main()
