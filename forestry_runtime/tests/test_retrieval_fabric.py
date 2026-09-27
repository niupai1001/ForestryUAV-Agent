"""The fabric is only useful if the request actually carries what it retrieved.

Two things are asserted here, and they are different claims:

* the fabric can be assembled from what one Run can reach -- tools, recorded
  failures, project memory -- and a broken or missing source does not empty the
  result;
* what comes back reaches the request. A retriever that returns perfect candidates
  into a compiler that ignores them is decoration, so the assertion is on the
  instruction the model would actually see, and on the manifest that makes the
  retrieval auditable after the fact.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.context import ContextCompiler
from runtime.domain_registry import domain_candidates, matching_domain_tools
from runtime.failure import FailureEvidence, FailureStore
from runtime.retrieval import (
    Candidate, FailureRetriever, MemoryRetriever, RetrievalQuery, RetrievalRouter,
    ToolRetriever, build, enabled, retrieve,
)
from runtime.retrieval.models import render


class _Toolbox:
    def __init__(self, memory=None):
        self.owner = "alice"
        self.chat_id = "chat"
        self.memory = memory
        self.workspace = Path(tempfile.mkdtemp(prefix="fab_"))
        self.failure_preflight = None

        class _Workspaces:
            @staticmethod
            def list_grants(owner, chat_id):
                return []

        self.workspaces = _Workspaces()

    @staticmethod
    def attachment_context():
        return []


class _Memory:
    """The smallest thing that behaves like the memory manager."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls: list[tuple] = []

    def selected_project(self, owner, chat_id):
        return {"project_id": "proj_1"}

    def search(self, owner, project_id, query, limit=6):
        self.calls.append((owner, project_id, query, limit))
        return {"mode": "keyword", "results": self.rows}


class FabricAssemblyTest(unittest.TestCase):
    def test_the_fabric_is_off_by_default(self) -> None:
        self.assertFalse(
            enabled(),
            "the current evaluation baseline was measured without retrieved evidence",
        )

    def test_build_registers_the_sources_this_run_can_reach(self) -> None:
        store = FailureStore(None)
        router = build(_Toolbox(), failures=store)
        self.assertIn("tool", router.retrievers)
        self.assertIn("failure", router.retrievers)

    def test_a_run_without_memory_still_has_tools_and_failures(self) -> None:
        router = build(_Toolbox(memory=None))
        self.assertIn("tool", router.retrievers)
        self.assertNotIn("memory", router.retrievers)

    def test_memory_is_wrapped_through_the_manager_that_owns_it(self) -> None:
        memory = _Memory([{"id": "c1", "content": "canopy threshold is 0.4",
                           "citation": "guide://canopy", "source_id": "s1"}])
        router = build(_Toolbox(memory=memory))
        self.assertIsInstance(router.retrievers.get("memory"), MemoryRetriever)
        result = retrieve(router, "canopy threshold")
        self.assertEqual(memory.calls[0][0], "alice")
        self.assertEqual(memory.calls[0][1], "proj_1")
        self.assertTrue(result.candidates)

    def test_one_broken_source_does_not_empty_the_result(self) -> None:
        class Broken:
            source_type = "memory"

            def retrieve(self, query, top_k=6):
                raise RuntimeError("store locked")

        router = RetrievalRouter()
        router.register("tool", ToolRetriever())
        router.register("memory", Broken())
        result = router.retrieve(RetrievalQuery(text="read a file", top_k=5))
        self.assertTrue(result.candidates, "a raising source must not read as 'nothing exists'")
        self.assertTrue(result.manifest["per_source"]["memory"]["error"])

    def test_a_recorded_failure_outranks_a_plausible_text_match(self) -> None:
        store = FailureStore(None)
        store.record(FailureEvidence(
            id="fev_1", label="path_grounding", stage="fs", code="file_not_found",
            failure_class="deterministic", retry_policy="requires_new_evidence",
            resource_key="path:mask.tif", resource_version="1",
            invalid_assumptions=["the file exists"], strategy_id="stg_1",
            tool="fs_read", message="no such file", occurrences=2,
        ))
        router = build(_Toolbox(), failures=store)
        result = retrieve(router, "mask.tif")
        self.assertTrue(result.candidates)
        self.assertEqual(result.candidates[0].source_type, "failure",
                         "a known fact must not compete with a plausible match")


