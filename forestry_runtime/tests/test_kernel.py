import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime.agent import _tools
from runtime.kernel.protocol import failure_category, inline_schema as kernel_inline_schema
from runtime.kernel.registry import build_registry
from runtime.kernel.spec import Scope, SideEffect, ToolSpec
from runtime.run_store import RunStore
from runtime.storage import AssetError
from runtime.tool_protocol import inline_schema as compatibility_inline_schema
from runtime.capabilities.runtime import GENERIC_DEFINITIONS, _REGISTRY, _SPECS


EXPECTED_CORE_NAMES = (
    "fs_list", "fs_read", "fs_search", "fs_write", "fs_edit",
    "code_run", "dependency_install", "job_status", "job_cancel",
    "tool_result_read", "artifacts_inspect", "artifacts_preview",
    "knowledge_search", "knowledge_read",
)
EXPECTED_LEGACY_CONTRACT_SHA256 = (
    "3f13edae22f3d0edf3f22863a5bf19621edb34ffde47ef29453e1d18e5213cd2"
)
EXPECTED_VISIBLE_SCHEMA_CHARS = 7506


class KernelContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runs = RunStore(self.root)
        self.run = self.runs.create(
            "alice", "chat-kernel",
            [{"role": "user", "content": "test", "input_id": "input-kernel"}],
            [], True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_tool_protocol_compatibility_and_registry_contract(self):
        """Regression coverage for kernel code that existed before this TDD cycle."""
        self.assertIs(compatibility_inline_schema, kernel_inline_schema)
        self.assertEqual(tuple(GENERIC_DEFINITIONS), EXPECTED_CORE_NAMES)
        self.assertEqual(_REGISTRY.as_legacy_definitions(), GENERIC_DEFINITIONS)
        self.assertEqual(len(_SPECS), 14)
        self.assertTrue(all(isinstance(spec, ToolSpec) for spec in _SPECS))

        payload = [
            {
                "name": name,
                "model": model.__name__,
                "description": description,
                "schema": kernel_inline_schema(model.model_json_schema()),
            }
            for name, (model, description) in GENERIC_DEFINITIONS.items()
        ]
        digest = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest()
        self.assertEqual(digest, EXPECTED_LEGACY_CONTRACT_SHA256)

        _, visible_count, visible_schema_chars = _tools(True)
        self.assertEqual(visible_count, 14)
        self.assertEqual(visible_schema_chars, EXPECTED_VISIBLE_SCHEMA_CHARS)

    def test_failure_taxonomy_only_maps_observable_categories(self):
        self.assertEqual(
            failure_category({"stage": "arguments", "code": "invalid_arguments"}),
            "tool_protocol",
        )
        self.assertEqual(
            failure_category({"stage": "execution", "code": "ValueError"}),
            "algorithm_numeric",
        )
        self.assertEqual(
            failure_category({"stage": "unexpected", "code": "mystery"}),
            "unknown",
        )

    def test_registry_rejects_duplicate_names(self):
        """Regression coverage for registry code that existed before this TDD cycle."""
        spec = ToolSpec(
            name="duplicate", description="test", params=next(iter(GENERIC_DEFINITIONS.values()))[0],
            returns="EvidenceArtifact", scope=Scope(), side_effect=SideEffect.NONE,
            equivalence_group="duplicate",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate tool specification"):
            build_registry((spec, spec))

    def test_registry_install_wins_lazy_init_race_and_tools_import(self):
        script = r'''
import json
import threading
import runtime.capabilities as capabilities
from runtime.capabilities.core_specs import load_specs
from runtime.kernel.registry import (
    build_registry, install_runtime_registry, runtime_registry,
)
from runtime.kernel.spec import Scope, SideEffect, ToolSpec

core = load_specs()
extension = ToolSpec(
    name="extension_tool", description="extension", params=core[0].params,
    returns="EvidenceArtifact", scope=Scope(), side_effect=SideEffect.NONE,
    equivalence_group="extension",
)
extended = build_registry((*core, extension))
entered = threading.Event()
release = threading.Event()
original = capabilities.load_specs

def blocked_load():
    entered.set()
    release.wait(5)
    return original()

capabilities.load_specs = blocked_load
thread = threading.Thread(target=runtime_registry)
thread.start()
if not entered.wait(5):
    raise RuntimeError("lazy registry loader did not start")
installer = threading.Thread(target=install_runtime_registry, args=(extended,))
installer.start()
release.set()
thread.join(5)
installer.join(5)
race_preserved = runtime_registry().get("extension_tool") is extension

capabilities.load_specs = original
install_runtime_registry(extended)
import runtime.capabilities
tools_preserved = runtime_registry().get("extension_tool") is extension
print(json.dumps({
    "race_preserved": race_preserved,
    "tools_preserved": tools_preserved,
}))
'''
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).parents[1],
            text=True, capture_output=True, timeout=20, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "race_preserved": True,
            "tools_preserved": True,
        })

    def test_run_store_resolves_core_specs_without_tools_import_side_effects(self):
        script = r'''
import json
import sys
import tempfile
from runtime.run_store import RunStore

with tempfile.TemporaryDirectory() as root:
    runs = RunStore(root)
    run = runs.create(
        "alice", "isolated-chat",
        [{"role": "user", "content": "test", "input_id": "isolated-input"}],
        [], True,
    )
    turn = runs.claim_turn(run["id"])
    runs.begin_action(
        run["id"], turn["id"], "isolated-begin", "fs_list", {"path": "."}
    )
    runs.append(run["id"], {
        "type": "tool_start", "action_id": "isolated-append",
        "name": "code_run", "arguments": {
            "language": "python", "code": "print(1)",
        },
    })
    events = [
        event for event in runs.events_after(run["id"], "alice")
        if event["type"] == "tool_start"
    ]
    print(json.dumps({
        "begin_spec": events[0].get("spec"),
        "append_spec": events[1].get("spec"),
        "tools_loaded": "runtime.tools" in sys.modules,
        "pil_loaded": "PIL" in sys.modules,
        "remote_sensing_loaded": "runtime.remote_sensing" in sys.modules,
    }))
'''
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).parents[1],
            text=True, capture_output=True, check=True,
        )
        observed = json.loads(result.stdout)
        self.assertEqual(observed["begin_spec"]["equivalent"], "inspect_filesystem")
        self.assertEqual(observed["append_spec"]["equivalent"], "execute_code")
        self.assertFalse(observed["tools_loaded"])
        self.assertFalse(observed["pil_loaded"])
        self.assertFalse(observed["remote_sensing_loaded"])

    def test_begin_action_tool_start_contains_spec_summary(self):
        turn = self.runs.claim_turn(self.run["id"])
        self.runs.begin_action(
            self.run["id"], turn["id"], "call-begin", "fs_list", {"path": "."}
        )

        event = self.runs.events_after(self.run["id"], "alice")[-1]
        self.assertEqual(event["name"], "fs_list")
        self.assertEqual(event["arguments"], {"path": "."})
        self.assertEqual(event["spec"]["side_effect"], "none")
        self.assertEqual(event["spec"]["equivalent"], "inspect_filesystem")
        self.assertEqual(event["spec"]["plugin"], "core")
        self.assertEqual(
            event["spec"]["scope"],
            {
                "reads": ["workspace", "source"], "writes": [],
                "network": "none", "host_path_args": ["path"],
            },
        )

    def test_append_tool_events_create_and_finish_corresponding_attempt(self):
        self.runs.append(self.run["id"], {
            "type": "tool_start", "action_id": "call-append",
            "name": "code_run", "arguments": {"language": "python", "code": "print(1)"},
        })
        start = self.runs.events_after(self.run["id"], "alice")[-1]
        self.assertTrue(start["attempt_id"].startswith("attempt_"))
        self.assertEqual(start["spec"]["side_effect"], "durable_job")
        self.assertEqual(start["spec"]["equivalent"], "execute_code")

        result = {"ok": True, "data": {"value": 1}}
        self.runs.append(self.run["id"], {
            "type": "tool_end", "action_id": "call-append",
            "name": "code_run", "ok": True, "result": result,
        })
        end = self.runs.events_after(self.run["id"], "alice")[-1]
        self.assertEqual(end["attempt_id"], start["attempt_id"])
        with self.runs.db() as conn:
            attempt = conn.execute(
                "SELECT state,operation_started,result_json FROM attempts WHERE id=?",
                (start["attempt_id"],),
            ).fetchone()
        self.assertEqual(attempt["state"], "succeeded")
        self.assertEqual(attempt["operation_started"], 1)
        self.assertEqual(json.loads(attempt["result_json"]), result)

    def test_append_tool_start_rejects_reused_action_with_different_owner_or_contract(self):
        turn = self.runs.claim_turn(self.run["id"])
        self.runs.begin_action(
            self.run["id"], turn["id"], "owned-action", "fs_list", {"path": "."}
        )
        other = self.runs.create(
            "alice", "chat-kernel-other",
            [{"role": "user", "content": "other", "input_id": "input-other"}],
            [], True,
        )
        conflicts = (
            (other["id"], "fs_list", {"path": "."}),
            (self.run["id"], "fs_read", {"path": "."}),
            (self.run["id"], "fs_list", {"path": "different"}),
        )
        for run_id, name, arguments in conflicts:
            with self.subTest(run_id=run_id, name=name, arguments=arguments):
                with self.assertRaises(AssetError):
                    self.runs.append(run_id, {
                        "type": "tool_start", "action_id": "owned-action",
                        "name": name, "arguments": arguments,
                    })
        foreign = self.runs.begin_action(
            self.run["id"], turn["id"], "foreign-start", "fs_list", {"path": "."}
        )
        with self.assertRaises(AssetError):
            self.runs.append(self.run["id"], {
                "type": "tool_start", "action_id": "owned-action",
                "attempt_id": foreign["attempt_id"],
                "name": "fs_list", "arguments": {"path": "."},
            })

        with self.runs.db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM attempts WHERE action_id='owned-action'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM events WHERE event_json LIKE '%owned-action%'"
                ).fetchone()[0],
                1,
            )

    def test_tool_end_rejects_invalid_attempt_without_partial_updates(self):
        cases = (
            ("append", "foreign", False),
            ("append", "attempt_missing", False),
            ("finish", "foreign", False),
            ("finish", "attempt_missing", False),
            ("append", "foreign", True),
            ("finish", "foreign", True),
        )
        for index, (path, attempt_kind, unknown_action) in enumerate(cases):
            with self.subTest(
                path=path, attempt_kind=attempt_kind,
                unknown_action=unknown_action,
            ):
                run = self.runs.create(
                    "alice", f"attempt-chat-{index}",
                    [{
                        "role": "user", "content": "attempt",
                        "input_id": f"attempt-input-{index}",
                    }],
                    [], True,
                )
                turn = self.runs.claim_turn(run["id"])
                action_id = f"attempt-action-{index}"
                prepared = self.runs.begin_action(
                    run["id"], turn["id"], action_id, "fs_list", {"path": "."}
                )
                foreign = self.runs.begin_action(
                    run["id"], turn["id"], f"foreign-action-{index}",
                    "fs_list", {"path": "."},
                )
                attempt_id = (
                    foreign["attempt_id"]
                    if attempt_kind == "foreign" else "attempt_missing"
                )
                ending_action = "unknown-action" if unknown_action else action_id
                result = {"ok": True, "data": {}}
                with self.assertRaises(AssetError):
                    if path == "append":
                        self.runs.append(run["id"], {
                            "type": "tool_end", "action_id": ending_action,
                            "attempt_id": attempt_id, "name": "fs_list",
                            "ok": True, "result": result,
                        })
                    else:
                        self.runs.finish_action(
                            run["id"], turn["id"], ending_action, "fs_list",
                            result, 0.1, attempt_id,
                        )
                with self.runs.db() as conn:
                    action = conn.execute(
                        "SELECT state,result_json FROM actions WHERE id=?",
                        (action_id,),
                    ).fetchone()
                    attempt = conn.execute(
                        "SELECT state,result_json FROM attempts WHERE id=?",
                        (prepared["attempt_id"],),
                    ).fetchone()
                    end_count = conn.execute(
                        """SELECT COUNT(*) FROM events WHERE run_id=?
                        AND event_json LIKE '%\"type\": \"tool_end\"%'""",
                        (run["id"],),
                    ).fetchone()[0]
                self.assertEqual((action["state"], action["result_json"]), ("running", None))
                self.assertEqual((attempt["state"], attempt["result_json"]), ("prepared", None))
                self.assertEqual(end_count, 0)

    def test_tool_end_without_attempt_id_resolves_owned_attempt_on_both_paths(self):
        for index, path in enumerate(("append", "finish")):
            with self.subTest(path=path):
                run = self.runs.create(
                    "alice", f"resolve-chat-{index}",
                    [{
                        "role": "user", "content": "resolve",
                        "input_id": f"resolve-input-{index}",
                    }],
                    [], True,
                )
                turn = self.runs.claim_turn(run["id"])
                action_id = f"resolve-action-{index}"
                prepared = self.runs.begin_action(
                    run["id"], turn["id"], action_id, "fs_list", {"path": "."}
                )
                result = {"ok": True, "data": {}}
                if path == "append":
                    self.runs.append(run["id"], {
                        "type": "tool_end", "action_id": action_id,
                        "name": "fs_list", "ok": True, "result": result,
                    })
                else:
                    self.runs.finish_action(
                        run["id"], turn["id"], action_id, "fs_list",
                        result, 0.1,
                    )
                event = self.runs.events_after(run["id"], "alice")[-1]
                self.assertEqual(event["attempt_id"], prepared["attempt_id"])
                with self.runs.db() as conn:
                    attempt = conn.execute(
                        "SELECT state,result_json FROM attempts WHERE id=?",
                        (prepared["attempt_id"],),
                    ).fetchone()
                self.assertEqual(attempt["state"], "succeeded")
                self.assertEqual(json.loads(attempt["result_json"]), result)

    def test_action_arguments_use_one_canonical_json_encoding_on_both_paths(self):
        turn = self.runs.claim_turn(self.run["id"])
        first = self.runs.begin_action(
            self.run["id"], turn["id"], "canonical-begin", "fs_list",
            {"path": ".", "page": 1},
        )
        self.runs.append(self.run["id"], {
            "type": "tool_start", "action_id": "canonical-begin",
            "name": "fs_list", "arguments": {"path": ".", "page": 1},
        })
        self.runs.append(self.run["id"], {
            "type": "tool_start", "action_id": "canonical-append",
            "name": "fs_list", "arguments": {"path": ".", "page": 1},
        })
        replay = self.runs.begin_action(
            self.run["id"], turn["id"], "canonical-append", "fs_list",
            {"page": 1, "path": "."},
        )

        self.assertFalse(replay["execute"])
        with self.runs.db() as conn:
            rows = conn.execute(
                """SELECT id,arguments_json FROM actions
                WHERE id IN ('canonical-begin','canonical-append') ORDER BY id"""
            ).fetchall()
            attempts = conn.execute(
                """SELECT action_id,COUNT(*) AS count FROM attempts
                WHERE action_id IN ('canonical-begin','canonical-append')
                GROUP BY action_id ORDER BY action_id"""
            ).fetchall()
        expected = json.dumps(
            {"path": ".", "page": 1}, ensure_ascii=False, sort_keys=True
        )
        self.assertEqual([row["arguments_json"] for row in rows], [expected, expected])
        self.assertEqual([(row["action_id"], row["count"]) for row in attempts], [
            ("canonical-append", 1), ("canonical-begin", 1),
        ])
        self.assertTrue(first["execute"])

    def test_jsonl_export_failure_does_not_reverse_committed_sqlite_events(self):
        turn = self.runs.claim_turn(self.run["id"])
        with patch.object(
            self.runs, "_write_log", side_effect=OSError("disk full")
        ), self.assertLogs("runtime.run_store", level="ERROR") as captured:
            decision = self.runs.begin_action(
                self.run["id"], turn["id"], "log-action", "fs_list", {"path": "."}
            )
            seq = self.runs.append(
                self.run["id"], {"type": "message", "content": "still readable"},
                turn["id"],
            )

        self.assertTrue(decision["execute"])
        events = self.runs.events_after(self.run["id"], "alice")
        self.assertEqual([event["seq"] for event in events], [1, seq])
        self.assertEqual(events[-1]["content"], "still readable")
        self.assertTrue(any("JSONL" in message for message in captured.output))

    def test_append_tool_end_uses_explicit_outcome_precedence(self):
        cases = (
            ({"outcome_ok": True, "ok": False}, {"outcome_ok": False, "ok": False}, "succeeded"),
            ({"ok": False}, {"outcome_ok": True, "ok": False}, "succeeded"),
            ({"ok": True}, {"ok": False}, "succeeded"),
            ({}, {"ok": True}, "succeeded"),
            ({"ok": False}, {"ok": True}, "failed"),
        )
        for index, (result_flags, event_flags, expected) in enumerate(cases):
            with self.subTest(
                result_flags=result_flags, event_flags=event_flags,
            ):
                run = self.runs.create(
                    "alice", f"outcome-chat-{index}",
                    [{
                        "role": "user", "content": "outcome",
                        "input_id": f"outcome-input-{index}",
                    }],
                    [], True,
                )
                self.runs.append(run["id"], {
                    "type": "tool_start", "action_id": f"outcome-action-{index}",
                    "name": "fs_list", "arguments": {"path": "."},
                })
                self.runs.append(run["id"], {
                    "type": "tool_end", "action_id": f"outcome-action-{index}",
                    "name": "fs_list", "result": {"data": {}, **result_flags},
                    **event_flags,
                })
                with self.runs.db() as conn:
                    action_state = conn.execute(
                        "SELECT state FROM actions WHERE id=?",
                        (f"outcome-action-{index}",),
                    ).fetchone()["state"]
                    attempt_state = conn.execute(
                        "SELECT state FROM attempts WHERE action_id=?",
                        (f"outcome-action-{index}",),
                    ).fetchone()["state"]
                self.assertEqual((action_state, attempt_state), (expected, expected))

    def test_run_state_and_user_message_add_trace_timestamps(self):
        self.runs.set_state(self.run["id"], "running")
        state_event = self.runs.events_after(self.run["id"], "alice")[-1]
        self.assertEqual(state_event["from_state"], "queued")
        self.assertEqual(state_event["state"], "running")

        self.runs.append(self.run["id"], {
            "type": "user_message", "content": "hello", "input_id": "input-next",
        })
        message_event = self.runs.events_after(self.run["id"], "alice")[-1]
        self.assertEqual(message_event["content"], "hello")
        self.assertIsInstance(message_event["received_at"], float)
        self.assertGreater(message_event["received_at"], 0)


if __name__ == "__main__":
    unittest.main()
