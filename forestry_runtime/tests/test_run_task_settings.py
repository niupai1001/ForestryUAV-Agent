"""The ``--settings`` flag must reach the trial's session, not die in argparse.

The flag used to be parsed and then dropped: ``main()`` never forwarded it to
``run_trial``, so an arm recorded "collected" while both arms ran the
deployment's own settings.  No override was ever written, the model never saw a
different window, and the comparison measured nothing -- the exact silent
failure this file pins down.  Together with the runtime-side fix (model-call
settings and the budget compactor now honour overrides instead of reading the
environment directly) this makes a controlled arm actually controlled.
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from evaluation.grounded_v1 import run_task


class SettingsForwardingTests(unittest.TestCase):
    """What main() promises on the command line, run_trial must receive."""

    def _run_main(self, *extra):
        captured = {}

        def fake_run_trial(**kwargs):
            captured.update(kwargs)
            return {"status": "evaluated", "package": "unused", "trace": None}

        argv = ["run_task.py", "--task", "oam-07", "--repeat", "9",
                "--no-grade", "--api-key", "test-key", *extra]
        with patch.object(sys, "argv", argv), \
                patch.object(run_task, "run_trial", side_effect=fake_run_trial):
            run_task.main()
        return captured

    def test_a_json_object_is_forwarded_to_the_trial(self):
        captured = self._run_main("--settings", '{"OLLAMA_CONTEXT": 8192}')
        self.assertEqual(captured["settings"], {"OLLAMA_CONTEXT": 8192})

    def test_configuration_records_the_arm(self):
        """The trace's configuration must carry the arm's parameters."""
        captured = self._run_main("--settings", '{"OLLAMA_CONTEXT": 8192}')
        self.assertEqual(captured["configuration"]["settings"],
                         {"OLLAMA_CONTEXT": 8192})

    def test_without_the_flag_no_settings_are_sent(self):
        captured = self._run_main()
        self.assertIsNone(captured["settings"])
        self.assertEqual(captured["configuration"]["settings"], {})

    def test_invalid_json_is_refused(self):
        with patch.object(sys, "argv", ["run_task.py", "--task", "oam-07",
                                        "--repeat", "9", "--no-grade",
                                        "--api-key", "test-key",
                                        "--settings", "{not json"]):
            with self.assertRaises(SystemExit) as raised:
                run_task.main()
        self.assertEqual(raised.exception.code, 2)

    def test_a_non_object_is_refused(self):
        with patch.object(sys, "argv", ["run_task.py", "--task", "oam-07",
                                        "--repeat", "9", "--no-grade",
                                        "--api-key", "test-key",
                                        "--settings", "[1, 2]"]):
            with self.assertRaises(SystemExit):
                run_task.main()

    def test_an_empty_object_is_refused(self):
        with patch.object(sys, "argv", ["run_task.py", "--task", "oam-07",
                                        "--repeat", "9", "--no-grade",
                                        "--api-key", "test-key",
                                        "--settings", "{}"]):
            with self.assertRaises(SystemExit):
                run_task.main()


if __name__ == "__main__":
    unittest.main()
