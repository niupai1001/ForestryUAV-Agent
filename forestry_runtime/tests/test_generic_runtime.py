import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from runtime.agent import _bounded_tool_result
from runtime.capabilities.domain_runtime import RemoteSensingTools
from runtime.capabilities.runtime import RuntimeTools
from runtime.lifecycle import Sessions
from runtime.memory import MemoryManager
from runtime.run_store import RunStore
from runtime.session.coordinator import RunCoordinator
from runtime.storage import AssetError
from runtime.workspace import WorkspaceRegistry


class FakeBridge:
    available = True
    key = "x" * 32

    def __init__(self):
        self.started = []
        self.states = {}

    def resolve(self, path):
        try:
            target = Path(path).resolve(strict=True)
        except OSError as exc:
            raise AssetError(str(exc)) from exc
        return {"path": str(target), "type": "directory" if target.is_dir() else "file"}

    def fs(self, operation, payload):
        if operation == "list":
            root = Path(payload["root"])
            target = root / Path(str(payload.get("path") or "."))
            if not target.exists():
                raise AssetError("path does not exist")
            rows = []
            for item in sorted(target.iterdir(), key=lambda value: value.name.casefold()):
                rows.append({
                    "name": item.name,
                    "path": item.relative_to(root).as_posix(),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                })
            return {
                "items": rows, "page": payload["page"],
                "page_size": payload["page_size"], "total": len(rows),
                "has_more": False,
            }
        if operation == "edit":
            target = Path(payload["root"])
            content = target.read_text(encoding="utf-8")
            old, new = payload["old"], payload["new"]
            if old not in content:
                raise ValueError("exact text was not found")
            target.write_text(content.replace(old, new), encoding="utf-8")
            return {"path": str(target), "replacements": 1, "size": target.stat().st_size}
        raise AssertionError(f"unexpected source bridge operation: {operation}")

    def job(self, operation, payload):
        job_id = payload["job_id"]
        if operation == "start":
            self.started.append(payload)
            self.states[job_id] = "running"
            return {"job_id": job_id, "state": "running", "terminal": False, "offset": 0}
        if operation == "status":
            return {"job_id": job_id, "state": self.states[job_id], "terminal": self.states[job_id] != "running", "offset": 0, "output": "", "exit_code": None}
        if operation == "cancel":
            self.states[job_id] = "canceled"
            return {"job_id": job_id, "state": "canceled", "terminal": True, "offset": 0, "output": ""}
        return {"job_id": job_id, "removed": True}


class GenericRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = Sessions(self.root)
        self.chat_id = str(uuid.uuid4())
        self.sessions.create("alice", self.chat_id)
        self.store = self.sessions.acquire("alice", self.chat_id)
        self.sessions.release(self.chat_id)
        self.registry = WorkspaceRegistry(self.root)
        self.bridge = FakeBridge()
        self.registry.bridge = self.bridge
        self.box = RuntimeTools(self.store, "alice", [], self.registry, "")

    def tearDown(self):
        self.temp.cleanup()

    def test_generic_files_are_not_domain_filtered(self):
        workspace = self.registry.workspace(self.store)
        (workspace / "nested").mkdir()
        (workspace / "config.ini").write_text("mode=general\n", encoding="utf-8")
        (workspace / "README").write_text("no extension\n", encoding="utf-8")
        (workspace / "sample.bin").write_bytes(b"\x00\xff")
        listed = self.box.execute("fs_list", {"path": "."})
        self.assertTrue(listed["ok"], listed)
        self.assertEqual({item["name"] for item in listed["data"]["items"]}, {".runtime", "nested", "config.ini", "README", "sample.bin"})
        read = self.box.execute("fs_read", {"path": "README"})
        self.assertEqual(read["data"]["content"], "no extension\n")
        binary = self.box.execute("fs_read", {"path": "sample.bin"})
        self.assertFalse(binary["data"]["text"])

    def test_implicit_grant_requires_exact_path_and_blocks_system_roots(self):
        source = self.root / "source"
        source.mkdir()
        requested = str(source.resolve())
        with self.assertRaises(AssetError):
            self.registry.authorize_latest_request(
                "alice", self.chat_id, requested,
                f"请检查 {requested}suffix",
            )
        grant = self.registry.authorize_latest_request(
            "alice", self.chat_id, requested,
            f'请检查 "{requested}"',
        )
        self.assertEqual(grant["host_path"], requested)
        with self.assertRaisesRegex(AssetError, "System directories"):
            self.registry.authorize_latest_request(
                "alice", self.chat_id, r"C:\Windows",
                r"请检查 C:\Windows",
            )

    def test_run_database_uses_wal_and_normal_synchronous_mode(self):
        store = RunStore(self.root / "wal")
        with store.db() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)

    def test_workspace_root_label_is_rejected_with_exact_retry_path(self):
        result = self.box.execute("fs_write", {
            "path": "workspace/report.json", "content": "{}",
        })

        self.assertFalse(result["ok"], result)
        self.assertEqual(
            result["failure"]["code"], "workspace_path_has_root_prefix"
        )
        self.assertEqual(result["failure"]["requested_path"], "workspace/report.json")
        self.assertEqual(result["failure"]["suggested_path"], "report.json")
        self.assertEqual(
            result["failure"]["suggested_arguments"], {"path": "report.json"}
        )
        self.assertFalse((self.registry.workspace(self.store) / "workspace").exists())

    def test_windows_host_workspace_mapping_is_not_resolved_as_linux_path(self):
        with patch.dict(os.environ, {
            "RUNTIME_DATA_HOST_ROOT": r"E:\Forestry Runtime\data",
        }):
            mapped = self.registry.host_workspace(self.store)
        self.assertEqual(
            str(mapped),
            rf"E:\Forestry Runtime\data\sessions\{self.chat_id}\workspace",
        )

    def test_absolute_user_requested_directory_is_routed_to_source_automatically(self):
        source = self.root / "用户目录"
        source.mkdir()
        (source / "notes.txt").write_text("real host file\n", encoding="utf-8")
        self.box.latest_user = f"{source} 这个文件夹下有什么东西"

        listed = self.box.execute("fs_list", {"path": str(source)})

        self.assertTrue(listed["ok"], listed)
        self.assertEqual([item["name"] for item in listed["data"]["items"]], ["notes.txt"])
        grant = listed["data"]["source"]
        self.assertEqual(grant["host_path"], str(source.resolve()))
        self.assertEqual(grant["access"], "read")

    def test_orthomosaic_source_reuses_generic_directory_grant(self):
        source = self.root / "flight"
        source.mkdir()
        grant = self.registry.grant(
            "alice", self.chat_id, str(source), "检查这个无人机影像目录", "read"
        )
        observed = {}

        box = RemoteSensingTools(self.store, "alice", [], self.registry, "")
        def inspect(**arguments):
            observed.update(arguments)
            return {"ready": True, "image_file_count": 10}

        with patch.object(box, "inspect_uav_source", side_effect=inspect):
            result = box.execute("inspect_uav_source", {
                "kind": "folder", "source_id": grant["id"]
            })

        self.assertEqual(
            observed["folder_path"], str(source.resolve())
        )
        self.assertIsNone(observed["source_id"])
        self.assertEqual(result["data"]["source_id"], grant["id"])

    def test_orthomosaic_source_id_resolves_child_without_expanding_to_root(self):
        parent = self.root / "flights"
        child = parent / "1605 白桦"
        child.mkdir(parents=True)
        grant = self.registry.grant(
            "alice", self.chat_id, str(parent), "检查航片根目录", "read"
        )
        self.box.latest_user = "递归检查1605白桦及其所有子目录"
        observed = {}

        box = RemoteSensingTools(
            self.store, "alice", [], self.registry,
            "递归检查1605白桦及其所有子目录",
        )
        def inspect(**arguments):
            observed.update(arguments)
            return {"ready": True}

        with patch.object(box, "inspect_uav_source", side_effect=inspect):
            result = box.execute("inspect_uav_source", {
                "kind": "folder", "source_id": grant["id"],
                "folder_path": "1605 白桦", "recursive": True,
            })

        self.assertEqual(observed["folder_path"], str(child.resolve()))
        self.assertNotEqual(observed["folder_path"], str(parent.resolve()))
        self.assertEqual(result["data"]["source_id"], grant["id"])
        with self.assertRaises(AssetError):
            self.registry.resolve_grant_directory(grant, "../outside")

    def test_authorized_child_suggests_unique_whitespace_variant(self):
        parent = self.root / "flights"
        child = parent / "1605白桦"
        child.mkdir(parents=True)
        (child / "image.jpg").write_bytes(b"image")
        grant = self.registry.grant(
            "alice", self.chat_id, str(parent), "检查航片根目录", "read"
        )

        suggestion = self.registry.suggest_grant_directory(grant, "1605 白桦")
        inferred = self.registry.match_granted_directory(
            "alice", self.chat_id, "1605 白桦"
        )
        listed = self.box.execute("fs_list", {
            "scope": "source", "source_id": grant["id"], "path": "1605 白桦"
        })

        self.assertEqual(suggestion, "1605白桦")
        self.assertEqual(inferred, ("suggestion", grant, "1605白桦"))
        self.assertFalse(listed["ok"], listed)
        self.assertEqual(listed["failure"]["code"], "source_path_not_found")
        self.assertEqual(listed["failure"]["suggested_path"], "1605白桦")

    def test_orthomosaic_relative_child_returns_observed_correction_to_model(self):
        parent = self.root / "flights"
        child = parent / "1605白桦"
        child.mkdir(parents=True)
        grant = self.registry.grant(
            "alice", self.chat_id, str(parent), "检查航片根目录", "read"
        )
        box = RemoteSensingTools(self.store, "alice", [], self.registry, "")
        result = box.execute("inspect_uav_source", {
            "kind": "folder", "folder_path": "1605 白桦"
        })

        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["source_id"], grant["id"])
        self.assertEqual(result["failure"]["suggested_path"], "1605白桦")
        self.assertEqual(result["failure"]["suggested_arguments"], {
            "source_id": grant["id"], "folder_path": "1605白桦",
        })

    def test_knowledge_tools_require_and_use_the_selected_project(self):
        knowledge = self.root / "knowledge"
        knowledge.mkdir()
        document = knowledge / "forest.md"
        document.write_text("候选单木数量不是林木总株数。", encoding="utf-8")
        with patch.dict(
            os.environ, {"KNOWLEDGE_ROOTS": str(knowledge)}, clear=False
        ):
            memory = MemoryManager(self.root / "memory")
            project = memory.create_project("alice", "林分结构")
            memory.index_source(
                "alice", project["id"], "local", str(document)
            )
            box = RuntimeTools(
                self.store, "alice", [], self.registry, "", memory
            )
            missing = box.execute(
                "knowledge_search", {"query": "候选单木", "limit": 3}
            )
            self.assertFalse(missing["ok"])
            memory.select_project("alice", self.chat_id, project["id"])
            found = box.execute(
                "knowledge_search", {"query": "候选单木", "limit": 3}
            )
            self.assertTrue(found["ok"], found)
            chunk_id = found["data"]["results"][0]["id"]
            read = box.execute(
                "knowledge_read",
                {"chunk_id": chunk_id, "before": 0, "after": 0},
            )
            self.assertTrue(read["ok"], read)
            self.assertEqual(read["data"]["chunks"][0]["id"], chunk_id)

            with patch.dict(
                os.environ, {"KNOWLEDGE_SEARCHES_PER_TURN": "2"}
            ):
                second = box.execute(
                    "knowledge_search", {"query": "林木总株数", "limit": 3}
                )
                exhausted = box.execute(
                    "knowledge_search", {"query": "冠层候选", "limit": 3}
                )
            self.assertTrue(second["ok"], second)
            self.assertFalse(exhausted["ok"])
            self.assertEqual(
                exhausted["failure"]["code"],
                "knowledge_search_budget_exhausted",
            )

    def test_exact_relative_child_executes_from_unique_existing_grant(self):
        parent = self.root / "flights"
        child = parent / "1605白桦"
        child.mkdir(parents=True)
        grant = self.registry.grant(
            "alice", self.chat_id, str(parent), "检查航片根目录", "read"
        )
        observed = {}

        box = RemoteSensingTools(self.store, "alice", [], self.registry, "")
        def inspect(**arguments):
            observed.update(arguments)
            return {"ready": True}

        with patch.object(box, "inspect_uav_source", side_effect=inspect):
            result = box.execute("inspect_uav_source", {
                "kind": "folder", "folder_path": "1605白桦"
            })

        self.assertEqual(observed["folder_path"], str(child.resolve()))
        self.assertEqual(result["data"]["source_id"], grant["id"])

    def test_unrequested_recursive_uav_scope_returns_precondition_observation(self):
        parent = self.root / "flights"
        child = parent / "1605白桦"
        child.mkdir(parents=True)
        grant = self.registry.grant(
            "alice", self.chat_id, str(parent), "检查航片根目录", "read"
        )
        box = RemoteSensingTools(
            self.store, "alice", [], self.registry,
            "检查1605白桦是否适合正射拼接",
        )
        result = box.execute("inspect_uav_source", {
            "kind": "folder", "source_id": grant["id"],
            "folder_path": "1605白桦", "recursive": True,
        })

        self.assertFalse(result["ok"])
        failure = result["failure"]
        self.assertEqual(failure["code"], "recursive_scope_unconfirmed")
        self.assertEqual(failure["suggested_arguments"], {"recursive": False})

    def test_existing_parent_grant_authorizes_named_child_directory(self):
        parent = self.root / "flights"
        child = parent / "1605 白桦"
        child.mkdir(parents=True)
        (child / "image.jpg").write_bytes(b"image")
        grant = self.registry.grant(
            "alice", self.chat_id, str(parent), "检查航片根目录", "read"
        )
        self.box.latest_user = "检查 1605 白桦并准备正射拼接"

        listed = self.box.execute("fs_list", {"path": str(child)})

        self.assertTrue(listed["ok"], listed)
        self.assertEqual([item["name"] for item in listed["data"]["items"]], ["image.jpg"])
        self.assertEqual(listed["data"]["source"]["id"], grant["id"])

    def test_write_edit_and_reference_artifact_without_copy(self):
        self.assertTrue(self.box.execute("fs_write", {"path": "code/a.py", "content": "value = 1\n"})["ok"])
        edited = self.box.execute("fs_edit", {"path": "code/a.py", "old": "1", "new": "2"})
        self.assertTrue(edited["ok"], edited)
        target = self.registry.workspace(self.store) / "code" / "a.py"
        asset = self.store.register_path(target, "a.py", "alice", "text/x-python")
        self.assertEqual(self.store.path(asset["id"], "alice"), target)
        self.assertFalse((self.store.root / asset["id"] / "content").exists())

    def test_read_deduplication_key_includes_arguments_and_local_version(self):
        workspace = self.registry.workspace(self.store)
        target = workspace / "notes.txt"
        target.write_text("first\n", encoding="utf-8")

        first = self.box.observation_key("fs_read", {
            "scope": "workspace", "path": "notes.txt",
            "start_line": 1, "max_lines": 20,
        })
        another_range = self.box.observation_key("fs_read", {
            "scope": "workspace", "path": "notes.txt",
            "start_line": 2, "max_lines": 20,
        })
        another_query = self.box.observation_key("fs_search", {
            "scope": "workspace", "path": ".", "query": "first",
            "glob": "*.txt", "max_results": 10,
        })
        target.write_text("second version\n", encoding="utf-8")
        changed = self.box.observation_key("fs_read", {
            "scope": "workspace", "path": "notes.txt",
            "start_line": 1, "max_lines": 20,
        })

        self.assertNotEqual(first, another_range)
        self.assertNotEqual(first, another_query)
        self.assertNotEqual(first, changed)
        self.assertIsNone(self.box.observation_key("fs_read", {
            "scope": "source", "source_id": "grant_x", "path": "notes.txt",
        }))

    def test_truncated_tool_result_can_be_read_to_completion(self):
        original = {"ok": True, "data": {"text": "林" * 20000}}
        bounded = _bounded_tool_result(original, self.box.store_tool_result)
        self.assertTrue(bounded["truncated"])
        self.assertRegex(bounded["result_id"], r"^result_[0-9a-f]{32}$")

        offset = 0
        chunks = []
        while True:
            page = self.box.execute("tool_result_read", {
                "result_id": bounded["result_id"], "offset": offset,
                "max_chars": 4000,
            })
            self.assertTrue(page["ok"], page)
            chunks.append(page["data"]["content"])
            if not page["data"]["has_more"]:
                break
            offset = page["data"]["next_offset"]
        self.assertEqual("".join(chunks), json.dumps(
            original, ensure_ascii=False, allow_nan=False
        ))

    def test_unknown_legacy_job_is_settled_without_backend_replay(self):
        status = self.box.execute("job_status", {
            "job_id": "uav_20260917_camera_ab12cd34", "wait_seconds": 0,
        })
        canceled = self.box.execute("job_cancel", {
            "job_id": "uav_20260917_camera_ab12cd34",
        })
        self.assertEqual(status["data"]["state"], "backend_removed")
        self.assertTrue(status["data"]["terminal"])
        self.assertEqual(
            status["data"]["failure"]["code"],
            "local_photogrammetry_backend_removed",
        )
        self.assertEqual(canceled["data"]["state"], "backend_removed")

    def test_removed_capability_router_is_not_callable(self):
        result = self.box.execute("capabilities_search", {"query": "all"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Unknown or unavailable tool.")

    def test_core_app_import_does_not_load_remote_sensing_dependencies(self):
        script = r'''
import builtins, os
original = builtins.__import__
blocked = {"prosail", "rasterio", "scipy"}
def guarded(name, *args, **kwargs):
    if name.split(".", 1)[0] in blocked:
        raise RuntimeError("domain dependency imported: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
os.environ["REMOTE_SENSING_PLUGINS_ENABLED"] = "false"
import runtime.api.app
print("core-import-ok")
'''
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("core-import-ok", result.stdout)

    def test_new_code_actions_run_even_when_code_is_identical(self):
        with patch.dict(os.environ, {"RUNTIME_DATA_HOST_ROOT": str(self.root)}):
            first = self.box.execute("code_run", {"language": "python", "code": "print(2 + 2)"})
            second = self.box.execute("code_run", {"language": "python", "code": "print(2 + 2)"})
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(first["data"]["job_id"], second["data"]["job_id"])
        self.assertNotIn("reused", second["data"])
        self.assertEqual(len(self.bridge.started), 2)

    def test_completed_code_job_is_not_reused_after_workspace_input_changes(self):
        workspace = self.registry.workspace(self.store)
        source = workspace / "input.txt"
        source.write_text("version one", encoding="utf-8")
        arguments = {
            "language": "python",
            "code": "print(open('input.txt', encoding='utf-8').read())",
        }
        with patch.dict(os.environ, {"RUNTIME_DATA_HOST_ROOT": str(self.root)}):
            first = self.box.execute("code_run", arguments)
            first_id = first["data"]["job_id"]
            self.bridge.states[first_id] = "succeeded"
            self.box.execute("job_status", {"job_id": first_id, "wait_seconds": 0})
            unchanged = self.box.execute("code_run", arguments)
            source.write_text("version two", encoding="utf-8")
            changed = self.box.execute("code_run", arguments)

        self.assertNotEqual(unchanged["data"]["job_id"], first_id)
        self.assertNotEqual(changed["data"]["job_id"], first_id)
        self.assertNotEqual(changed["data"]["job_id"], unchanged["data"]["job_id"])
        self.assertEqual(len(self.bridge.started), 3)

    def test_completed_code_job_with_unversioned_source_is_not_reused(self):
        external = self.root / "external-code-input"
        external.mkdir()
        grant = self.registry.grant(
            "alice", self.chat_id, str(external), "use this directory", "read"
        )
        arguments = {
            "language": "python", "code": "print('source job')",
            "source_ids": [grant["id"]],
        }
        with patch.dict(os.environ, {"RUNTIME_DATA_HOST_ROOT": str(self.root)}):
            first = self.box.execute("code_run", arguments)
            first_id = first["data"]["job_id"]
            self.bridge.states[first_id] = "succeeded"
            self.box.execute("job_status", {"job_id": first_id, "wait_seconds": 0})
            second = self.box.execute("code_run", arguments)

        self.assertNotEqual(second["data"]["job_id"], first_id)
        self.assertEqual(len(self.bridge.started), 2)

    def test_dependency_install_result_returns_resolved_manifest(self):
        with patch.dict(os.environ, {"RUNTIME_DATA_HOST_ROOT": str(self.root)}):
            started = self.box.execute("dependency_install", {"packages": ["demo-package==1.2.3"]})
        job_id = started["data"]["job_id"]
        manifest = self.registry.workspace(self.store) / ".runtime" / "deps" / "installed-packages.json"
        manifest.write_text('[{"name":"demo-package","version":"1.2.3"}]', encoding="utf-8")
        self.bridge.states[job_id] = "succeeded"
        result = self.box.execute("job_status", {"job_id": job_id, "wait_seconds": 0})
        self.assertEqual(result["data"]["installed_packages"], [{"name": "demo-package", "version": "1.2.3"}])

    def test_uncertain_submission_is_reconciled_by_action_id(self):
        original_job = self.bridge.job

        def uncertain(operation, payload):
            if operation == "start":
                self.bridge.states[payload["job_id"]] = "running"
                raise TimeoutError("response lost after submission")
            return original_job(operation, payload)

        self.bridge.job = uncertain
        with patch.dict(os.environ, {"RUNTIME_DATA_HOST_ROOT": str(self.root)}):
            result = self.box.execute("code_run", {"language": "python", "code": "print('once')"})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["data"]["submission_reconciled"])

    def test_run_events_and_actions_survive_reopen(self):
        runs = RunStore(self.root)
        run = runs.create("alice", self.chat_id, [{"role": "user", "content": "test"}], [], True)
        runs.append(run["id"], {"type": "tool_start", "action_id": "call_1", "name": "fs_list", "arguments": {"path": "."}})
        runs.append(run["id"], {"type": "tool_end", "action_id": "call_1", "name": "fs_list", "ok": True, "result": {"ok": True, "data": {"items": []}}})
        reopened = RunStore(self.root)
        events = reopened.events_after(run["id"], "alice")
        self.assertEqual([event["type"] for event in events], ["tool_start", "tool_end"])
        self.assertTrue((self.root / "runs" / run["id"] / "events.jsonl").is_file())
        reopened.cleanup_chat(self.chat_id)
        with self.assertRaises(AssetError):
            reopened.get(run["id"], "alice")
        self.assertFalse((self.root / "runs" / run["id"]).exists())

    def test_action_id_replay_returns_recorded_result_without_new_attempt(self):
        runs = RunStore(self.root)
        run = runs.create(
            "alice", self.chat_id,
            [{"role": "user", "content": "once", "input_id": "input_action_once"}],
            [], True,
        )
        turn = runs.claim_turn(run["id"])
        prepared = runs.begin_action(
            run["id"], turn["id"], "call_once", "fs_list", {"path": "."}
        )
        runs.mark_attempt_started(prepared["attempt_id"])
        result = {"ok": True, "data": {"items": []}}
        runs.finish_action(
            run["id"], turn["id"], "call_once", "fs_list", result, 0.1,
            prepared["attempt_id"],
        )
        replay = runs.begin_action(
            run["id"], turn["id"], "call_once", "fs_list", {"path": "."}
        )
        self.assertFalse(replay["execute"])
        self.assertEqual(replay["result"], result)
        with runs.db() as conn:
            attempts = conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE action_id='call_once'"
            ).fetchone()[0]
        self.assertEqual(attempts, 1)

    def test_action_registration_and_event_are_one_transaction(self):
        runs = RunStore(self.root)
        run = runs.create(
            "alice", self.chat_id,
            [{"role": "user", "content": "atomic", "input_id": "input_atomic_action"}],
            [], True,
        )
        turn = runs.claim_turn(run["id"])
        with runs.db() as conn:
            conn.execute("""CREATE TRIGGER reject_action_event BEFORE INSERT ON events
                BEGIN SELECT RAISE(ABORT, 'injected event failure'); END""")
        with self.assertRaises(Exception):
            runs.begin_action(
                run["id"], turn["id"], "call_atomic", "fs_list", {"path": "."}
            )
        with runs.db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM actions WHERE id='call_atomic'").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM attempts WHERE action_id='call_atomic'").fetchone()[0],
                0,
            )

    def test_action_result_and_event_are_one_transaction(self):
        runs = RunStore(self.root)
        run = runs.create(
            "alice", self.chat_id,
            [{"role": "user", "content": "atomic", "input_id": "input_atomic_result"}],
            [], True,
        )
        turn = runs.claim_turn(run["id"])
        prepared = runs.begin_action(
            run["id"], turn["id"], "call_result", "fs_list", {"path": "."}
        )
        with runs.db() as conn:
            conn.execute("""CREATE TRIGGER reject_result_event BEFORE INSERT ON events
                BEGIN SELECT RAISE(ABORT, 'injected event failure'); END""")
        with self.assertRaises(Exception):
            runs.finish_action(
                run["id"], turn["id"], "call_result", "fs_list",
                {"ok": True, "data": {"items": []}}, 0.1, prepared["attempt_id"],
            )
        with runs.db() as conn:
            action = conn.execute(
                "SELECT state,result_json FROM actions WHERE id='call_result'"
            ).fetchone()
            attempt = conn.execute(
                "SELECT state,result_json FROM attempts WHERE id=?",
                (prepared["attempt_id"],),
            ).fetchone()
        self.assertEqual(action["state"], "running")
        self.assertIsNone(action["result_json"])
        self.assertEqual(attempt["state"], "prepared")
        self.assertIsNone(attempt["result_json"])

    def test_duplicate_input_id_does_not_create_another_turn(self):
        coordinator = RunCoordinator(self.sessions, self.registry)
        first = coordinator.store.create(
            "alice", self.chat_id,
            [{"role": "user", "content": "first", "input_id": "input_stable_123"}],
            [], True,
        )
        coordinator.store.set_state(first["id"], "running")
        coordinator.store.set_state(first["id"], "completed")
        duplicate = coordinator.continue_run(
            first["id"], "alice", "first", self.store, [], "input_stable_123"
        )
        self.assertEqual(duplicate["id"], first["id"])
        self.assertEqual(len(coordinator.store.turns(first["id"], "alice")), 1)

    def test_reconciled_job_event_updates_persisted_job_reference(self):
        runs = RunStore(self.root)
        run = runs.create(
            "alice", self.chat_id, [{"role": "user", "content": "job"}], [], True
        )
        runs.append(run["id"], {
            "type": "tool_start", "action_id": "call_job",
            "name": "code_run", "arguments": {},
        })
        runs.append(run["id"], {
            "type": "tool_end", "action_id": "call_job",
            "name": "code_run", "ok": True,
            "result": {"ok": True, "data": {
                "job_id": "job_" + "1" * 32, "job_type": "code",
                "state": "running",
            }},
        })
        runs.append(run["id"], {
            "type": "job_reconciled", "job_id": "job_" + "1" * 32,
            "job_type": "code", "state": "canceled", "terminal": True,
        })
        self.assertEqual(runs.jobs(run["id"])[0]["state"], "canceled")

    def test_long_event_stream_pages_without_losing_terminal_events(self):
        runs = RunStore(self.root)
        run = runs.create(
            "alice", self.chat_id, [{"role": "user", "content": "long"}], [], True
        )
        turn = runs.latest_turn(run["id"])
        for index in range(10004):
            runs.append(
                run["id"], {"type": "message", "content": str(index)}, turn["id"]
            )
        runs.append(run["id"], {"type": "done", "artifacts": []}, turn["id"])

        after = 0
        events = []
        while True:
            page = runs.event_page(run["id"], "alice", after, 500)
            events.extend(page["events"])
            after = page["next"]
            if not page["has_more"]:
                break
        self.assertEqual(len(events), 10005)
        self.assertEqual([event["seq"] for event in events], list(range(1, 10006)))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(runs.events_tail(run["id"], "alice", 1)[0]["type"], "done")

    def test_source_directory_is_read_only_and_write_grant_is_one_file(self):
        source = self.root / "external"
        source.mkdir()
        target = source / "config.ini"
        target.write_text("mode=old\n", encoding="utf-8")
        read_grant = self.registry.grant("alice", self.chat_id, str(source), "查看这个目录", "read")
        denied = self.box.execute("fs_edit", {
            "scope": "source", "source_id": read_grant["id"], "path": "config.ini",
            "old": "old", "new": "new",
        })
        self.assertFalse(denied["ok"])
        with self.assertRaises(AssetError):
            self.registry.grant("alice", self.chat_id, str(source), "修改目录", "write")
        with self.assertRaises(AssetError):
            self.registry.grant("alice", self.chat_id, str(target), "读取文件", "read")

        write_grant = self.registry.grant("alice", self.chat_id, str(target), "修改这个文件", "write")
        changed = self.box.execute("fs_edit", {
            "scope": "source", "source_id": write_grant["id"], "path": ".",
            "old": "old", "new": "new",
        })
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(target.read_text(encoding="utf-8"), "mode=new\n")

    def test_background_run_persists_events_after_request_lifetime(self):
        async def fake_agent(*args, **kwargs):
            yield {"type": "model_call", "number": 1, "tool_count": 13, "tool_schema_chars": 100}
            yield {"type": "message", "content": "finished"}
            yield {"type": "done", "artifacts": []}

        async def check():
            coordinator = RunCoordinator(self.sessions, self.registry)
            with patch("runtime.session.coordinator.stream_agent", fake_agent):
                run = coordinator.create(self.store, "alice", [{"role": "user", "content": "go"}], [])
                await coordinator.tasks[run["id"]]
            self.assertEqual(coordinator.get(run["id"], "alice")["state"], "completed")
            reopened = RunStore(self.root)
            self.assertEqual(
                [event["type"] for event in reopened.events_after(run["id"], "alice")],
                ["user_message", "run_state", "model_call", "message", "done", "run_state"],
            )

        asyncio.run(check())

    def test_runtime_shutdown_pauses_run_without_marking_user_cancel(self):
        async def slow_agent(*args, **kwargs):
            yield {"type": "model_call", "number": 1, "tool_count": 13, "tool_schema_chars": 100}
            await asyncio.sleep(30)

        async def check():
            coordinator = RunCoordinator(self.sessions, self.registry)
            with patch("runtime.session.coordinator.stream_agent", slow_agent):
                run = coordinator.create(self.store, "alice", [{"role": "user", "content": "go"}], [])
                await asyncio.sleep(0)
                await coordinator.shutdown()
            restored = coordinator.store.get(run["id"], "alice")
            self.assertEqual(restored["state"], "paused")
            self.assertFalse(restored["cancel_requested"])
            self.assertEqual(coordinator.store.events_after(run["id"], "alice")[-1]["state"], "paused")

        asyncio.run(check())

    def test_continued_run_reuses_framework_persistence_and_only_sends_new_input(self):
        captured = []

        async def fake_agent(*args, **kwargs):
            captured.append({
                "messages": args[3], "agent_run_id": kwargs.get("agent_run_id"),
                "chat_id": kwargs.get("chat_id"),
                "database": kwargs.get("persistence_database"),
            })
            yield {"type": "message", "content": "observed-result"}
            yield {"type": "done", "artifacts": []}

        async def check():
            coordinator = RunCoordinator(self.sessions, self.registry)
            with patch("runtime.session.coordinator.stream_agent", fake_agent):
                run = coordinator.create(self.store, "alice", [{"role": "user", "content": "first"}], [])
                await coordinator.tasks[run["id"]]
                coordinator.continue_run(run["id"], "alice", "continue", self.store)
                await coordinator.tasks[run["id"]]
            self.assertNotEqual(captured[0]["agent_run_id"], captured[1]["agent_run_id"])
            self.assertEqual(captured[0]["chat_id"], captured[1]["chat_id"])
            self.assertEqual(captured[0]["database"], captured[1]["database"])
            self.assertNotIn("first", [item.get("content") for item in captured[1]["messages"]])
            self.assertEqual(captured[1]["messages"], [{"role": "user", "content": "continue"}])

        asyncio.run(check())

    def test_input_added_while_running_is_executed_as_the_next_turn(self):
        captured = []
        release = asyncio.Event()

        async def fake_agent(*args, **kwargs):
            captured.append(args[3])
            if len(captured) == 1:
                yield {"type": "model_call", "number": 1}
                await release.wait()
            yield {"type": "message", "content": f"turn-{len(captured)}"}
            yield {"type": "done", "artifacts": []}

        async def check():
            coordinator = RunCoordinator(self.sessions, self.registry)
            with patch("runtime.session.coordinator.stream_agent", fake_agent):
                created = coordinator.create(
                    self.store, "alice", [{"role": "user", "content": "first"}], []
                )
                first_task = coordinator.tasks[created["id"]]
                await asyncio.sleep(0)
                queued = coordinator.continue_run(
                    created["id"], "alice", "additional requirement", self.store
                )
                self.assertEqual(queued["state"], "running")
                release.set()
                await first_task
                await coordinator.tasks[created["id"]]

            self.assertEqual(len(captured), 2)
            self.assertEqual(
                captured[1], [{"role": "user", "content": "additional requirement"}]
            )
            turns = coordinator.store.turns(created["id"], "alice")
            self.assertEqual([turn["ordinal"] for turn in turns], [1, 2])
            self.assertEqual([turn["state"] for turn in turns], ["completed", "completed"])
            queued_event = next(
                event for event in coordinator.store.events_all(created["id"], "alice")
                if event.get("queued")
            )
            self.assertEqual(queued_event["turn_id"], turns[1]["id"])

        asyncio.run(check())

    def test_pause_waits_for_an_event_boundary_and_preserves_run(self):
        release = asyncio.Event()

        async def fake_agent(*args, **kwargs):
            yield {"type": "model_call", "number": 1}
            await release.wait()
            if kwargs["pause_requested"]():
                yield {"type": "error", "content": "paused before next request", "state": "paused"}
                yield {"type": "done", "artifacts": [], "state": "paused"}
                return
            yield {"type": "done", "artifacts": []}

        async def check():
            coordinator = RunCoordinator(self.sessions, self.registry)
            with patch("runtime.session.coordinator.stream_agent", fake_agent):
                created = coordinator.create(
                    self.store, "alice", [{"role": "user", "content": "pause me"}], []
                )
                await asyncio.sleep(0)
                paused_request = await coordinator.pause(created["id"], "alice")
                self.assertEqual(paused_request["state"], "running")
                release.set()
                await coordinator.tasks[created["id"]]
            restored = coordinator.store.get(created["id"], "alice")
            self.assertEqual(restored["state"], "paused")
            self.assertFalse(restored["cancel_requested"])
            self.assertEqual(
                coordinator.store.events_tail(created["id"], "alice", 1)[0]["state"],
                "paused",
            )

        asyncio.run(check())

    def test_restart_reattaches_job_then_agent_resumes_from_terminal_state(self):
        captured = []
        job_id = "job_" + "b" * 32

        class RecoveringBridge(FakeBridge):
            def __init__(self):
                super().__init__()
                self.status_reads = 0

            def job(self, operation, payload):
                if operation == "status":
                    self.status_reads += 1
                    state = "running" if self.status_reads == 1 else "succeeded"
                    return {
                        "job_id": payload["job_id"], "state": state,
                        "terminal": state == "succeeded", "offset": 0,
                        "output": "", "exit_code": 0 if state == "succeeded" else None,
                    }
                return super().job(operation, payload)

        async def fake_agent(*args, **kwargs):
            captured.append(args[3])
            yield {"type": "message", "content": "resumed"}
            yield {"type": "done", "artifacts": []}

        async def check():
            registry = WorkspaceRegistry(self.root)
            registry.bridge = RecoveringBridge()
            coordinator = RunCoordinator(self.sessions, registry)
            run = coordinator.store.create("alice", self.chat_id, [{"role": "user", "content": "long job"}], [], True)
            coordinator.store.set_state(run["id"], "running")
            coordinator.store.append(run["id"], {
                "type": "tool_start", "action_id": "action_1",
                "name": "code_run", "arguments": {},
            })
            coordinator.store.append(run["id"], {
                "type": "tool_end", "action_id": "action_1", "name": "code_run", "ok": True,
                "result": {"ok": True, "data": {"job_id": job_id, "state": "running"}},
            })
            with patch("runtime.session.coordinator.stream_agent", fake_agent), patch.object(
                coordinator, "_checkpoint_state", return_value="complete"
            ):
                await coordinator.reconcile()
                for _ in range(100):
                    task = coordinator.tasks.get(run["id"])
                    if task:
                        await task
                        break
                    await asyncio.sleep(0.02)
            self.assertEqual(coordinator.store.get(run["id"], "alice")["state"], "completed")
            context = "\n".join(item["content"] for item in captured[0] if item["role"] == "system")
            self.assertIn(job_id, context)
            self.assertIn("succeeded", context)

        asyncio.run(check())

    def test_restart_does_not_replay_job_without_settled_checkpoint(self):
        captured = []
        job_id = "job_" + "d" * 32

        class FinishedBridge(FakeBridge):
            def job(self, operation, payload):
                if operation == "status":
                    return {
                        "job_id": payload["job_id"], "state": "succeeded",
                        "terminal": True, "offset": 0, "output": "", "exit_code": 0,
                    }
                return super().job(operation, payload)

        async def fake_agent(*args, **kwargs):
            captured.append(args[3])
            yield {"type": "done", "artifacts": []}

        async def check():
            registry = WorkspaceRegistry(self.root)
            registry.bridge = FinishedBridge()
            coordinator = RunCoordinator(self.sessions, registry)
            run = coordinator.store.create(
                "alice", self.chat_id,
                [{"role": "user", "content": "side effect"}], [], True,
            )
            turn = coordinator.store.latest_turn(run["id"])
            coordinator.store.set_state(run["id"], "running")
            coordinator.store.append(run["id"], {
                "type": "tool_start", "action_id": "action_unsafe",
                "name": "code_run", "arguments": {},
            }, turn["id"])
            coordinator.store.append(run["id"], {
                "type": "tool_end", "action_id": "action_unsafe", "name": "code_run",
                "ok": True,
                "result": {"ok": True, "data": {"job_id": job_id, "state": "running"}},
            }, turn["id"])
            with patch("runtime.session.coordinator.stream_agent", fake_agent):
                await coordinator.reconcile()
            restored = coordinator.store.get(run["id"], "alice")
            self.assertEqual(restored["state"], "paused")
            self.assertEqual(captured, [])
            self.assertEqual(
                coordinator.store.events_tail(run["id"], "alice", 1)[0]["type"],
                "recovery_blocked",
            )

        asyncio.run(check())

    def test_cancel_stays_canceling_while_job_state_is_unknown(self):
        async def check():
            coordinator = RunCoordinator(self.sessions, self.registry)
            run = coordinator.store.create(
                "alice", self.chat_id,
                [{"role": "user", "content": "cancel", "input_id": "input_cancel_unknown"}],
                [], True,
            )
            turn = coordinator.store.latest_turn(run["id"])
            coordinator.store.set_state(run["id"], "running")
            coordinator.store.append(run["id"], {
                "type": "tool_start", "action_id": "cancel_action",
                "name": "code_run", "arguments": {},
            }, turn["id"])
            coordinator.store.append(run["id"], {
                "type": "tool_end", "action_id": "cancel_action", "name": "code_run",
                "ok": True, "result": {"ok": True, "data": {
                    "job_id": "job_" + "e" * 32, "job_type": "code", "state": "running",
                }},
            }, turn["id"])

            async def unavailable(*args, **kwargs):
                raise AssetError("Docker temporarily unavailable")

            with patch.object(coordinator, "_job_operation", unavailable):
                canceled = await coordinator.cancel(run["id"], "alice")
                self.assertEqual(canceled["state"], "canceling")
                tail = coordinator.store.events_tail(run["id"], "alice", 3)
                self.assertTrue(any(
                    item.get("observation_error") and item.get("state") == "unknown"
                    for item in tail
                ))
                await coordinator.shutdown()

        asyncio.run(check())

    def test_coordinator_settles_removed_photogrammetry_job_without_replay(self):
        async def check():
            coordinator = RunCoordinator(self.sessions, self.registry)
            run = coordinator.store.create(
                "alice", self.chat_id,
                [{"role": "user", "content": "orthomosaic"}], [], True,
            )
            turn = coordinator.store.latest_turn(run["id"])
            coordinator.store.set_state(run["id"], "running")
            coordinator.store.append(run["id"], {
                "type": "tool_start", "action_id": "odm_action",
                "name": "start_orthomosaic", "arguments": {},
            }, turn["id"])
            coordinator.store.append(run["id"], {
                "type": "tool_end", "action_id": "odm_action",
                "name": "start_orthomosaic", "ok": True,
                "result": {"ok": True, "data": {
                    "job_id": "uav_job_recovery", "job_type": "orthomosaic",
                    "state": "running",
                }},
            }, turn["id"])
            status = await coordinator._job_operation(
                coordinator.store.get(run["id"]), "job_status",
                "uav_job_recovery",
            )
            self.assertEqual(status["state"], "backend_removed")
            self.assertTrue(status["terminal"])
            self.assertEqual(
                status["failure"]["code"],
                "local_photogrammetry_backend_removed",
            )
            await coordinator.shutdown()

        asyncio.run(check())

    def test_active_job_keeps_deleted_chat_resources_until_terminal(self):
        job_id = "job_" + "c" * 32
        self.bridge.states[job_id] = "running"

        async def fake_agent(*args, **kwargs):
            yield {
                "type": "tool_start", "action_id": "action_job",
                "name": "code_run", "arguments": {},
            }
            yield {
                "type": "tool_end", "action_id": "action_job", "name": "code_run", "ok": True,
                "result": {"ok": True, "data": {"job_id": job_id, "state": "running"}},
            }
            yield {"type": "done", "artifacts": []}

        async def check():
            coordinator = RunCoordinator(self.sessions, self.registry)
            with patch("runtime.session.coordinator.stream_agent", fake_agent), patch.dict(os.environ, {"RECOVERY_POLL_SECONDS": "1"}):
                run = coordinator.create(self.store, "alice", [{"role": "user", "content": "start"}], [])
                await coordinator.tasks[run["id"]]
                await asyncio.sleep(0)
                self.assertEqual(coordinator.store.get(run["id"], "alice")["state"], "waiting")
                self.sessions.mark_deleted("alice", self.chat_id)
                self.sessions.reap()
                self.assertTrue(self.store.root.exists())
                self.bridge.states[job_id] = "succeeded"
                for _ in range(100):
                    if self.sessions.active.get(self.chat_id, 0) == 0:
                        break
                    await asyncio.sleep(0.02)
                self.sessions.reap()
                self.assertFalse(self.store.root.exists())

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
