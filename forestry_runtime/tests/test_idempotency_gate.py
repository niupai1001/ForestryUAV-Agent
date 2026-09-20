"""Fault-injected persistence probe used by the engineering evaluation collector."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest

from runtime.run_store import RunStore


class IdempotencyGateTests(unittest.TestCase):
    def test_exactly_once_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory))
            run = store.create(
                "gate-owner", "gate-chat",
                [{"role": "user", "content": "once", "input_id": "input_gate_once"}],
                [], True,
            )
            duplicate = store.create(
                "gate-owner", "gate-chat",
                [{"role": "user", "content": "once", "input_id": "input_gate_once"}],
                [], True,
            )
            self.assertEqual(duplicate["id"], run["id"])
            turn = store.claim_turn(run["id"])

            with store.db() as conn:
                conn.execute("""CREATE TRIGGER reject_gate_event BEFORE INSERT ON events
                    BEGIN SELECT RAISE(ABORT, 'injected reservation failure'); END""")
            with self.assertRaises(Exception):
                store.begin_action(
                    run["id"], turn["id"], "action_gate_rollback", "code_run", {}
                )
            with store.db() as conn:
                conn.execute("DROP TRIGGER reject_gate_event")

            prepared = store.begin_action(
                run["id"], turn["id"], "action_gate_once", "code_run", {"code": "pass"}
            )
            store.mark_attempt_started(prepared["attempt_id"])
            result = {"ok": True, "data": {
                "job_id": "job_gate_once", "job_type": "code", "state": "succeeded"
            }}
            store.finish_action(
                run["id"], turn["id"], "action_gate_once", "code_run",
                result, 0.01, prepared["attempt_id"],
            )
            replay = store.begin_action(
                run["id"], turn["id"], "action_gate_once", "code_run", {"code": "pass"}
            )
            self.assertFalse(replay["execute"])
            self.assertEqual(replay["result"], result)

            target = os.environ.get("EVALUATION_IDEMPOTENCY_DB")
            if target:
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(store.database, target)


if __name__ == "__main__":
    unittest.main()
