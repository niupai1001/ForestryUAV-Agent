"""Whatever ends a turn, the user hears it from the model, not from the Runtime.

An exhausted budget used to emit a template sentence and stop. The model -- which had
just spent 129 calls and knew exactly what it had and had not done -- was never asked
to account for any of it, because the limit is raised before the request hook that
gives the pause path its closing turn.

These tests pin the two properties that make the wrap-up safe: it cannot become more
work, and a wrap-up that cannot be produced leaves the real cause visible instead of
being replaced by a second failure.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest

from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from runtime.agent import _final_statement


def _history() -> list:
    return [ModelResponse(parts=[TextPart("我先看一下影像。")])]


class _Capture:
    """Records the request a model actually receives, through a real agent."""

    def __init__(self, output: str = "我读到了影像，但没有产出掩膜。",
                 fail: Exception | None = None):
        self.output = output
        self.fail = fail
        self.requests: list[dict] = []

    def model(self) -> FunctionModel:
        async def stream(messages, info):
            self.requests.append({"messages": messages, "info": info})
            if self.fail is not None:
                raise self.fail
            yield self.output

        return FunctionModel(stream_function=stream)


def _run(capture: _Capture, history=None, reason="已用完本轮的 1 次模型调用预算。"):
    return asyncio.run(_final_statement(
        model=capture.model(), instructions="SYSTEM",
        history=_history() if history is None else history,
        reason=reason, model_settings=None,
    ))


class FinalStatementTests(unittest.TestCase):
    def test_the_wrap_up_carries_the_run_history_plus_the_reason(self):
        capture = _Capture()
        _run(capture)
        self.assertEqual(len(capture.requests), 1)
        messages = capture.requests[0]["messages"]
        self.assertEqual(len(messages), 2, "the run history plus the closing instruction")
        last = messages[-1]
        text = "".join(part.content for part in last.parts
                       if isinstance(part, UserPromptPart))
        self.assertIn("模型调用预算", text)
        # It asks for an account of the work, addressed to the user.
        self.assertIn("用户", text)

    def test_the_wrap_up_offers_no_tools(self):
        """A closing statement must not be able to start new work."""
        from pydantic_ai.models.function import FunctionModel as _FM

        seen: list[dict] = []

        async def stream(messages, info):
            seen.append({
                "tools": list(getattr(info, "function_tools", ()) or ()),
                "instructions": getattr(info, "instructions", None),
            })
            yield "说明"

        statement = asyncio.run(_final_statement(
            model=_FM(stream_function=stream), instructions="SYSTEM",
            history=_history(), reason="预算用尽", model_settings=None,
        ))
        self.assertEqual(seen[0]["tools"], [], "the closing request must expose no tools")
        self.assertEqual(statement, "说明")
        self.assertIsInstance(statement, str)

    def test_a_statement_is_returned_when_the_model_answers(self):
        capture = _Capture(output="我读到了影像，但树冠掩膜没交付。")
        self.assertEqual(_run(capture), "我读到了影像，但树冠掩膜没交付。")

    def test_an_unreachable_model_yields_no_statement(self):
        """The caller must then report the real cause, not a second failure."""
        capture = _Capture(fail=RuntimeError("model unreachable"))
        self.assertIsNone(_run(capture))

    def test_an_empty_answer_is_not_a_statement(self):
        self.assertIsNone(_run(_Capture(output="   ")))

    def test_no_history_means_no_request_at_all(self):
        capture = _Capture()
        self.assertIsNone(_run(capture, history=[]))
        self.assertEqual(capture.requests, [], "an empty history cannot be explained from")


class BudgetExhaustionTests(unittest.TestCase):
    """The end-to-end shape: a spent budget produces words, not a template."""

    def test_a_spent_budget_asks_the_model_to_explain(self):
        from pydantic_ai.exceptions import UsageLimitExceeded

        from runtime.agent import stream_agent
        from runtime.storage import Store
        from runtime.workspace import WorkspaceRegistry

        calls = {"n": 0}
        closing_requests: list[list] = []

        async def model(messages, info):
            calls["n"] += 1
            if calls["n"] == 1:
                raise UsageLimitExceeded("request_limit of 1")
            closing_requests.append(messages)
            yield "预算已用尽：我读到了影像，但没有产出掩膜。"

        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.chat_id = "budget-test"
            registry = WorkspaceRegistry(root)

            async def run():
                return [event async for event in stream_agent(
                    store, "alice", [],
                    [{"role": "user", "content": "提取树冠"}],
                    model=FunctionModel(stream_function=model),
                    workspace_registry=registry,
                    max_requests=1,
                )]

            events = asyncio.run(run())

        messages = [e for e in events if e.get("type") == "message"]
        errors = [e for e in events if e.get("type") == "error"]
        done = [e for e in events if e.get("type") == "done"]

        self.assertTrue(
            messages,
            f"no model words reached the user; events={events}",
        )
        self.assertIn("预算已用尽", "".join(e["content"] for e in messages))
        # The template sentence is not what the user is shown.
        self.assertFalse(
            any("次模型调用预算" in str(e.get("content") or "") for e in errors),
            f"a template error was shown instead of the model's account: {errors}",
        )
        # The closing request happened after the failure and carried the user's ask.
        self.assertTrue(closing_requests, "the model was never asked to explain")
        joined = "".join(
            part.content for message in closing_requests[0]
            for part in getattr(message, "parts", [])
            if isinstance(part, UserPromptPart)
        )
        self.assertIn("提取树冠", joined)
        self.assertEqual(done[-1]["state"], "paused")


if __name__ == "__main__":
    unittest.main()