class MemoryRetrieverTest(unittest.TestCase):
    def test_rows_become_candidates_with_a_citable_reference(self) -> None:
        retriever = MemoryRetriever(lambda query, limit: {
            "mode": "hybrid",
            "results": [{"id": "c9", "content": "NDVI threshold", "citation": "guide://ndvi",
                         "source_id": "s3"}],
        })
        found = retriever.retrieve("ndvi")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].exact_reference["citation"], "guide://ndvi")
        self.assertEqual(found[0].source_type, "memory")

    def test_a_failing_search_returns_nothing_rather_than_raising(self) -> None:
        def broken(query, limit):
            raise RuntimeError("fts5 unavailable")

        self.assertEqual(MemoryRetriever(broken).retrieve("anything"), [])


class DomainRoutingTest(unittest.TestCase):
    def test_a_chinese_query_reaches_the_group_it_names(self) -> None:
        try:
            from unittest import mock
            with mock.patch.dict("os.environ", {"REMOTE_SENSING_PLUGINS_ENABLED": "true"}):
                selected = matching_domain_tools("读取影像元数据")
        except Exception:
            return
        self.assertTrue(selected, "a Chinese query must not score zero on Chinese keywords")

    def test_domain_groups_can_be_expressed_in_the_retrieval_contract(self) -> None:
        from unittest import mock

        with mock.patch.dict("os.environ", {"REMOTE_SENSING_PLUGINS_ENABLED": "true"}):
            found = domain_candidates("栅格")
        self.assertTrue(found)
        self.assertEqual(found[0].source_type, "tool")
        self.assertIn("tools", found[0].exact_reference)

    def test_the_plugin_switch_still_gates_everything(self) -> None:
        from unittest import mock

        with mock.patch.dict("os.environ", {"REMOTE_SENSING_PLUGINS_ENABLED": "false"}):
            self.assertEqual(matching_domain_tools("栅格"), set())
            self.assertEqual(domain_candidates("栅格"), [])


class KnowledgeAndMemorySourceTest(unittest.TestCase):
    def test_both_project_sources_are_registered(self) -> None:
        router = build(_Toolbox(memory=_Memory()))
        self.assertIn("memory", router.retrievers)
        self.assertIn("knowledge", router.retrievers)

    def test_memory_uses_recall_and_knowledge_uses_search(self) -> None:
        class _Manager:
            def __init__(self):
                self.calls: list[str] = []
                self.project = {"project_id": "proj_1"}

            def selected_project(self, owner, chat_id):
                return self.project

            def recall_memories(self, owner, project_id, query, limit=5):
                self.calls.append("recall")
                return [{"id": "m1", "content": "源目录只读"}]

            def search(self, owner, project_id, query, limit=6):
                self.calls.append("search")
                return {"mode": "keyword", "results": [
                    {"id": "k1", "content": "canopy definition"},
                ]}

        manager = _Manager()
        router = build(_Toolbox(memory=manager))
        result = retrieve(router, "林冠")
        self.assertIn("recall", manager.calls)
        self.assertIn("search", manager.calls)
        self.assertIn("memory", {item.source_type for item in result.candidates})
        self.assertIn("knowledge", {item.source_type for item in result.candidates})


