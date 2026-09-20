import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel

from runtime.agent import _report_only_requested, _tools, stream_agent
from runtime.storage import Store
from runtime.capabilities.domain_runtime import DEFINITIONS, RemoteSensingTools
from runtime.capabilities.runtime import GENERIC_DEFINITIONS
from runtime.tool_protocol import inline_schema, normalize_arguments
from runtime.workspace import WorkspaceRegistry


def tool_call(name, arguments, call_id):
    return DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=call_id)


class ToolRecoveryTests(unittest.TestCase):
    def test_complete_report_lock_distinguishes_inspection_from_requested_action(self):
        self.assertTrue(_report_only_requested("检查已有成果并判断是否可生成CHM"))
        self.assertFalse(_report_only_requested("检查已有成果，然后生成CHM"))
        self.assertFalse(_report_only_requested("检查影像并计算NDVI"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.chat_id = "test-chat"
        self.registry = WorkspaceRegistry(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_with(self, stream_function, asset_ids=None):
        async def check():
            return [event async for event in stream_agent(
                self.store,
                "alice",
                asset_ids or [],
                [{"role": "user", "content": "执行测试任务"}],
                model=FunctionModel(stream_function=stream_function),
                workspace_registry=self.registry,
            )]

        return asyncio.run(check())

    def test_model_contract_flattens_common_uav_action_but_keeps_band_object(self):
        definitions = {
            name: inline_schema(model.model_json_schema())
            for name, (model, _) in DEFINITIONS.items()
        }
        self.assertNotIn('"$ref"', json.dumps(definitions))
        self.assertEqual(definitions["inspect_uav_source"]["properties"]["kind"]["type"], "string")
        self.assertIn("folder_path", definitions["inspect_uav_source"]["properties"])
        self.assertNotIn("source", definitions["inspect_uav_source"]["properties"])
        self.assertEqual(definitions["calculate_ndvi"]["properties"]["bands"]["type"], "object")
        self.assertNotIn("start_orthomosaic", definitions)
        self.assertEqual(
            set(definitions["simulate_prosail"]["required"]),
            {
                "parameters", "geometry", "model", "sensor_bands",
                "parameter_source", "geometry_source",
                "sensor_response_source",
            },
        )
        self.assertIn("parameter_ranges", definitions["build_prosail_lut"]["required"])
        self.assertIn("parameter_source", definitions["build_prosail_lut"]["required"])
        self.assertIn("lut_asset_id", definitions["invert_prosail"]["required"])
        self.assertIn("band_mapping_source", definitions["invert_prosail"]["required"])
        with self.assertRaises(ValueError):
            inline_schema({"$defs": {"Loop": {"$ref": "#/$defs/Loop"}}, "$ref": "#/$defs/Loop"})

    def test_serialized_object_is_decoded_without_changing_path_or_string_fields(self):
        box = RemoteSensingTools(self.store, "alice", [])
        source = {"kind": "folder", "folder_path": "E:\\survey\\images"}
        with patch.object(box, "inspect_uav_source", return_value={"ready": True}) as inspect:
            result = box.execute("inspect_uav_source", source)
        self.assertTrue(result["ok"], result)
        self.assertEqual(inspect.call_args.kwargs["folder_path"], source["folder_path"])
        value, changes = normalize_arguments('{"x":1}', {"type": "string"})
        self.assertEqual(value, '{"x":1}')
        self.assertEqual(changes, [])

    def test_pydantic_loop_returns_real_tool_observation(self):
        asset = self.store.put(io.BytesIO(b"field=oak"), "sample.txt", "alice")
        turn = 0

        async def model(messages, info):
            nonlocal turn
            turn += 1
            if turn == 1:
                yield {0: tool_call("fs_read", {"scope": "asset", "asset_id": asset["id"]}, "call_1")}
            else:
                returned = [
                    part.content for message in messages for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                self.assertIn("field=oak", json.dumps(returned))
                yield "oak"

        events = self.run_with(model, [asset["id"]])
        self.assertEqual(
            [event["type"] for event in events],
            ["model_call", "tool_start", "tool_end", "model_call", "message", "done"],
        )
        self.assertTrue(events[0]["budget_ok"])
        self.assertGreater(events[0]["estimated_input_tokens"], 0)
        self.assertEqual(
            events[0]["estimated_total_tokens"],
            events[0]["estimated_input_tokens"] + events[0]["output_reserve_tokens"],
        )
        self.assertEqual(events[-2]["content"], "oak")

    def test_failed_action_does_not_discard_independent_action(self):
        turn = 0

        async def model(messages, info):
            nonlocal turn
            turn += 1
            if turn == 1:
                yield {
                    0: tool_call("fs_read", {"path": "missing.txt"}, "call_read"),
                    1: tool_call("fs_write", {"path": "created.txt", "content": "kept"}, "call_write"),
                }
            else:
                yield "handled"

        events = self.run_with(model)
        ends = [event for event in events if event["type"] == "tool_end"]
        self.assertEqual([event["name"] for event in ends], ["fs_read", "fs_write"])
        self.assertFalse(ends[0]["ok"])
        self.assertTrue(ends[1]["ok"])
        self.assertEqual((Path(self.temp.name) / "workspace" / "created.txt").read_text(), "kept")

    def test_failed_read_can_change_method(self):
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "notes.txt").write_text("converted-input-available", encoding="utf-8")
        turn = 0

        async def model(messages, info):
            nonlocal turn
            turn += 1
            if turn == 1:
                yield {0: tool_call("fs_read", {"path": "missing.txt"}, "call_1")}
            elif turn == 2:
                yield {0: tool_call("fs_list", {"path": "."}, "call_2")}
            elif turn == 3:
                yield {0: tool_call("fs_read", {"path": "notes.txt"}, "call_3")}
            else:
                yield "已读取实际存在的文件"

        events = self.run_with(model)
        self.assertEqual(
            [event["name"] for event in events if event["type"] == "tool_end"],
            ["fs_read", "fs_list", "fs_read"],
        )
        self.assertEqual(events[-2]["content"], "已读取实际存在的文件")

    def test_thinking_is_forwarded_by_pydantic_events(self):
        async def model(messages, info):
            yield {0: DeltaThinkingPart(content="inspect")}
            yield "ok"

        events = self.run_with(model)
        thinking = "".join(event["content"] for event in events if event["type"] == "thinking")
        self.assertEqual(thinking, "inspect")
        self.assertEqual(events[-2]["content"], "ok")

    def test_identical_failed_call_is_not_executed_again(self):
        turn = 0

        async def model(messages, info):
            nonlocal turn
            turn += 1
            if turn <= 2:
                yield {0: tool_call("fs_read", {"path": "missing.txt"}, f"call_{turn}")}
            else:
                yield "stopped"

        events = self.run_with(model)
        failures = [event["result"] for event in events if event["type"] == "tool_end"]
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[1]["failure"]["code"], "duplicate_failed_call")

    def test_three_blocked_repeats_pause_the_agent_run(self):
        turn = 0

        async def model(messages, info):
            nonlocal turn
            turn += 1
            yield {0: tool_call("fs_read", {"path": "missing.txt"}, f"call_{turn}")}

        events = self.run_with(model)
        failures = [event for event in events if event["type"] == "tool_end"]
        self.assertEqual(len(failures), 3)
        self.assertEqual(failures[-1]["result"]["failure"]["code"], "duplicate_failed_call")
        self.assertEqual(events[-1]["state"], "paused")
        self.assertIn("连续三次", events[-2]["content"])

    def test_known_invalid_source_path_is_not_retried_through_another_tool(self):
        turn = 0

        async def model(messages, info):
            nonlocal turn
            turn += 1
            if turn == 1:
                yield {0: tool_call("fs_list", {
                    "scope": "source", "source_id": "grant_" + "a" * 32,
                    "path": "1605 白桦",
                }, "call_1")}
            elif turn == 2:
                yield {0: tool_call("fs_search", {
                    "scope": "source", "source_id": "grant_" + "a" * 32,
                    "path": "1605 白桦", "query": "IMG",
                }, "call_2")}
            else:
                yield "stopped"

        failure = {
            "ok": False,
            "error": "use observed exact path",
            "failure": {
                "stage": "preconditions", "code": "source_path_not_found",
                "operation_started": False, "side_effects": "none",
                "requested_path": "1605 白桦", "suggested_path": "1605白桦",
                "source_id": "grant_" + "a" * 32,
            },
        }
        with patch("runtime.capabilities.runtime.RuntimeTools.execute", return_value=failure) as execute:
            events = self.run_with(model)

        failures = [event["result"] for event in events if event["type"] == "tool_end"]
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(failures[1]["failure"]["code"], "known_invalid_source_path")
        self.assertEqual(failures[1]["failure"]["suggested_path"], "1605白桦")

    def test_domain_retry_requires_model_to_use_exact_observed_path(self):
        turn = 0
        grant_id = "grant_" + "a" * 32

        async def model(messages, info):
            nonlocal turn
            turn += 1
            if turn == 1:
                yield {0: tool_call("inspect_uav_source", {
                    "kind": "folder", "source_id": grant_id,
                    "folder_path": "1605 白桦",
                }, "call_1")}
            elif turn == 2:
                yield {0: tool_call("inspect_uav_source", {
                    "kind": "folder", "source_id": grant_id,
                    "folder_path": "1605白桦",
                }, "call_2")}
            else:
                yield "checked"

        failure = {
            "ok": False,
            "error": "use observed exact path",
            "failure": {
                "stage": "preconditions", "code": "source_path_not_found",
                "operation_started": False, "side_effects": "none",
                "requested_path": "1605 白桦", "suggested_path": "1605白桦",
                "source_id": grant_id,
                "suggested_arguments": {
                    "source_id": grant_id, "folder_path": "1605白桦",
                },
            },
        }
        observed = []

        def execute(name, arguments, progress=None):
            observed.append(arguments)
            return failure if len(observed) == 1 else {"ok": True, "data": {"ready": True}}

        async def check():
            with patch.dict("os.environ", {"REMOTE_SENSING_PLUGINS_ENABLED": "true"}), patch(
                "runtime.capabilities.runtime.RuntimeTools._execute_domain", side_effect=execute
            ):
                return [event async for event in stream_agent(
                    self.store, "alice", [],
                    [{"role": "user", "content": "检查无人机正射目录"}],
                    model=FunctionModel(stream_function=model),
                    workspace_registry=self.registry,
                )]

        events = asyncio.run(check())
        self.assertEqual(observed[1]["folder_path"], "1605白桦")
        success = [event for event in events if event.get("type") == "tool_end"][-1]
        self.assertTrue(success["ok"])
        self.assertNotIn("argument_normalization", success["result"])

    def test_framework_tools_have_no_legacy_capability_router(self):
        tools, visible_count, schema_chars = _tools(True)
        names = {tool.name for tool in tools}
        self.assertNotIn("capabilities_search", names)
        self.assertNotIn("capabilities_read", names)
        self.assertIn("fs_list", names)
        self.assertGreater(visible_count, 0)
        self.assertGreater(schema_chars, 0)

    def test_remote_sensing_tools_are_deferred(self):
        with patch.dict("os.environ", {"REMOTE_SENSING_PLUGINS_ENABLED": "true"}):
            tools, visible_count, _ = _tools(True)
        by_name = {tool.name: tool for tool in tools}
        self.assertTrue(by_name["simulate_prosail"].defer_loading)
        self.assertTrue(by_name["build_prosail_lut"].defer_loading)
        self.assertTrue(by_name["invert_prosail"].defer_loading)
        self.assertTrue(by_name["inspect_uav_source"].defer_loading)
        self.assertNotIn("start_orthomosaic", by_name)
        self.assertEqual(visible_count, len(GENERIC_DEFINITIONS))

    def test_chinese_orthomosaic_request_preloads_only_relevant_domain_group(self):
        with patch.dict("os.environ", {"REMOTE_SENSING_PLUGINS_ENABLED": "true"}):
            tools, visible_count, _ = _tools(
                True, "检查这个无人机航片目录能否进行正射拼接"
            )
        by_name = {tool.name: tool for tool in tools}
        self.assertFalse(by_name["inspect_uav_source"].defer_loading)
        self.assertNotIn("start_orthomosaic", by_name)
        self.assertTrue(by_name["invert_prosail"].defer_loading)
        self.assertEqual(visible_count, len(GENERIC_DEFINITIONS))

    def test_forest_structure_request_preloads_structure_group(self):
        with patch.dict("os.environ", {"REMOTE_SENSING_PLUGINS_ENABLED": "true"}):
            tools, _, _ = _tools(
                True, "根据DSM和DTM构建CHM并提取林分结构和候选单木"
            )
        by_name = {tool.name: tool for tool in tools}
        self.assertFalse(by_name["build_canopy_height_model"].defer_loading)
        self.assertFalse(by_name["delineate_tree_candidates"].defer_loading)
        self.assertFalse(by_name["summarize_forest_structure"].defer_loading)
        self.assertTrue(by_name["invert_prosail"].defer_loading)

    def test_domain_authorization_failure_is_a_tool_result_not_run_crash(self):
        turn = 0

        async def model(messages, info):
            nonlocal turn
            turn += 1
            if turn == 1:
                yield {0: tool_call("inspect_uav_source", {
                    "kind": "folder", "folder_path": "E:\\not-authorized"
                }, "call_domain")}
            else:
                returned = [
                    part for message in messages for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                self.assertTrue(returned)
                self.assertFalse(returned[-1].content["ok"])
                yield "无法访问该目录，请提供可用路径。"

        async def check():
            with patch.dict("os.environ", {"REMOTE_SENSING_PLUGINS_ENABLED": "true"}):
                return [event async for event in stream_agent(
                    self.store, "alice", [],
                    [{"role": "user", "content": "检查无人机正射目录"}],
                    model=FunctionModel(stream_function=model),
                    workspace_registry=self.registry,
                )]

        events = asyncio.run(check())
        self.assertEqual(
            [event["type"] for event in events if event["type"] in {
                "tool_start", "tool_end", "error", "message"
            }],
            ["tool_start", "tool_end", "message"],
        )
        end = next(event for event in events if event["type"] == "tool_end")
        self.assertFalse(end["ok"])

    def test_pydantic_message_history_can_continue(self):
        database = Path(self.temp.name) / "agent-steps.sqlite3"
        run_id = "run_" + "a" * 32
        chat_id = str(uuid.uuid4())

        async def first_model(messages, info):
            yield "first answer"

        async def first_run():
            return [event async for event in stream_agent(
                self.store, "alice", [], [{"role": "user", "content": "first"}],
                model=FunctionModel(stream_function=first_model),
                workspace_registry=self.registry,
                agent_run_id=run_id + "_first", chat_id=chat_id,
                persistence_database=database,
            )]

        asyncio.run(first_run())

        async def second_model(messages, info):
            text = json.dumps([part.to_jsonable_python() if hasattr(part, "to_jsonable_python") else str(part)
                               for message in messages for part in message.parts])
            self.assertIn("first answer", text)
            self.assertIn("continue", text)
            yield "second answer"

        async def second_run():
            return [event async for event in stream_agent(
                self.store, "alice", [], [{"role": "user", "content": "continue"}],
                model=FunctionModel(stream_function=second_model),
                workspace_registry=self.registry,
                agent_run_id=run_id + "_second", chat_id=chat_id,
                persistence_database=database,
                resume_agent_run_id=run_id + "_first",
            )]

        events = asyncio.run(second_run())
        self.assertEqual(events[-2]["content"], "second answer")


if __name__ == "__main__":
    unittest.main()
