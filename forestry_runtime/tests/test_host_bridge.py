import json
import hashlib
import hmac
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from host_bridge.server import Service, dependency_install_command


class HostBridgeTests(unittest.TestCase):
    def test_revoked_grant_is_rejected_even_with_a_valid_old_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            source = data_root / "source"
            source.mkdir()
            grant_id = "grant_" + "a" * 32
            chat_id = "chat"
            key = "k" * 32
            database = data_root / "workspaces.sqlite3"
            conn = sqlite3.connect(database)
            try:
                conn.execute(
                    "CREATE TABLE grants(id TEXT PRIMARY KEY, chat_id TEXT, host_path TEXT, access TEXT, active INTEGER)"
                )
                conn.execute(
                    "INSERT INTO grants VALUES(?,?,?,?,1)",
                    (grant_id, chat_id, str(source.resolve()), "read"),
                )
                conn.commit()
            finally:
                conn.close()
            message = f"{chat_id}\n{source.resolve()}\nread".encode("utf-8")
            body = {
                "grant_id": grant_id, "chat_id": chat_id,
                "root": str(source.resolve()), "access": "read",
                "grant_token": hmac.new(
                    key.encode("utf-8"), message, hashlib.sha256
                ).hexdigest(),
            }
            service = object.__new__(Service)
            service.key = key
            service.data_root = data_root
            self.assertEqual(service.verify_grant(body), source.resolve())
            conn = sqlite3.connect(database)
            try:
                conn.execute("UPDATE grants SET active=0 WHERE id=?", (grant_id,))
                conn.commit()
            finally:
                conn.close()
            with self.assertRaisesRegex(PermissionError, "revoked"):
                service.verify_grant(body)

    def test_install_command_records_resolved_dependency_versions(self):
        command = dependency_install_command(["pandas==2.3.2"])
        self.assertEqual(command[:2], ["python", "-c"])
        self.assertIn("installed-packages.json", command[2])
        # The packages travel as JSON now, because the same command also carries the
        # system packages: a wheel can need a shared library pip cannot supply, and
        # both kinds have to be installed in one job so one failure leaves one record.
        self.assertEqual(json.loads(command[3]), [])
        self.assertEqual(json.loads(command[4]), ["pandas==2.3.2"])

    def test_install_command_can_report_plan_before_running_pip(self):
        command = dependency_install_command([])
        # Run the generated program with no requested packages. This catches
        # missing imports in the preamble before any apt/pip side effects.
        with tempfile.TemporaryDirectory() as root:
            command[2] = command[2].replace("target = '/deps'", "target = " + repr(root))
            completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("RUNTIME_INSTALL_PLAN=", completed.stdout)

    def test_terminal_status_is_saved_and_container_removed(self):
        job_id = "job_" + "a" * 32
        name = "forestry-" + job_id
        calls = []
        info = [{"Id": "c" * 64, "State": {"Running": False, "ExitCode": 1},
                 "Config": {"Labels": {"forestry.runtime.kind": "install"}}}]

        def fake_docker(*args, **kwargs):
            calls.append(args)
            if args[0] == "inspect":
                return subprocess.CompletedProcess(args, 0, json.dumps(info), "")
            if args[0] == "logs":
                return subprocess.CompletedProcess(args, 0, "failed", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        with tempfile.TemporaryDirectory() as root:
            service = object.__new__(Service)
            service.data_root = Path(root)
            service._committed = {}
            with patch("host_bridge.server.docker", side_effect=fake_docker):
                first = service.job_status({"job_id": job_id})
                again = service.job_status({"job_id": job_id})
            self.assertEqual(first, again)
            self.assertEqual(first["state"], "failed")
            self.assertIn(("rm", name), calls)
            self.assertEqual(sum(1 for call in calls if call[0] == "inspect"), 1)
            self.assertFalse(any(call[0] == "commit" for call in calls))

    def test_install_command_installs_system_packages_before_python_ones(self):
        command = dependency_install_command(["rasterio"], ["libexpat1"])
        script = command[2]
        self.assertEqual(json.loads(command[3]), ["libexpat1"])
        self.assertIn("apt-get", script)
        self.assertIn("--no-install-recommends", script)
        # Order matters: the shared library has to be present before pip resolves a
        # wheel that links against it. Compared on the two install commands rather
        # than on the bare substrings, because the script also names the pip cache
        # directory before either call.
        self.assertLess(
            script.index("'apt-get', 'install'"),
            script.index("'-m', 'pip', 'install'"),
        )
        self.assertIn("SYSTEM_PACKAGES=", script)

    def test_install_scratch_is_disk_backed_and_reported(self):
        """A hard tmpfs was the capacity a real install actually ran out of."""
        service = object.__new__(Service)
        service.cpu, service.memory, service.pids = "4", "6g", "256"
        service.code_tmp_bytes = 2 * 1024 * 1024 * 1024
        install = service._container_args(
            "job_" + "a" * 32, [], "bridge", "chat", 600, workdir=False, kind="install",
        )
        joined = " ".join(install)
        # The install job's /tmp comes from the mount table (a real directory), so no
        # size-limited tmpfs is declared for it here.
        self.assertNotIn("size=536870912", joined)
        self.assertIn("TMPDIR=/scratch/tmp", install)
        self.assertIn("PIP_CACHE_DIR=/scratch/pip-cache", install)
        self.assertIn("HOME=/scratch/home", install)

        code = service._container_args(
            "job_" + "b" * 32, [], "none", "chat", 600, workdir=True, kind="python",
        )
        self.assertIn("size=%d" % service.code_tmp_bytes, " ".join(code))
        self.assertIn("RUNTIME_SCRATCH=/scratch", code)

    def test_install_command_accepts_a_system_only_request(self):
        command = dependency_install_command([], ["libexpat1"])
        self.assertIn("apt-get", command[2])
        self.assertEqual(json.loads(command[4]), [])

    def test_watchdog_stops_expired_managed_container_without_client_poll(self):
        calls = []

        def fake_docker(*args, **kwargs):
            calls.append(args)
            if args[0] == "ps":
                return subprocess.CompletedProcess(args, 0, "forestry-job_deadline\n", "")
            if args[0] == "inspect":
                value = [{
                    "Config": {"Labels": {"forestry.runtime.deadline": "99"}},
                    "State": {"Running": True},
                }]
                return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
            return subprocess.CompletedProcess(args, 0, "", "")

        service = object.__new__(Service)
        with patch("host_bridge.server.docker", side_effect=fake_docker), patch("host_bridge.server.time.time", return_value=100):
            self.assertEqual(service.enforce_deadlines(), 1)
        self.assertIn(("stop", "--time", "10", "forestry-job_deadline"), calls)


if __name__ == "__main__":
    unittest.main()
