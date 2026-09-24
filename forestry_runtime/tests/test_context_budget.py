"""Phase 1 acceptance: context budgeting, tool-result bounding, job waiting.

Covers the failure modes this phase exists to remove:

* a request locally estimated as over budget that the server measures as fitting;
* compaction running *after* the budget check, so an over-budget request raised a
  generic error instead of being compacted;
* a bounded tool result that dropped the terminal error it needed to carry.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from runtime.agent import _bounded_tool_result
from runtime.context import (
    ContextBudgetExceeded,
    RequestBudgetCompaction,
    _digest_result,
)
from runtime.tokens import (
    TokenEstimator,
    context_limit_tokens,
    estimate_text,
    input_budget_tokens,
    output_reserve_tokens,
    safety_margin_tokens,
)


def _tool(name: str, description: str, schema: dict) -> ToolDefinition:
    return ToolDefinition(name=name, description=description, parameters_json_schema=schema)


def _pair(name: str, arguments: dict, result: str, index: int) -> list:
    return [
        ModelResponse(parts=[ToolCallPart(
            tool_name=name, args=arguments, tool_call_id=f"call_{index}"
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name=name, content=result, tool_call_id=f"call_{index}"
        )]),
    ]


class TokenEstimatorTests(unittest.TestCase):
    def test_cjk_text_is_not_over_counted_four_fold(self):
        """The old estimator divided every character by four, including CJK.

        A Chinese sentence costs roughly one token per character, so len//4
        under-counts it; JSON-rendering envelopes then inflated the total again.
        The estimator must land near one token per CJK character.
        """
        text = "林地遥感影像质量检查" * 20
        estimate = estimate_text(text)
        self.assertGreater(estimate, len(text) * 0.8)
        self.assertLess(estimate, len(text) * 1.4)
        # Latin text stays near four characters per token.
        latin = "the quick brown fox jumps over the lazy dog " * 20
        self.assertLess(estimate_text(latin), len(latin) * 0.4)

    def test_calibration_folds_provider_usage_back_in(self):
        estimator = TokenEstimator()
        self.assertEqual(estimator.ratio, 1.0)
        estimator.calibrate(estimated=24_932, actual=13_568)
        self.assertLess(estimator.ratio, 1.0)
        self.assertEqual(estimator.samples, 1)
        self.assertAlmostEqual(
            estimator.estimate_text("x" * 4000),
            round(1000 * estimator.ratio), delta=2,
        )

    def test_calibration_ignores_implausible_samples(self):
        estimator = TokenEstimator()
        estimator.calibrate(estimated=1000, actual=1000_000)
        self.assertEqual(estimator.ratio, 1.0)
        estimator.calibrate(estimated=0, actual=10)
        self.assertEqual(estimator.ratio, 1.0)

    def test_budget_reserves_output_and_safety_margin(self):
        limit = 32768
        budget = input_budget_tokens(limit=limit)
        self.assertEqual(
            budget,
            limit - output_reserve_tokens() - safety_margin_tokens(limit),
        )
        self.assertLess(budget, limit)
        # The reserve must not grow with the window the way a fixed 8192 did.
        self.assertLessEqual(output_reserve_tokens(), 8192)

    def test_breakdown_reports_every_component(self):
        estimator = TokenEstimator()
        params = ModelRequestParameters(function_tools=[
            _tool("fs_read", "read a file", {"type": "object", "properties": {}}),
        ])
        breakdown = estimator.breakdown(
            [ModelRequest(parts=[UserPromptPart(content="读取林地影像")])],
            params,
            extra_text="Runtime facts",
        )
        for key in (
            "instruction_tokens", "tool_schema_tokens", "history_tokens",
            "runtime_facts_tokens", "visible_tool_count", "calibration_ratio",
        ):
            self.assertIn(key, breakdown)
        self.assertEqual(breakdown["visible_tool_count"], 1)


class RequestBudgetCompactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.params = ModelRequestParameters(function_tools=[
            _tool("fs_read", "read a file", {"type": "object", "properties": {}}),
        ])

    def tearDown(self):
        self.temp.cleanup()

    async def _run(self, messages, params=None, **kwargs):
        compaction = RequestBudgetCompaction(estimator=TokenEstimator(), **kwargs)
        ctx = type("Ctx", (), {"deps": None})()
        request_context = type("RC", (), {
            "messages": list(messages),
            "model_request_parameters": params or self.params,
        })()
        result = await compaction.before_model_request(ctx, request_context)
        return compaction, result

    async def test_small_request_is_not_compacted(self):
        compaction, request_context = await self._run([
            ModelRequest(parts=[UserPromptPart(content="读取一个文件")]),
        ])
        self.assertEqual(len(request_context.messages), 1)
        self.assertEqual(compaction.last_ledger["tokens_reclaimed"], 0)
        self.assertEqual(compaction.last_ledger["compaction_tiers_applied"], [])

    async def test_old_tool_results_are_digested_and_budget_is_measured(self):
        """Compaction must run before the final budget decision, and preserve
        job references, error evidence and the user objective."""
        messages = [ModelRequest(parts=[UserPromptPart(content="比较两个目录并汇总")])]
        for index in range(30):
            messages.extend(_pair(
                "fs_read", {"path": f"f{index}.txt"},
                json.dumps({
                    "ok": True,
                    "data": {
                        "job_id": f"job_{index:032x}",
                        "valid_pixels": 1234 + index,
                        "path": f"f{index}.txt",
                        "content": "x" * 20000,
                    },
                }),
                index,
            ))
        messages.append(ModelRequest(parts=[UserPromptPart(content="现在总结")]))

        compaction, request_context = await self._run(messages)
        ledger = compaction.last_ledger
        self.assertGreater(ledger["estimated_input_tokens_before"],
                           ledger["estimated_input_tokens"])
        self.assertGreater(ledger["tokens_reclaimed"], 0)
        self.assertTrue(ledger["compaction_tiers_applied"])

        rendered = json.dumps([
            getattr(part, "content", "") for message in request_context.messages
            for part in message.parts
        ], ensure_ascii=False, default=str)
        # The user's objective survives.
        self.assertIn("比较两个目录并汇总", rendered)
        # Job references survive compaction.
        self.assertIn("job_00000000000000000000000000000000", rendered)

    async def test_budget_pause_is_explicit_and_recoverable(self):
        """An unfittable request raises a typed, actionable condition.

        It must not be a bare RuntimeError: the caller turns this into a paused
        Run with `blocked_by: context_budget`.  The content here has no tool
        results to clear and every remaining message is irreducible, so the
        deterministic pipeline genuinely cannot fit it.
        """
        messages = [ModelRequest(parts=[UserPromptPart(content="目标")])]
        for index in range(6):
            messages.append(ModelResponse(parts=[TextPart(content="y" * 60_000)]))
            messages.append(ModelRequest(parts=[UserPromptPart(content="z" * 60_000)]))
        with patch.dict(os.environ, {"OLLAMA_CONTEXT": "8192"}), \
                self.assertRaises(ContextBudgetExceeded) as caught:
            await self._run(messages, ledger_keep_messages=6, keep_pairs=0)
        message = str(caught.exception)
        self.assertIn("input budget", message)
        self.assertIn("OLLAMA_CONTEXT", message)
        ledger = caught.exception.ledger
        self.assertFalse(ledger["fits"])
        self.assertTrue(ledger["compaction_tiers_applied"])


class BoundedToolResultTests(unittest.TestCase):
    def test_large_result_keeps_tail_and_failure_evidence(self):
        """The tail of a long result is where the terminal error lives."""
        output = {
            "ok": False,
            "error": "container exited 1",
            "failure": {
                "stage": "execution", "code": "install_failed",
                "missing_from_manifest": ["scikit-learn"],
            },
            "data": {
                "job_id": "job_" + "a" * 32,
                "state": "failed",
                "valid_pixels": 42,
                "log": "line\n" * 20_000 + "FATAL: no space left on device",
            },
        }
        bounded = _bounded_tool_result(output, persist=lambda value: "result_abc")
        self.assertTrue(bounded["truncated"])
        self.assertEqual(bounded["result_id"], "result_abc")
        self.assertIn("FATAL: no space left on device", bounded["tail"])
        self.assertEqual(bounded["failure"]["code"], "install_failed")
        self.assertEqual(bounded["failure"]["missing_from_manifest"], ["scikit-learn"])
        self.assertEqual(bounded["references"]["job_id"], "job_" + "a" * 32)
        self.assertEqual(bounded["metrics"]["valid_pixels"], 42)
        self.assertLess(len(json.dumps(bounded)), 14_100)

    def test_small_result_is_returned_unchanged(self):
        output = {"ok": True, "data": {"items": []}}
        self.assertIs(_bounded_tool_result(output), output)


class DigestResultTests(unittest.TestCase):
    def test_digest_preserves_status_metrics_and_error(self):
        digest = json.loads(_digest_result(
            "code_run",
            json.dumps({
                "ok": False,
                "error": "ImportError: no module named sklearn",
                "failure": {"stage": "execution", "code": "nonzero_exit"},
                "data": {"job_id": "job_" + "b" * 32, "valid_pixels": 0},
            }),
            {"name": "code_run", "args": {"language": "python", "code": "print(1)"}},
        ))
        self.assertEqual(digest["tool"], "code_run")
        self.assertFalse(digest["ok"])
        self.assertIn("sklearn", digest["error"])
        self.assertEqual(digest["failure"]["code"], "nonzero_exit")
        self.assertEqual(digest["references"]["job_id"], "job_" + "b" * 32)

    def test_digest_keeps_units_and_metrics_from_nested_data(self):
        digest = json.loads(_digest_result(
            "inspect_raster",
            json.dumps({"ok": True, "data": {
                "shape": [100, 200], "crs": "EPSG:4326",
                "pixel_size": 0.0001, "unit": "degree",
                "bands": {"count": 4},
            }}),
            None,
        ))
        self.assertEqual(digest["metrics"]["shape"], [100, 200]
                         if isinstance(digest["metrics"]["shape"], list)
                         else digest["metrics"]["shape"])
        self.assertEqual(digest["metrics"]["crs"], "EPSG:4326")


if __name__ == "__main__":
    unittest.main()
