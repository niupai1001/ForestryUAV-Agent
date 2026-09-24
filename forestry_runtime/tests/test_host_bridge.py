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

    def test_install_command_installs_system_packages_before_python_ones(self):
        command = dependency_install_command(["rasterio"], ["libexpat1"])
        script = command[2]
        self.assertEqual(json.loads(command[3]), ["libexpat1"])
        self.assertIn("apt-get", script)
        self.assertIn("--no-install-recommends", script)
        # Order matters: the shared library has to be present before pip resolves a
        # wheel that links against it.
        self.assertLess(script.index("apt-get"), script.index("pip"))
        self.assertIn("SYSTEM_PACKAGES=", script)

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
