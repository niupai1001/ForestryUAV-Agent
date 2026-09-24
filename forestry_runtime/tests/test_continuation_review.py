"""The harness reviews repeated exploration without replacing the agent loop."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from runtime.agent import REVIEWABLE_READ_TOOLS, stream_agent
from runtime.continuation_review import (
    ContinuationReview, ReviewDecision, review_proposed_action,
)
from runtime.storage import Store
from runtime.run_store import RunStore
from runtime.workspace import WorkspaceRegistry


def _call(name: str, arguments: dict, index: int):
    return {0: DeltaToolCall(
        name=name, json_args=json.dumps(arguments), tool_call_id=f"call_{index}",
    )}


class ContinuationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.chat_id = "review-chat"
        self.registry = WorkspaceRegistry(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, model_stream, reviewer):
        async def collect():
            return [event async for event in stream_agent(
                self.store, "alice", [],
                [{"role": "user", "content": "处理我刚发的影像"}],
                model=FunctionModel(stream_function=model_stream),
                workspace_registry=self.registry,
                continuation_reviewer=reviewer,
            )]
        return asyncio.run(collect())

    def test_repeated_cross_tool_search_gets_one_reconsideration_then_closes(self):
        calls = 0
        reviews = []

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield _call("fs_list", {"scope": "assets"}, calls)
            elif calls == 2:
                yield _call("fs_search", {"query": "missing.tif"}, calls)
            elif calls in (3, 4):
                yield _call("fs_list", {"scope": "workspace"}, calls)
            else:
                self.assertEqual(list(info.function_tools or []), [])
                yield "当前没有可处理的影像，请在本会话附加文件。"

        async def reviewer(payload):
            reviews.append(payload)
            return ReviewDecision("reconsider", "此前的读取没有找到输入，拟调用没有新的依据")

        events = self._run(model, reviewer)
        self.assertEqual(calls, 5)
        self.assertEqual(len(reviews), 2)
        self.assertIn("matches", reviews[0]["recent_actions_and_observations"][-1]["observation"])
        self.assertEqual(
            [e["decision"] for e in events if e["type"] == "continuation_review"],
            ["reconsider", "stop"],
        )
        self.assertEqual(events[-1]["state"], "paused")
        self.assertTrue(any("附加文件" in e.get("content", "") for e in events))
        blocked = [e for e in events if e["type"] == "tool_end" and
                   e.get("result", {}).get("failure", {}).get("code", "").startswith("continuation_")]
        self.assertEqual(len(blocked), 2)

    def test_review_allows_a_plausible_alternative_read(self):
        calls = 0
        reviews = []

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield _call("fs_list", {"scope": "assets"}, calls)
            elif calls == 2:
                yield _call("fs_search", {"query": "missing.tif"}, calls)
            elif calls == 3:
                yield _call("fs_list", {"scope": "workspace"}, calls)
            else:
                observations = [
                    part.content for message in messages for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                self.assertTrue(any(".runtime" in str(value) for value in observations))
                yield "已查看工作区。"

        async def reviewer(payload):
            reviews.append(payload)
            return ReviewDecision("allow", "工作区目录尚未检查")

        events = self._run(model, reviewer)
        self.assertEqual(calls, 4)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(events[-1]["type"], "done")
        self.assertNotEqual(events[-1].get("state"), "paused")
        self.assertTrue(any(e["type"] == "tool_end" and e["name"] == "fs_list" and
                            e.get("ok") for e in events))

    def test_a_real_state_change_resets_the_risk_signal(self):
        state = ContinuationReview()
        empty = {"ok": True, "data": {"matches": []}}
        state.observe("fs_search", {"query": "a"}, empty, True)
        state.observe("fs_search", {"query": "b"}, empty, True)
        self.assertTrue(state.should_review("fs_list", True))
        state.observe("fs_write", {"path": "new.txt"}, {"ok": True}, False)
        self.assertFalse(state.should_review("fs_list", True))

    def test_dynamic_job_observation_is_not_reviewed(self):
        self.assertNotIn("job_status", REVIEWABLE_READ_TOOLS)
        self.assertNotIn("job_wait", REVIEWABLE_READ_TOOLS)

    def test_reviewer_failure_does_not_block_the_proposed_tool(self):
        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield _call("fs_list", {"scope": "assets"}, calls)
            elif calls == 2:
                yield _call("fs_search", {"query": "missing.tif"}, calls)
            elif calls == 3:
                yield _call("fs_list", {"scope": "workspace"}, calls)
            else:
                yield "已检查。"

        async def reviewer(payload):
            raise RuntimeError("reviewer unavailable")

        events = self._run(model, reviewer)
        self.assertEqual(calls, 4)
        self.assertTrue(any(e["type"] == "continuation_review" and
                            e["decision"] == "allow" for e in events))
        self.assertTrue(any(e["type"] == "tool_end" and e["name"] == "fs_list" and
                            e.get("ok") for e in events))

    def test_validation_failures_are_available_to_the_next_review(self):
        calls = 0
        reviews = []

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield _call("fs_list", {"scope": "assets"}, calls)
            elif calls == 2:
                yield _call("fs_list", {"scope": "source", "path": "."}, calls)
            elif calls == 3:
                yield _call("fs_list", {"scope": "workspace"}, calls)
            else:
                yield "需要用户提供输入。"

        async def reviewer(payload):
            reviews.append(payload)
            return ReviewDecision("reconsider", "没有新的来源")

        self._run(model, reviewer)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(
            reviews[0]["recent_actions_and_observations"][-1]["arguments"]["scope"],
            "source",
        )

    def test_review_model_calls_are_counted_in_comparisons(self):
        from evaluation.compare_arms import _model_calls, _token_usage
        events = [
            {"type": "model_call", "number": 1},
            {"type": "model_call", "number": 2},
            {"type": "review_model_call", "action_id": "call_1"},
            {"type": "review_model_call", "action_id": "call_1"},
            {"type": "review_model_call", "action_id": "call_2"},
        ]
        self.assertEqual(_model_calls({}, events), 4)
        events.append({"type": "continuation_review", "usage": {
            "input_tokens": 80, "output_tokens": 12,
        }})
        self.assertEqual(
            _token_usage({"token_usage": {"input_tokens": 100, "output_tokens": 25}}, events),
            {"input_tokens": 180, "output_tokens": 37},
        )

    def test_run_store_counts_the_additional_review_request(self):
        runs = RunStore(self.temp.name)
        run = runs.create("alice", "accounting-chat", [
            {"role": "user", "content": "test"},
        ], [], True)
        runs.append(run["id"], {
            "type": "review_model_call", "action_id": "call_1", "review_number": 1,
        })
        self.assertEqual(runs.get(run["id"], "alice")["model_calls"], 1)

    def test_one_shot_reviewer_receives_raw_observations_without_tools(self):
        seen = []

        async def model(messages, info):
            seen.append((messages, info))
            yield '{"decision":"stop","reason":"重复读取"}'

        result = asyncio.run(review_proposed_action(
            model=FunctionModel(stream_function=model), user_goal="查文件",
            recent=[{"tool": "fs_list", "observation": '{"items":[]}'}],
            proposed={"tool": "fs_list", "arguments": {"path": "."}},
            available_inputs={"asset_count": 0}, model_settings=None,
        ))
        self.assertEqual(result.decision, "stop")
        self.assertEqual(result.usage["requests"], 1)
        self.assertEqual(list(seen[0][1].function_tools or []), [])
        self.assertIn("items", str(seen[0][0]))

    def test_default_harness_uses_a_bounded_model_review(self):
        main_calls = 0
        review_calls = 0

        async def model(messages, info):
            nonlocal main_calls, review_calls
            latest = "".join(
                part.content for part in messages[-1].parts
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            )
            if "recent_actions_and_observations" in latest:
                review_calls += 1
                self.assertEqual(list(info.function_tools or []), [])
                yield '{"decision":"stop","reason":"没有新的可访问输入"}'
                return
            main_calls += 1
            if main_calls == 1:
                yield _call("fs_list", {"scope": "assets"}, main_calls)
            elif main_calls == 2:
                yield _call("fs_search", {"query": "missing.tif"}, main_calls)
            elif main_calls == 3:
                yield _call("fs_list", {"scope": "workspace"}, main_calls)
            else:
                yield "请提供文件。"

        async def collect():
            with patch("runtime.agent._model", return_value=FunctionModel(stream_function=model)):
                return [event async for event in stream_agent(
                    self.store, "alice", [],
                    [{"role": "user", "content": "处理我刚发的影像"}],
                    workspace_registry=self.registry, max_requests=8,
                )]

        events = asyncio.run(collect())
        self.assertEqual(main_calls, 4)
        self.assertEqual(review_calls, 1)
        self.assertEqual(sum(e["type"] == "review_model_call" for e in events), 1)
        self.assertEqual(
            [e["decision"] for e in events if e["type"] == "continuation_review"],
            ["reconsider"],
        )
        self.assertEqual(events[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main()
