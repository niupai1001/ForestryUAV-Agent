import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.memory import MemoryManager
from runtime.storage import AssetError
from pypdf import PdfWriter


class MemoryManagerTests(unittest.TestCase):
    def test_memory_requires_confirmation_and_project_selection_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = MemoryManager(temporary)
            project = manager.create_project("alice", "白桦项目")
            self.assertEqual(manager.context("alice", "chat"), {})
            with self.assertRaises(AssetError):
                manager.add_memory("alice", project["id"], "源目录只读", False)
            saved = manager.add_memory(
                "alice", project["id"], "源目录只读", True
            )
            manager.select_project("alice", "chat", project["id"])
            context = manager.context("alice", "chat")
            self.assertEqual(context["project_id"], project["id"])
            self.assertEqual(context["confirmed_memories"][0]["content"], "源目录只读")
            self.assertEqual(context["knowledge_sources"], [])
            updated = manager.update_memory(
                "alice", project["id"], saved["id"],
                "源目录只读，修改写入 Workspace", True,
            )
            self.assertEqual(updated["version"], 2)
            self.assertEqual(
                [item["version"] for item in manager.memory_history(
                    "alice", project["id"], saved["id"]
                )],
                [2, 1],
            )
            manager.delete_memory("alice", project["id"], saved["id"])
            history = manager.memory_history(
                "alice", project["id"], saved["id"]
            )
            self.assertEqual(history[0]["state"], "deleted")
            self.assertEqual(manager.memories("alice", project["id"]), [])

    def test_local_knowledge_is_limited_to_configured_roots_and_cited(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root / "knowledge"
            allowed.mkdir()
            document = allowed / "field.txt"
            document.write_text("1605白桦 已经完成正射影像拼接。", encoding="utf-8")
            with patch.dict(os.environ, {"KNOWLEDGE_ROOTS": str(allowed)}, clear=False):
                manager = MemoryManager(root / "state")
                project = manager.create_project("alice", "验收")
                source = manager.index_source("alice", project["id"], "local", str(allowed))
                self.assertIn(source["state"], {"ready", "keyword_only"})
                result = manager.search("alice", project["id"], "白桦 正射", 3)
                self.assertTrue(result["results"])
                citation = result["results"][0]["citation"]
                self.assertRegex(
                    citation,
                    r"^knowledge://source_[0-9a-f]{32}/chunk_[0-9a-f]{32}#segment=\d+$",
                )
                self.assertEqual(str(document), result["results"][0]["document"])
                manager.select_project("alice", "chat", project["id"])
                with patch.object(
                    manager, "search",
                    side_effect=AssertionError("request context must not retrieve"),
                ):
                    context = manager.context("alice", "chat")
                self.assertEqual(context["knowledge_sources"][0]["id"], source["id"])
                self.assertNotIn("retrieval", context)
                with self.assertRaises(AssetError):
                    manager.index_source("alice", project["id"], "local", str(root))

    def test_queued_index_is_visible_and_restart_marks_interruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root / "knowledge"
            allowed.mkdir()
            document = allowed / "facts.md"
            document.write_text("白桦项目知识", encoding="utf-8")
            state = root / "state"
            with patch.dict(os.environ, {"KNOWLEDGE_ROOTS": str(allowed)}, clear=False):
                manager = MemoryManager(state)
                project = manager.create_project("alice", "异步索引")
                queued = manager.queue_source(
                    "alice", project["id"], "local", str(document)
                )
                self.assertEqual(queued["state"], "indexing")
                restarted = MemoryManager(state)
                source = restarted.sources("alice", project["id"])[0]
                self.assertEqual(source["state"], "failed")
                self.assertIn("restart", source["error"])

    def test_vector_index_is_persisted_and_unchanged_source_is_not_rebuilt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "knowledge.md"
            document.write_text("白桦正射成果已经完成。", encoding="utf-8")
            with patch.dict(os.environ, {"KNOWLEDGE_ROOTS": str(root)}, clear=False):
                manager = MemoryManager(root / "state")
                if not manager._vec_available:
                    self.skipTest("sqlite-vec is unavailable")
                project = manager.create_project("alice", "向量索引")
                with patch.object(
                    manager, "_embed",
                    side_effect=lambda texts: [[0.0] * manager.embedding_dim for _ in texts],
                ):
                    first = manager.index_source(
                        "alice", project["id"], "local", str(document)
                    )
                    second = manager.index_source(
                        "alice", project["id"], "local", str(document)
                    )
                self.assertEqual(first["state"], "ready")
                self.assertTrue(second["unchanged"])
                with manager.db() as conn:
                    manager._load_vec(conn)
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM knowledge_vec").fetchone()[0],
                        first["chunk_count"],
                    )

    def test_html_source_keeps_title_and_chunk_can_be_read_with_neighbors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "method.html"
            document.write_text(
                "<html><head><title>林分结构方法</title><style>忽略</style></head>"
                "<body><h1>冠层高度</h1><p>CHM 由 DSM 与 DTM 对齐后相减。</p>"
                "<script>不要索引</script></body></html>",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"KNOWLEDGE_ROOTS": str(root)}, clear=False):
                manager = MemoryManager(root / "state")
                project = manager.create_project("alice", "林分结构")
                manager.index_source("alice", project["id"], "local", str(document))
                result = manager.search("alice", project["id"], "冠层高度", 3)
                self.assertEqual(result["results"][0]["title"], "林分结构方法")
                self.assertNotIn("不要索引", result["results"][0]["content"])
                chunk_id = result["results"][0]["id"]
                read = manager.read_chunks(
                    "alice", project["id"], chunk_id, before=1, after=1
                )
                self.assertEqual(read["chunks"][0]["id"], chunk_id)
                self.assertTrue(read["chunks"][0]["source_version"])

    def test_scanned_pdf_reports_that_ocr_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with document.open("wb") as stream:
                writer.write(stream)
            with patch.dict(os.environ, {"KNOWLEDGE_ROOTS": str(root)}, clear=False):
                manager = MemoryManager(root / "state")
                project = manager.create_project("alice", "扫描资料")
                with self.assertRaisesRegex(AssetError, "requires OCR"):
                    manager.index_source(
                        "alice", project["id"], "local", str(document)
                    )

    def test_neighbor_read_does_not_cross_pdf_page_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = root / "method.pdf"
            document.write_bytes(b"placeholder")
            with patch.dict(os.environ, {"KNOWLEDGE_ROOTS": str(root)}, clear=False):
                manager = MemoryManager(root / "state")
                project = manager.create_project("alice", "分页资料")
                pages = [
                    {
                        "document": str(document), "title": "林分结构", "page": 1,
                        "text": "第一页冠层高度。" * 300,
                    },
                    {
                        "document": str(document), "title": "林分结构", "page": 2,
                        "text": "第二页林分密度。" * 300,
                    },
                ]
                with patch.object(manager, "_documents", return_value=pages), patch.object(
                    manager, "_embed",
                    side_effect=lambda texts: [
                        [0.0] * manager.embedding_dim for _ in texts
                    ],
                ):
                    manager.index_source(
                        "alice", project["id"], "local", str(document)
                    )
                with manager.db() as conn:
                    target = conn.execute(
                        """SELECT id FROM knowledge_chunks
                        WHERE project_id=? AND page=1 AND ordinal=1""",
                        (project["id"],),
                    ).fetchone()["id"]
                read = manager.read_chunks(
                    "alice", project["id"], target, before=0, after=10
                )
                self.assertEqual({chunk["page"] for chunk in read["chunks"]}, {1})
                self.assertGreater(len(read["chunks"]), 1)


