import io
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4

from runtime.lifecycle import Sessions, SessionClosed
from runtime.storage import AssetError
from runtime.tools import Toolbox


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Sessions(self.temp.name)
        self.chat = str(uuid4())
        self.manager.create('alice', self.chat)

    def tearDown(self):
        self.temp.cleanup()

    def test_active_tool_cleanup_and_other_chat_survives(self):
        other = str(uuid4())
        self.manager.create('alice', other)
        store = self.manager.acquire('alice', self.chat)
        original = store.put(io.BytesIO(b'original'), 'a.txt', 'alice')
        second = self.manager.acquire('alice', other)
        other_asset = second.put(io.BytesIO(b'other'), 'b.txt', 'alice')
        self.manager.release(other)
        ready, finish = threading.Event(), threading.Event()
        errors = []

        def executing_tool():
            try:
                with store.operation():
                    ready.set()
                    if not finish.wait(5):
                        raise RuntimeError('test timeout')
                    store.put(io.BytesIO(b'result'), 'result.txt', 'alice', parent_id=original['id'])
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=executing_tool)
        thread.start()
        self.assertTrue(ready.wait(5))
        self.manager.release(self.chat)  # Simulate HTTP disconnect while tool continues.
        self.manager.mark_deleted('alice', self.chat)
        self.manager.reap()
        self.assertTrue(store.root.exists())
        with self.assertRaises(SessionClosed):
            self.manager.acquire('alice', self.chat)
        finish.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.manager.reap()
        self.assertFalse(store.root.exists())
        self.assertEqual(second.path(other_asset['id'], 'alice').read_bytes(), b'other')

    def test_restart_retries_and_late_sources_not_lost(self):
        store = self.manager.acquire('alice', self.chat)
        store.put(io.BytesIO(b'x'), 'a.txt', 'alice')
        self.manager.release(self.chat)
        first, late = str(uuid4()), str(uuid4())
        self.manager.source('alice', self.chat, first)
        self.manager.mark_deleted('alice', self.chat)
        recovered = Sessions(self.temp.name)
        recovered.reap()
        recovered.source('alice', self.chat, late)
        recovered.acknowledge('alice', self.chat, [first])
        self.assertEqual(recovered.pending()[0]['file_ids'], [late])
        recovered.acknowledge('alice', self.chat, [late])
        self.assertEqual(recovered.pending(), [])
        with self.assertRaises(SessionClosed):
            recovered.create('alice', self.chat)

    def test_owner_path_and_legacy_protection(self):
        legacy = Path(self.temp.name) / 'asset_legacy'
        legacy.mkdir()
        (legacy / 'content').write_bytes(b'keep')
        with self.assertRaises(AssetError):
            self.manager.mark_deleted('bob', self.chat)
        with self.assertRaises(AssetError):
            self.manager.create('alice', '../outside')
        self.manager.mark_deleted('alice', self.chat)
        self.manager.reap()
        self.assertTrue((legacy / 'content').exists())


if __name__ == '__main__':
    unittest.main()
