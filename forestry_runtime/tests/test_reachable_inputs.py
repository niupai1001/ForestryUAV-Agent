"""What a chat can reach must be stated, not inferred from failed lookups.

Three defects shared one shape. A file that was never attached, a path that is
reachable but not authorized, and a directory that is simply absent all came back
as the same observation to a discovery call: nothing matched. From "nothing
matched" a model cannot tell

* whether the input exists somewhere it has not looked,
* whether it should look somewhere else, or
* whether the user still has to supply it,

so it retries other directories. Observed in a real Run: seven tool calls across
artifact inspection, directory listing, text search and text read, all circling one
missing input, until the user cancelled.

`reachable_inputs` states the reachable set up front and gives every entry one
reference a tool accepts unchanged. It reports facts only; the model still decides
what to do about them.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.context import reachable_inputs


def _attachment(asset_id: str, name: str) -> dict:
    return {"id": asset_id, "name": name, "media_type": "image/tiff"}


def _grant(grant_id: str, host_path: str) -> dict:
    return {"id": grant_id, "host_path": host_path, "access": "read"}


class ReachableInputsTests(unittest.TestCase):
    def test_empty_chat_says_nothing_is_reachable(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            (workspace / ".runtime" / "deps").mkdir(parents=True)
            facts = reachable_inputs(attachments=[], grants=[], workspace=workspace)
            self.assertTrue(facts["workspace_empty"])
            self.assertEqual(facts["asset_count"], 0)
            self.assertEqual(facts["source_grant_count"], 0)
            self.assertIn("Nothing is reachable", facts["note"])
            # The empty workspace is stated as empty, not left to be discovered.
            self.assertEqual(facts["roots"][0]["contents"], [])
            self.assertTrue(facts["roots"][0]["empty"])

    def test_runtime_directory_does_not_make_the_workspace_look_occupied(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            (workspace / ".runtime" / "actions").mkdir(parents=True)
            (workspace / ".runtime" / "deps").mkdir(parents=True)
            facts = reachable_inputs(attachments=[], grants=[], workspace=workspace)
            self.assertTrue(facts["workspace_empty"])

    def test_a_real_file_makes_the_workspace_non_empty_and_is_listed(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "canopy.tif").write_bytes(b"x")
            facts = reachable_inputs(attachments=[], grants=[], workspace=workspace)
            self.assertFalse(facts["workspace_empty"])
            self.assertEqual(facts["roots"][0]["contents"], ["canopy.tif"])

    def test_attached_file_is_addressed_by_asset_id_not_by_path(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir(parents=True)
            facts = reachable_inputs(
                attachments=[_attachment("asset_abc", "oam-01.tif")],
                grants=[], workspace=workspace,
            )
            asset = [root for root in facts["roots"] if root["kind"] == "asset"][0]
            self.assertEqual(asset["exact_reference"]["asset_id"], "asset_abc")
            self.assertNotIn("path", asset["exact_reference"])
            self.assertIn("asset_id", facts["note"])

    def test_the_uploaded_file_the_message_names_is_reachable_by_reference(self):
        """The regression: the message quoted a path, the file arrived as an asset."""
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir(parents=True)
            facts = reachable_inputs(
                attachments=[_attachment("asset_e879", "oam-01.tif")],
                grants=[], workspace=workspace,
            )
            # The quoted relative path names nothing; the asset is what exists.
            self.assertTrue(facts["workspace_empty"])
            self.assertEqual(facts["asset_count"], 1)
            self.assertFalse(
                facts["note"].startswith("Nothing is reachable"),
                "an attached asset means the chat is not empty",
            )

    def test_source_grant_reference_uses_source_id_and_stays_read_only(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir(parents=True)
            facts = reachable_inputs(
                attachments=[], grants=[_grant("grant_1", r"E:\imagery")],
                workspace=workspace,
            )
            source = [root for root in facts["roots"] if root["kind"] == "source"][0]
            self.assertEqual(source["exact_reference"]["source_id"], "grant_1")
            self.assertEqual(source["access"], "read")
            self.assertIn("source_id", facts["note"])

    def test_workspace_reference_matches_what_the_fs_tools_accept(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir(parents=True)
            facts = reachable_inputs(attachments=[], grants=[], workspace=workspace)
            reference = facts["roots"][0]["exact_reference"]
            self.assertEqual(reference, {"scope": "workspace", "path": "."})

    def test_payload_is_json_serialisable_for_the_request(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir(parents=True)
            facts = reachable_inputs(
                attachments=[_attachment("asset_abc", "a.tif")],
                grants=[_grant("grant_1", r"E:\imagery")],
                workspace=workspace,
            )
            encoded = json.dumps(facts, ensure_ascii=False, allow_nan=False)
            self.assertIn("asset_abc", encoded)

    def test_no_note_tells_the_model_what_to_do(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir(parents=True)
            notes = [
                reachable_inputs(attachments=[], grants=[], workspace=workspace)["note"],
                reachable_inputs(attachments=[_attachment("a", "n.tif")], grants=[],
                                 workspace=workspace)["note"],
                reachable_inputs(attachments=[], grants=[_grant("g", "E:\\x")],
                                 workspace=workspace)["note"],
            ]
            for note in notes:
                lowered = note.casefold()
                for imperative in ("you must", "do not", "you should", "stop", "ask the user"):
                    self.assertNotIn(imperative, lowered, note)


class ReachableInputsReachTheModelTests(unittest.TestCase):
    """The claim is about what the model is told, so assert it where it is told."""

    def test_compiled_instruction_carries_the_reachable_set(self):
        import asyncio

        from pydantic_ai.models.function import FunctionModel

        from runtime.agent import stream_agent
        from runtime.storage import Store
        from runtime.workspace import WorkspaceRegistry

        seen: list[str] = []

        async def model(messages, info):
            # The compiled instruction is delivered as request instruction parts, not
            # as a message, so it has to be read from there.
            parameters = getattr(info, "model_request_parameters", None)
            for part in getattr(parameters, "instruction_parts", ()) or ():
                text = getattr(part, "content", None)
                if isinstance(text, str) and "Runtime facts for this model request" in text:
                    seen.append(text)
            for message in messages:
                for part in getattr(message, "parts", []) or []:
                    text = getattr(part, "content", None)
                    if isinstance(text, str) and "Runtime facts for this model request" in text:
                        seen.append(text)
            yield "ok"

        with tempfile.TemporaryDirectory() as raw:
            store = Store(raw)
            store.chat_id = "reachable-test"
            registry = WorkspaceRegistry(raw)

            async def run():
                return [event async for event in stream_agent(
                    store, "alice", [],
                    [{"role": "user", "content": "提取树冠"}],
                    model=FunctionModel(stream_function=model),
                    workspace_registry=registry,
                )]

            asyncio.run(run())

        self.assertTrue(seen, "the model never received a runtime-facts paragraph")
        facts = seen[0]
        self.assertIn("reachable_inputs", facts)
        self.assertIn("workspace_empty", facts)
        # With nothing attached, the model is told the chat is empty rather than
        # having to discover it through failed lookups.
        self.assertIn("Nothing is reachable", facts)


if __name__ == "__main__":
    unittest.main()