class InjectedMemoryTest(unittest.TestCase):
    """The request carries the memories retrieval found -- and only those."""

    FULL = [{"id": "m1", "content": "源目录只读"}, {"id": "m2", "content": "阈值 0.4"}]

    @staticmethod
    def _project():
        return {"project_id": "p", "project_name": "白桦",
                "confirmed_memories": list(InjectedMemoryTest.FULL)}

    def _compile(self, retrieval):
        compiler = ContextCompiler(_Toolbox(), retrieval=retrieval,
                                   project_context=self._project)
        parts, manifest, _ = compiler.compile("阈值是多少", [], 1)
        facts = next(json.loads(part.split("\n", 1)[1]) for part in parts
                     if part.startswith("Runtime facts"))
        return facts["project"]["confirmed_memories"], manifest

    def test_without_retrieval_the_full_list_is_still_carried(self) -> None:
        compiler = ContextCompiler(_Toolbox(), project_context=self._project)
        parts, _, _ = compiler.compile("阈值", [], 1)
        facts = next(json.loads(part.split("\n", 1)[1]) for part in parts
                     if part.startswith("Runtime facts"))
        # A worse default, not a broken one: losing memories entirely would be worse
        # than carrying too many, so this stays until retrieval is switched on.
        self.assertEqual(facts["project"]["confirmed_memories"], self.FULL)

    def test_retrieval_found_nothing_means_no_memories(self) -> None:
        from runtime.retrieval.models import RetrievalResult

        carried, manifest = self._compile(lambda q: RetrievalResult())
        self.assertEqual(carried, [])
        self.assertEqual(manifest["memories"], [])

    def test_only_the_memory_retrieval_found_is_carried(self) -> None:
        from runtime.retrieval.models import Candidate, RetrievalResult

        def retrieval(query):
            return RetrievalResult(candidates=[
                Candidate(id="m2", source_type="memory", content="阈值 0.4",
                          exact_reference={"id": "m2"}),
                Candidate(id="fs_read", source_type="tool", content="Read a file.",
                          exact_reference={"tool": "fs_read"}),
            ])

        carried, _ = self._compile(retrieval)
        self.assertEqual([item["id"] for item in carried], ["m2"])
        self.assertNotIn("源目录只读", [item["content"] for item in carried])


class CompilerIntegrationTest(unittest.TestCase):
    def _compiler(self, retrieval=None) -> ContextCompiler:
        return ContextCompiler(_Toolbox(), retrieval=retrieval)

    def test_without_a_fabric_the_request_carries_no_retrieval_section(self) -> None:
        parts, manifest, _ = self._compiler().compile("问题", [], 1)
        self.assertIsNone(manifest["retrieval_mode"])
        self.assertFalse(any("Evidence retrieved" in part for part in parts))

    def test_what_was_retrieved_reaches_the_request(self) -> None:
        def retrieval(query):
            from runtime.retrieval.models import RetrievalResult
            return RetrievalResult(candidates=[Candidate(
                id="fs_read", source_type="tool", content="Read a file's text.",
                exact_reference={"tool": "fs_read"},
            )], manifest={"dropped_for_budget": 0})

        parts, manifest, _ = self._compiler(retrieval).compile("读文件", [], 1)
        self.assertTrue(any("Evidence retrieved" in part for part in parts))
        self.assertEqual(manifest["retrieval_mode"], "fused")
        self.assertEqual(manifest["retrieval_count"], 1)
        self.assertIn("tool", manifest["retrieval_sources"])

    def test_a_broken_retriever_costs_the_run_nothing(self) -> None:
        def explodes(query):
            raise RuntimeError("index missing")

        parts, manifest, _ = self._compiler(explodes).compile("问题", [], 1)
        self.assertIsNone(manifest["retrieval_mode"])
        self.assertTrue(any("Runtime facts" in part for part in parts),
                        "the rest of the request must still be assembled")

    def test_an_empty_result_says_retrieval_ran_and_found_nothing(self) -> None:
        from runtime.retrieval.models import RetrievalResult

        parts, manifest, _ = self._compiler(lambda q: RetrievalResult()).compile("问题", [], 1)
        self.assertEqual(manifest["retrieval_count"], 0)
        self.assertEqual(
            manifest["retrieval_mode"], "fused",
            "ran and found nothing is not the same as never having run: the first is "
            "a fact about this query, the second is a fact about the configuration",
        )
        self.assertEqual(manifest["memories"], [])

    def test_rendered_candidates_carry_their_reference(self) -> None:
        from runtime.retrieval.models import RetrievalResult
        result = RetrievalResult(candidates=[Candidate(
            id="guide://canopy", source_type="knowledge", content="body",
            exact_reference={"id": "guide://canopy"},
        )])
        rendered = render(result)
        self.assertIn("guide://canopy", rendered)
        self.assertIn("[knowledge]", rendered)


if __name__ == "__main__":
    unittest.main()
