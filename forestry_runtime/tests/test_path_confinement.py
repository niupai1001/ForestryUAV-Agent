"""Confinement tests for the shared path invariant and its call sites.

The invariant is defined once in ``runtime.exec.paths``; these tests pin its
behaviour for Windows and POSIX path flavours, then assert that the real call
sites reject escapes. That combination is what lets the ``gate.permissions``
verifier cover every path decision from a single definition.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
import unittest

from runtime.exec.paths import is_within
from runtime.storage import AssetError, Store
from runtime.workspace import WorkspacePathError, WorkspaceRegistry


class IsWithinTests(unittest.TestCase):
    def test_root_itself_and_real_children_are_inside(self):
        root = PurePosixPath("/data/session")
        for child in (
            PurePosixPath("/data/session"),
            PurePosixPath("/data/session/workspace"),
            PurePosixPath("/data/session/a/b/c"),
        ):
            with self.subTest(child=str(child)):
                self.assertTrue(is_within(child, root))

    def test_siblings_and_ancestors_are_outside(self):
        root = PurePosixPath("/data/session")
        for child in (
            PurePosixPath("/data/other"),
            PurePosixPath("/data/session-2"),
            PurePosixPath("/data"),
            PurePosixPath("/"),
        ):
            with self.subTest(child=str(child)):
                self.assertFalse(is_within(child, root))

    def test_unresolved_parent_segments_are_outside(self):
        # A lexical comparison must not accept a path that still escapes upward.
        root = PurePosixPath("/data/session")
        self.assertFalse(is_within(PurePosixPath("/data/session/../secrets"), root))
        self.assertFalse(is_within(Path("/data/session") / ".." / "secrets", root))

    def test_posix_relative_and_absolute_mix_is_outside(self):
        self.assertFalse(is_within(PurePosixPath("relative/file"), PurePosixPath("/data")))

    def test_windows_flavour_is_case_insensitive_and_separator_aware(self):
        root = PureWindowsPath(r"E:\Data\Session")
        for child in (
            PureWindowsPath(r"E:\Data\Session"),
            PureWindowsPath(r"e:\data\session\workspace"),
            PureWindowsPath(r"E:\Data\Session\a\b"),
        ):
            with self.subTest(child=str(child)):
                self.assertTrue(is_within(child, root))
        for child in (
            PureWindowsPath(r"E:\Data\Other"),
            PureWindowsPath(r"E:\Data\Session2"),
            PureWindowsPath(r"E:\Data"),
            PureWindowsPath(r"F:\Data\Session"),
        ):
            with self.subTest(child=str(child)):
                self.assertFalse(is_within(child, root))

    def test_windows_unresolved_parent_segments_are_outside(self):
        root = PureWindowsPath(r"E:\Data\Session")
        self.assertFalse(is_within(PureWindowsPath(r"E:\Data\Session\..\Secrets"), root))

    def test_non_path_arguments_are_rejected(self):
        for value in ("/data/session", 1, None):
            with self.subTest(value=repr(value)):
                with self.assertRaises(TypeError):
                    is_within(value, PurePosixPath("/data"))


class CallSiteConfinementTests(unittest.TestCase):
    """Every call site must still refuse to leave its root."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_workspace_registry_rejects_escape_and_allows_children(self):
        registry = WorkspaceRegistry(self.root / "data")
        from runtime.lifecycle import Sessions

        sessions = Sessions(self.root / "data")
        chat_id = "00000000-0000-0000-0000-0000000000aa"
        sessions.create("owner", chat_id)
        store = sessions.acquire("owner", chat_id)

        inside = registry.workspace_path(store, "notes/today.txt")
        self.assertTrue(
            is_within(inside, registry.workspace(store)),
            "a normal child path must be accepted",
        )
        for escape in ("../escape.txt", "a/../../escape.txt", "/etc/passwd"):
            with self.subTest(escape=escape):
                with self.assertRaises((AssetError, WorkspacePathError)):
                    registry.workspace_path(store, escape)

    def test_store_rejects_registered_path_outside_its_root(self):
        store = Store(self.root / "session")
        outside = self.root / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        with self.assertRaises(AssetError):
            store.register_path(outside, "outside.txt", "owner")
        inside = self.root / "session" / "inside.txt"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("fine", encoding="utf-8")
        asset = store.register_path(inside, "inside.txt", "owner")
        self.assertTrue(asset["id"].startswith("asset_"))
        self.assertTrue(is_within(store.path(asset["id"], "owner"), self.root / "session"))

    def test_host_bridge_safe_relative_rejects_escape(self):
        from host_bridge.server import safe_relative

        authorized = self.root / "authorized"
        (authorized / "sub").mkdir(parents=True)
        (authorized / "sub" / "file.txt").write_text("x", encoding="utf-8")
        self.assertTrue(is_within(safe_relative(authorized, "sub/file.txt", exists=True), authorized))
        self.assertTrue(is_within(safe_relative(authorized, "."), authorized))
        for escape in ("../outside.txt", "sub/../../outside.txt", "/etc/passwd"):
            with self.subTest(escape=escape):
                with self.assertRaises(ValueError):
                    safe_relative(authorized, escape)


if __name__ == "__main__":
    unittest.main()
