"""The event log is written once per streamed chunk, so opening it must be cheap.

A Run appends one event for every thinking chunk the model emits. When each of
those appends opened its own SQLite connection, the cost of *connecting* -- not
of writing -- dominated the Run: on this deployment the data directory is a host
bind mount, one connect measured ~24 ms there and one insert on an open
connection measured under 1 ms. A 19k-event Run therefore spent minutes opening
the database. These tests hold that property in place.
"""

import sqlite3
import threading
import tempfile
import unittest

from runtime.run_store import RunStore


class ConnectionReuseTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.store = RunStore(self._temporary.name)

    def test_the_same_thread_reuses_one_connection(self):
        with self.store.db() as first:
            with self.store.db() as second:
                self.assertIs(first, second)

    def test_a_nested_call_does_not_commit_the_outer_write(self):
        # create() reads through get() while it is still inserting. If the inner
        # call committed, one Run would be split into two partial writes, and an
        # outer failure would leave the first half behind.
        try:
            with self.store.db() as outer:
                outer.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY)")
                with self.store.db() as inner:
                    self.assertIs(inner, outer)
                    inner.execute("INSERT INTO probe(id) VALUES(1)")
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        with self.store.db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM probe").fetchone()[0], 0
            )

    def test_close_releases_the_connection_and_the_next_call_reopens(self):
        with self.store.db() as first:
            pass
        self.store.close()
        with self.store.db() as second:
            self.assertIsNot(first, second)
            second.execute("SELECT 1")

    def test_each_thread_gets_its_own_connection(self):
        seen = []
        barrier = threading.Barrier(2)

        def worker():
            with self.store.db() as conn:
                seen.append(id(conn))
                barrier.wait(timeout=10)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(set(seen)), 2)

    def test_a_written_event_is_visible_to_another_connection(self):
        # Keeping a connection open buys throughput; it must not cost a reader --
        # the collector polls from its own connection while the Run is appending.
        self.store.append(
            "run_probe", {"type": "message", "content": "hello"}, "turn_probe"
        )
        other = sqlite3.connect(self.store.database)
        try:
            rows = other.execute(
                "SELECT event_json FROM events WHERE run_id=?", ("run_probe",)
            ).fetchall()
        finally:
            other.close()
        self.assertTrue(
            any("hello" in str(row[0]) for row in rows),
            "an event written on a reused connection must be readable elsewhere",
        )


if __name__ == "__main__":
    unittest.main()
