"""Schema-initialization and additive-migration tests for the Run store.

The schema used to live inside ``RunStore.__init__``, so the only way to check it
was to drive a Run. These tests pin the properties that mattered there: a fresh
database gets the full layout, an older database is upgraded in place without
losing rows, and the store still opens a database created before a column existed.

Connections are closed explicitly: ``sqlite3``'s context manager commits but does
not close, and Windows refuses to remove a directory while a handle is open.
"""

from __future__ import annotations

from contextlib import closing
import shutil
import sqlite3
from pathlib import Path
import tempfile
import unittest

from runtime.run_store import RunStore
from runtime.store.schema import ADDED_COLUMNS, INDEXES, initialize


EXPECTED_TABLES = {
    "runs", "events", "actions", "job_refs", "turns", "inputs", "steps",
    "attempts", "artifact_refs",
}


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        try:
            self.temp.cleanup()
        except PermissionError:  # a lingering WAL handle on Windows
            shutil.rmtree(self.root, ignore_errors=True)

    def tables(self, database: Path) -> set[str]:
        with closing(sqlite3.connect(database)) as conn:
            return {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

    def columns(self, database: Path, table: str) -> set[str]:
        with closing(sqlite3.connect(database)) as conn:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def test_fresh_database_gets_every_declared_table(self):
        database = self.root / "fresh.sqlite3"
        with closing(sqlite3.connect(database)) as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            conn.commit()
        self.assertTrue(EXPECTED_TABLES <= self.tables(database))
        for table, column, _ in ADDED_COLUMNS:
            with self.subTest(table=table, column=column):
                self.assertIn(column, self.columns(database, table))

    def test_initialize_is_idempotent(self):
        database = self.root / "twice.sqlite3"
        for _ in range(3):
            with closing(sqlite3.connect(database)) as conn:
                conn.row_factory = sqlite3.Row
                initialize(conn)
                conn.commit()
        self.assertTrue(EXPECTED_TABLES <= self.tables(database))

    def test_legacy_database_is_upgraded_in_place_without_losing_rows(self):
        """A pre-migration database keeps its data and gains the newer columns."""
        database = self.root / "legacy" / "runs.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
        # The layout as it stood before state_version / recovery_reason /
        # pause_requested / agent_input_count were introduced.
        with closing(sqlite3.connect(database)) as conn:
            conn.execute(
                """CREATE TABLE runs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT NOT NULL,
                state TEXT NOT NULL, messages_json TEXT NOT NULL,
                asset_ids_json TEXT NOT NULL, use_tools INTEGER NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                model_calls INTEGER NOT NULL DEFAULT 0, final_json TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL)"""
            )
            conn.execute(
                """INSERT INTO runs(id,owner,chat_id,state,messages_json,
                asset_ids_json,use_tools,cancel_requested,model_calls,created_at,
                updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("run_legacy", "owner", "chat", "completed", "[]", "[]", 1, 0, 3,
                 1.0, 1.0),
            )
            conn.commit()
        legacy_columns = self.columns(database, "runs")
        self.assertNotIn("state_version", legacy_columns)
        self.assertNotIn("recovery_reason", legacy_columns)

        store = RunStore(self.root / "legacy")
        upgraded = self.columns(database, "runs")
        for column in ("state_version", "recovery_reason", "pause_requested",
                       "agent_input_count"):
            with self.subTest(column=column):
                self.assertIn(column, upgraded)
        restored = store.get("run_legacy")
        self.assertEqual(restored["state"], "completed")
        self.assertEqual(restored["model_calls"], 3)
        self.assertEqual(restored["state_version"], 0)

    def test_store_initialization_creates_all_indexes(self):
        store = RunStore(self.root)
        with closing(sqlite3.connect(store.database)) as conn:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        for statement in INDEXES:
            expected = statement.split(" IF NOT EXISTS ")[1].split(" ON ")[0]
            with self.subTest(index=expected):
                self.assertIn(expected, names)

    def test_store_enables_write_ahead_logging_and_normal_sync(self):
        store = RunStore(self.root)
        with closing(sqlite3.connect(store.database)) as conn:
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0].casefold(), "wal"
            )

    def test_run_store_uses_the_shared_schema_initializer(self):
        """The store must not carry its own DDL again."""
        source = (
            Path(__file__).resolve().parents[1] / "runtime" / "run_store.py"
        ).read_text(encoding="utf-8")
        self.assertIn("initialize_schema(conn)", source)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS", source)


if __name__ == "__main__":
    unittest.main()
