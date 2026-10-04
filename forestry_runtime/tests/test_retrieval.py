"""Retrieval has to be measurable, or it is decoration.

The failure this test set exists to catch is the one that is invisible from the
outside: the correct tool exists in the registry, is never returned in the candidate
set, and the Run then fails for want of a capability it had all along. A model asked
whether it knew about a tool will say yes; only a frozen query set with a known answer
can show whether retrieval actually offered it.

Recall@5 is the metric because the model sees a bounded list. A correct answer ranked
twelfth is, to the model, indistinguishable from no answer.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime.failure import FailureStore
from runtime.retrieval import (
    Candidate, FailureRetriever, RetrievalQuery, RetrievalRouter, TaskStateRetriever,
    ToolRetriever, apply_budget, build, estimate_tokens, rrf_fuse, terms_of,
)
from runtime.retrieval.fusion import diversify

#: Frozen bilingual set. Each query has one tool it is asking for; the assertion is
#: that the tool appears in the top five. Bilingual on purpose: the Chinese half is
#: where a purely lexical retriever silently scores zero.
FROZEN_QUERIES: list[tuple[str, str]] = [
    ("read the contents of a file", "fs_read"),
    ("读取文件内容", "fs_read"),
    ("list what is in this directory", "fs_list"),
    ("列出目录下的文件", "fs_list"),
    ("search the workspace for a string", "fs_search"),
    ("搜索包含某字符串的文件", "fs_search"),
    ("write a new file into the workspace", "fs_write"),
    ("新建一个文件", "fs_write"),
    ("edit an existing file in place", "fs_edit"),
    ("修改已有文件", "fs_edit"),
    ("run this python script", "code_run"),
    ("运行一段 python 代码", "code_run"),
    ("install a missing dependency", "dependency_install"),
    ("安装缺少的依赖包", "dependency_install"),
    ("check whether the module is importable", "environment_check"),
    ("检查模块能否导入", "environment_check"),
    ("what is the status of the running job", "job_status"),
    ("查看作业状态", "job_status"),
    ("wait for the job to finish", "job_wait"),
    ("等待作业完成", "job_wait"),
    ("read the job log", "job_log"),
    ("查看作业日志", "job_log"),
    ("cancel the running job", "job_cancel"),
    ("取消正在运行的作业", "job_cancel"),
    ("search the knowledge base for guidance", "knowledge_search"),
    ("检索知识库指南", "knowledge_search"),
    ("read the body of a guide", "knowledge_read"),
    ("读取指南正文", "knowledge_read"),
    ("record the work plan for this task", "work_plan"),
    ("记录本次任务的工作计划", "work_plan"),
    ("preview the artifact", "artifacts_preview"),
    ("预览产物", "artifacts_preview"),
    ("inspect the produced artifact", "artifacts_inspect"),
    ("查看产物元信息", "artifacts_inspect"),
]


class ToolRecallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.retriever = ToolRetriever()

    def test_recall_at_5_over_the_frozen_set(self):
        missed = []
        for query, expected in FROZEN_QUERIES:
            found = [item.id for item in self.retriever.retrieve(query, top_k=5)]
            if expected not in found:
                missed.append((query, expected, found))
        recall = 1.0 - len(missed) / len(FROZEN_QUERIES)
        self.assertGreaterEqual(
            recall, 0.95,
            "tool Recall@5 fell below 0.95; misses: %s" % (missed[:6],),
        )

    def test_recall_at_5_holds_for_each_language(self):
        for label, queries in (
            ("en", [pair for pair in FROZEN_QUERIES if pair[0].isascii()]),
            ("zh", [pair for pair in FROZEN_QUERIES if not pair[0].isascii()]),
        ):
            hits = sum(
                1 for query, expected in queries
                if expected in [item.id for item in self.retriever.retrieve(query, top_k=5)]
            )
            self.assertGreaterEqual(
                hits / len(queries), 0.95,
                "%s Recall@5 = %.2f" % (label, hits / len(queries)),
            )

    def test_naming_a_tool_is_a_lookup_not_a_match(self):
        found = self.retriever.retrieve("fs_read", top_k=5)
        self.assertEqual(found[0].id, "fs_read")

    def test_an_empty_query_returns_nothing(self):
        self.assertEqual(self.retriever.retrieve("", top_k=5), [])


class FailureRetrievalTests(unittest.TestCase):
    def test_a_recorded_failure_is_retrievable_as_a_fact(self):
        store = FailureStore(Path(tempfile.mkdtemp()))
        preflight_store = store
        from runtime.failure import FailurePreflight, ResourceVersions
        pre = FailurePreflight(preflight_store, ResourceVersions())
        pre.record("fs_read", {"path": "C:/flight/A"}, {
            "ok": False, "outcome_ok": False,
            "failure": {"stage": "preconditions", "code": "source_path_not_found",
                        "requested_path": "C:/flight/A"},
        })
        found = FailureRetriever(store).retrieve("C:/flight/A", top_k=5)
        self.assertTrue(found)
        self.assertEqual(found[0].source_type, "failure")
        self.assertIn("exists", found[0].content)

    def test_failure_facts_outrank_text_matches(self):
        """A known false assumption must not compete on rank with a plausible match."""
        store = FailureStore(Path(tempfile.mkdtemp()))
        from runtime.failure import FailurePreflight, ResourceVersions
        pre = FailurePreflight(store, ResourceVersions())
        pre.record("fs_read", {"path": "C:/flight/A"}, {
            "ok": False, "outcome_ok": False,
            "failure": {"stage": "preconditions", "code": "source_path_not_found",
                        "requested_path": "C:/flight/A"},
        })
        router = RetrievalRouter({
            "failure": FailureRetriever(store),
            "tool": ToolRetriever(),
        })
        result = router.retrieve(RetrievalQuery(text="read the flight A directory",
                                                source_types=("failure", "tool")))
        self.assertEqual(result.candidates[0].source_type, "failure")


class FusionTests(unittest.TestCase):
    def _candidate(self, ident, source):
        return Candidate(id=ident, source_type=source, content="x")

    def test_rrf_folds_repeat_findings_of_one_item_into_one_candidate(self):
        """Found by two passes is one fact, scored higher -- not two facts."""
        left = [self._candidate("a", "tool"), self._candidate("b", "tool")]
        right = [self._candidate("b", "tool"), self._candidate("a", "tool")]
        fused = rrf_fuse([left, right])
        self.assertEqual(len(fused), 2)
        self.assertTrue(all(item.fusion_score > 0 for item in fused))

    def test_identity_includes_the_source_type(self):
        """The same id from two sources is two candidates, not a duplicate."""
        fused = rrf_fuse([[self._candidate("a", "tool"), self._candidate("a", "memory")]])
        self.assertEqual(len(fused), 2)

    def test_fusion_deduplicates_by_source_and_id(self):
        left = [self._candidate("a", "tool")]
        right = [self._candidate("a", "tool")]
        self.assertEqual(len(rrf_fuse([left, right])), 1)

    def test_diversity_stops_one_source_taking_every_slot(self):
        many = [self._candidate("t%d" % index, "tool") for index in range(6)]
        many += [self._candidate("m1", "memory")]
        kept = diversify(many, per_source=3)
        self.assertEqual(len([item for item in kept if item.source_type == "tool"]), 3)


class BudgetTests(unittest.TestCase):
    def test_the_budget_is_respected_and_the_cut_is_recorded(self):
        candidates = [Candidate(id=str(index), source_type="memory",
                                content="x" * 400) for index in range(10)]
        kept, manifest = apply_budget(candidates, 400)
        self.assertLess(len(kept), len(candidates))
        self.assertEqual(manifest["dropped_for_budget"], len(candidates) - len(kept))
        self.assertLessEqual(manifest["tokens_used"], 400)

    def test_cjk_is_not_undercounted(self):
        """Undercounting Chinese would overflow the request mid-flight."""
        self.assertGreater(estimate_tokens("检查目录中的文件" * 10),
                           estimate_tokens("list the directory" * 3))


class ScopeTests(unittest.TestCase):
    def test_another_projects_memory_cannot_leak_in(self):
        router = RetrievalRouter({"memory": _StubRetriever()})
        result = router.retrieve(RetrievalQuery(text="anything", source_types=("memory",),
                                                project_id="p1"))
        self.assertEqual([item.id for item in result.candidates], ["own"])


class _StubRetriever:
    source_type = "memory"

    def retrieve(self, query, top_k=10):
        return [
            Candidate(id="other", source_type="memory", content="leak",
                      metadata={"project_id": "p2"}),
            Candidate(id="own", source_type="memory", content="ok",
                      metadata={"project_id": "p1"}),
        ]


class TaskStateRetrievalTests(unittest.TestCase):
    """The plan's state is a source, not only a broadcast the compaction may drop."""

    def _store(self):
        from runtime.plan import PlanStore, WorkPlan

        store = PlanStore(Path(tempfile.mkdtemp(prefix="plan_")))
        store._write(WorkPlan(
            objective="build a canopy mask",
            requirements=[
                {"id": "r1", "text": "classify the canopy into open and closed stands",
                 "status": "open"},
                {"id": "r2", "text": "report the closed-stand area", "status": "satisfied"},
            ],
            open_questions=[{"question": "which threshold applies to sparse stands"}],
        ))
        return store

    def test_an_open_requirement_is_retrievable_and_a_settled_one_is_not(self):
        store = self._store()
        found = TaskStateRetriever(lambda: store).retrieve("classify canopy", top_k=6)
        ids = [item.id for item in found]
        self.assertIn("requirement:r1", ids)
        self.assertNotIn("requirement:r2", ids)

    def test_an_open_question_is_retrievable(self):
        store = self._store()
        found = TaskStateRetriever(lambda: store).retrieve("threshold for sparse stands")
        self.assertTrue(any(item.id.startswith("question:") for item in found))
        self.assertEqual(found[0].source_type, "task_state")

    def test_a_broken_store_costs_the_source_not_the_run(self):
        def broken():
            raise RuntimeError("boom")
        self.assertEqual(TaskStateRetriever(broken).retrieve("anything"), [])

    def test_an_empty_plan_retrieves_nothing(self):
        from runtime.plan import PlanStore

        store = PlanStore(Path(tempfile.mkdtemp(prefix="plan_")))
        self.assertEqual(TaskStateRetriever(lambda: store).retrieve("canopy"), [])


