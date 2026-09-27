"""Tool routing closes the loop between retrieval and what the model can see.

Three claims, and the order matters:

* a domain group is a retrieval source, so which schemas open is decided on the
  same ranked list that produced the context -- not by a second, unseen scorer;
* the routing verdict is a *set of tools*, and a query that reaches nothing leaves
  routing silent rather than deferring everything;
* with the fabric off nothing changes: selection falls back to the keyword
  pre-pass, so the frozen visible-schema contract still describes the default.
"""
from __future__ import annotations

import unittest
from unittest import mock

from runtime.domain_registry import DOMAIN_TOOL_GROUPS, matching_domain_tools
from runtime.retrieval import DomainRetriever, build, retrieve


RS_ON = {"REMOTE_SENSING_PLUGINS_ENABLED": "true"}
RS_OFF = {"REMOTE_SENSING_PLUGINS_ENABLED": "false"}
FABRIC_ON = {"RETRIEVAL_FABRIC_ENABLED": "true"}
FABRIC_OFF = {"RETRIEVAL_FABRIC_ENABLED": "false"}


class _Toolbox:
    def __init__(self):
        from pathlib import Path
        import tempfile

        self.owner = "alice"
        self.chat_id = "chat"
        self.memory = None
        self.workspace = Path(tempfile.mkdtemp(prefix="route_"))
        self.failure_preflight = None

        class _Workspaces:
            @staticmethod
            def list_grants(owner, chat_id):
                return []

        self.workspaces = _Workspaces()

    @staticmethod
    def attachment_context():
        return []


def _group(name: str):
    for group in DOMAIN_TOOL_GROUPS:
        if group.name == name:
            return group
    raise AssertionError(f"no domain group named {name}")


class DomainSourceTest(unittest.TestCase):
    def test_the_group_enters_the_same_ranked_list_as_the_tools(self) -> None:
        with mock.patch.dict("os.environ", {**RS_ON}):
            router = build(_Toolbox())
            self.assertIn("domain", router.retrievers)

    def test_the_switch_still_gates_the_source(self) -> None:
        with mock.patch.dict("os.environ", {**RS_OFF}):
            router = build(_Toolbox())
            self.assertNotIn("domain", router.retrievers)

    def test_a_query_reaches_the_group_through_the_router(self) -> None:
        with mock.patch.dict("os.environ", {**RS_ON}):
            router = build(_Toolbox())
            result = retrieve(router, "解压这个压缩包")
        names = [
            item.exact_reference.get("domain_group")
            for item in result.candidates
            if item.exact_reference and "domain_group" in item.exact_reference
        ]
        self.assertIn("archive-import", names)

    def test_the_retriever_alone_answers_in_the_retrieval_contract(self) -> None:
        with mock.patch.dict("os.environ", {**RS_ON}):
            found = DomainRetriever().retrieve("林冠高度", top_k=5)
        self.assertTrue(found)
        self.assertEqual("tool", found[0].source_type)
        self.assertIn("tools", found[0].exact_reference)

    def test_a_broken_registry_costs_the_group_not_the_result(self) -> None:
        with mock.patch.dict("os.environ", {**RS_ON}):
            with mock.patch("runtime.domain_registry.domain_candidates",
                            side_effect=RuntimeError("boom")):
                self.assertEqual(DomainRetriever().retrieve("林冠"), [])


class RoutingSelectionTest(unittest.TestCase):
    def test_no_router_means_routing_did_not_decide(self) -> None:
        from runtime.agent import _routing_selection

        self.assertIsNone(_routing_selection(None, "解压这个压缩包"))

    def test_an_empty_query_is_not_a_verdict(self) -> None:
        from runtime.agent import _routing_selection

        with mock.patch.dict("os.environ", {**RS_ON}):
            self.assertIsNone(_routing_selection(build(_Toolbox()), "   "))

    def test_a_group_is_expanded_to_the_tools_it_stands_for(self) -> None:
        from runtime.agent import _routing_selection

        with mock.patch.dict("os.environ", {**RS_ON}):
            selected = _routing_selection(build(_Toolbox()), "解压这个压缩包")
        self.assertIsNotNone(selected)
        self.assertTrue({"inspect_zip", "extract_zip"} <= selected)

    def test_a_retrieval_that_returns_nothing_leaves_routing_silent(self) -> None:
        """Empty is not "defer everything": the pre-pass still decides."""
        from runtime.agent import _routing_selection

        with mock.patch.dict("os.environ", {**RS_ON}):
            selected = _routing_selection(
                build(_Toolbox()), "zzzz qqqq  unrelated  gibberish"
            )
        self.assertIsNone(selected)

    def test_a_broken_retrieval_does_not_cost_the_run_its_schemas(self) -> None:
        from runtime.agent import _routing_selection

        router = build(_Toolbox())
        with mock.patch("runtime.retrieval.retrieve", side_effect=RuntimeError("boom")):
            self.assertIsNone(_routing_selection(router, "林冠高度"))


class ToolVisibilityTest(unittest.TestCase):
    def _undeferred(self, text: str, selected):
        from runtime.agent import _tools

        with mock.patch.dict("os.environ", {**RS_ON}):
            tools, _, _ = _tools(True, text, selected=selected)
        deferred = {tool.name for tool in tools if not tool.defer_loading}
        return deferred

    def test_the_verdict_is_what_undeferring_follows(self) -> None:
        from runtime.agent import _routing_selection

        with mock.patch.dict("os.environ", {**RS_ON}):
            router = build(_Toolbox())
            selected = _routing_selection(router, "解压这个压缩包")
            undeferred = self._undeferred("解压这个压缩包", selected)
        self.assertIn("extract_zip", undeferred)
        self.assertIn("inspect_zip", undeferred)
        self.assertNotIn("build_canopy_height_model", undeferred)

    def test_without_a_verdict_the_keyword_pre_pass_still_decides(self) -> None:
        text = "林冠高度"
        with mock.patch.dict("os.environ", {**RS_ON, **FABRIC_OFF}):
            expected = matching_domain_tools(text)
            undeferred = self._undeferred(text, None)
        self.assertEqual(expected, undeferred & expected)
        self.assertTrue(undeferred, "the default must still open the schemas it names")

    def test_an_unrelated_verdict_defers_the_domain(self) -> None:
        undeferred = self._undeferred("林冠高度", {"inspect_zip"})
        self.assertIn("inspect_zip", undeferred)
        self.assertNotIn("delineate_tree_candidates", undeferred)

    def test_the_frozen_default_schema_is_untouched(self) -> None:
        """Fabric off must produce byte-identical selection to the pre-pass."""
        from runtime.agent import _routing_selection

        text = "生成冠层高度模型并勾出树冠"
        with mock.patch.dict("os.environ", {**RS_ON, **FABRIC_OFF}):
            from runtime.agent import _fabric_router

            self.assertIsNone(_fabric_router(_Toolbox()))
            self.assertIsNone(_routing_selection(None, text))
            baseline = self._undeferred(text, None)
        with mock.patch.dict("os.environ", {**RS_ON, **FABRIC_ON}):
            routed = self._undeferred(text, _routing_selection(build(_Toolbox()), text))
        self.assertEqual(baseline, routed,
                         "a relevant query must not lose a schema the pre-pass opened")


if __name__ == "__main__":
    unittest.main()
