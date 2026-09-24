"""Contract tests for reading the deployment's ``.env`` from a host process."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.env_file import load_env_file, parse_env  # noqa: E402


class ParseEnvTests(unittest.TestCase):
    def test_parses_the_syntax_a_deployment_file_uses(self):
        parsed = parse_env(
            "# a comment\n"
            "\n"
            "RUNTIME_API_KEY=abcdef0123456789\n"
            "UI_SESSION_KEY='quoted value'\n"
            'OLLAMA_URL="http://127.0.0.1:11434"\n'
            "export HOST_BRIDGE_KEY=exported\n"
            "EMPTY=\n"
            "NOT_AN_ASSIGNMENT\n"
        )
        self.assertEqual(parsed["RUNTIME_API_KEY"], "abcdef0123456789")
        self.assertEqual(parsed["UI_SESSION_KEY"], "quoted value")
        self.assertEqual(parsed["OLLAMA_URL"], "http://127.0.0.1:11434")
        self.assertEqual(parsed["HOST_BRIDGE_KEY"], "exported")
        self.assertEqual(parsed["EMPTY"], "")
        self.assertNotIn("NOT_AN_ASSIGNMENT", parsed)

    def test_a_value_containing_equals_is_kept_whole(self):
        """A session key or URL may contain ``=``; splitting on all of them corrupts it."""
        parsed = parse_env("UI_SESSION_KEY=abc=def==\n")
        self.assertEqual(parsed["UI_SESSION_KEY"], "abc=def==")


class LoadEnvFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".env"
        self.path.write_text(
            "RUNTIME_API_KEY=from-file\nOTHER_KEY=other\n", encoding="utf-8"
        )

    def test_a_missing_file_is_not_an_error(self):
        """The runner must work with no deployment file; it just has no credentials."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_env_file(Path(self.temp.name) / "absent.env"), {})
            self.assertNotIn("RUNTIME_API_KEY", os.environ)

    def test_declared_values_reach_the_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            declared = load_env_file(self.path)
            self.assertEqual(declared["RUNTIME_API_KEY"], "from-file")
            self.assertEqual(os.environ["RUNTIME_API_KEY"], "from-file")
            self.assertEqual(os.environ["OTHER_KEY"], "other")

    def test_an_explicit_environment_value_wins(self):
        """An override passed on the command line must not be replaced by the file."""
        with patch.dict(os.environ, {"RUNTIME_API_KEY": "explicit"}, clear=True):
            load_env_file(self.path)
            self.assertEqual(os.environ["RUNTIME_API_KEY"], "explicit")

    def test_override_replaces_it_when_asked(self):
        with patch.dict(os.environ, {"RUNTIME_API_KEY": "explicit"}, clear=True):
            load_env_file(self.path, override=True)
            self.assertEqual(os.environ["RUNTIME_API_KEY"], "from-file")

    def test_a_byte_order_mark_does_not_hide_the_first_key(self):
        self.path.write_bytes(b"\xef\xbb\xbfRUNTIME_API_KEY=with-bom\n")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_env_file(self.path)["RUNTIME_API_KEY"], "with-bom")


class RunnerCredentialTests(unittest.TestCase):
    def test_the_runner_loads_the_deployment_file(self):
        """Importing the runner must make the credential available to collection.

        This is the regression: the agent track raised "RUNTIME_API_KEY is required"
        even on a correctly deployed machine, because the host-side runner never read
        the file Compose uses.

        The module is reloaded on purpose. Another test has usually imported it
        already, and a plain second import is a no-op -- which made this test pass
        alone and fail in the full suite.
        """
        import evaluation.run_baseline as run_baseline

        env_file = ROOT / ".env"
        if not env_file.is_file():
            self.skipTest("no deployment file in this checkout")
        with patch.dict(os.environ, {}, clear=True):
            importlib.reload(run_baseline)
            self.assertTrue(
                os.environ.get("RUNTIME_API_KEY"),
                "the deployment declares a key but the runner did not load it",
            )
        importlib.reload(run_baseline)  # leave the module consistent for other tests

    def test_the_dashboard_can_answer_preflight_without_a_credential(self):
        from evaluation import dashboard

        with patch.dict(os.environ, {}, clear=True):
            message = dashboard.preflight(
                ["agent"], ROOT / "evaluation" / "work" / "score-now"
            )
        self.assertIsNotNone(message)
        self.assertIn("RUNTIME_API_KEY", message)


if __name__ == "__main__":
    unittest.main()