class FabricWiringTests(unittest.TestCase):
    """A source the fabric builds but never registers is indistinguishable from none."""

    def test_the_failure_source_registers_before_the_first_tool_call(self):
        """The failure store is created lazily on first tool execution -- *after* the
        router is built. Wiring that only read it off the deps registered nothing,
        ever, and the first request's retrieval silently had no failure source."""
        from runtime.agent import AgentDependencies, _fabric_router

        class _Box:
            owner = "alice"
            chat_id = "chat"
            memory = None
            workspace = Path(tempfile.mkdtemp(prefix="fab_"))

        box = _Box()
        deps = AgentDependencies(
            toolbox=box, cancelled=lambda: False, pause_requested=lambda: False,
        )
        with mock.patch.dict("os.environ", {"RETRIEVAL_FABRIC_ENABLED": "true"}):
            router = _fabric_router(box, deps)
        self.assertIsNotNone(router)
        self.assertIn("failure", router.retrievers)
        self.assertIsNotNone(deps.failure_preflight)

    def test_the_task_state_source_registers_from_the_toolbox(self):
        from runtime.plan import PlanStore

        class _Box:
            owner = "alice"
            chat_id = "chat"
            memory = None
            workspace = Path(tempfile.mkdtemp(prefix="fab_"))

            def _plan_store(self):
                return PlanStore(self.workspace)

        router = build(_Box())
        self.assertIn("task_state", router.retrievers)


class TermTests(unittest.TestCase):
    def test_chinese_queries_produce_terms_without_spaces(self):
        terms = terms_of("检查目录")
        self.assertIn("检查", terms)
        self.assertIn("目录", terms)


if __name__ == "__main__":
    unittest.main()