class MemoryRecallTests(unittest.TestCase):
    """Memory is recalled per query, not injected wholesale.

    The old behaviour put every confirmed memory into every request. A project that
    had once noted "源目录只读" carried that sentence into every later question, while
    the one memory that actually bore on the question sat somewhere in a list of
    twenty. These tests pin the difference: relevant in, irrelevant out.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = MemoryManager(self.temporary.name)
        self.project = self.manager.create_project("alice", "白桦项目")
        self.manager.add_memory("alice", self.project["id"], "源目录只读", True)
        self.manager.add_memory("alice", self.project["id"], "NDVI 阈值取 0.4", True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_only_memories_bearing_on_the_query_are_recalled(self):
        found = self.manager.recall_memories("alice", self.project["id"], "阈值")
        self.assertEqual(len(found), 1)
        self.assertIn("NDVI", found[0]["content"])

    def test_an_unrelated_query_recalls_nothing(self):
        self.assertEqual(
            self.manager.recall_memories("alice", self.project["id"], "投影坐标系"),
            [],
        )

    def test_recall_is_ranked_and_bounded(self):
        for index in range(10):
            self.manager.add_memory(
                "alice", self.project["id"], f"阈值相关记忆 {index}", True
            )
        found = self.manager.recall_memories("alice", self.project["id"], "阈值", limit=3)
        self.assertEqual(len(found), 3)

    def test_an_empty_query_is_not_a_license_to_return_everything(self):
        self.assertEqual(self.manager.recall_memories("alice", self.project["id"], ""), [])

    def test_the_full_list_is_still_available_for_management(self):
        self.assertEqual(len(self.manager.memories("alice", self.project["id"])), 2)


if __name__ == "__main__":
    unittest.main()
