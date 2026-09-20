import re
import io
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from runtime.lifecycle import Sessions
from runtime.session.coordinator import RunCoordinator
from runtime.workspace import WorkspaceRegistry


class RunApiTests(unittest.TestCase):
    def test_file_download_requires_identity_and_uses_real_owner(self):
        from runtime.api import app as api

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {
                "RUNTIME_API_KEY": "k" * 32,
                "UI_SESSION_KEY": "u" * 32,
            }, clear=False,
        ):
            sessions = Sessions(temporary)
            workspaces = WorkspaceRegistry(temporary)
            runs = RunCoordinator(sessions, workspaces)
            sessions.cleanup = workspaces.cleanup_session
            chat_id = str(uuid.uuid4())
            sessions.create("alice", chat_id)
            store = sessions.acquire("alice", chat_id)
            asset = store.put(io.BytesIO(b"private"), "private.txt", "alice")
            sessions.release(chat_id)
            with patch.object(api, "sessions", sessions), patch.object(
                api, "workspaces", workspaces
            ), patch.object(api, "runs", runs), TestClient(api.app) as client:
                path = f"/files/{chat_id}/{asset['id']}"
                self.assertEqual(client.get(path).status_code, 401)
                missing_owner = client.get(
                    path, headers={"Authorization": "Bearer " + "k" * 32}
                )
                self.assertEqual(missing_owner.status_code, 400)
                allowed = client.get(path, headers={
                    "Authorization": "Bearer " + "k" * 32,
                    "X-User-ID": "alice",
                })
                self.assertEqual(allowed.status_code, 200)
                self.assertEqual(allowed.content, b"private")

    def test_react_ui_assets_do_not_collide_with_asset_api(self):
        from runtime.api import app as api

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"RUNTIME_API_KEY": "k" * 32}, clear=False
        ):
            sessions = Sessions(temporary)
            workspaces = WorkspaceRegistry(temporary)
            runs = RunCoordinator(sessions, workspaces)
            sessions.cleanup = workspaces.cleanup_session
            with patch.object(api, "sessions", sessions), patch.object(
                api, "workspaces", workspaces
            ), patch.object(api, "runs", runs), TestClient(api.app) as client:
                root = client.get("/", follow_redirects=False)
                self.assertEqual(root.status_code, 307)
                self.assertEqual(root.headers["location"], "/ui/")
                self.assertIn("forestry_runtime_ui", root.headers["set-cookie"])

                page = client.get("/ui/")
                self.assertEqual(page.status_code, 200)
                asset = re.search(r'/ui/assets/[^"\']+\.js', page.text)
                self.assertIsNotNone(asset)
                self.assertEqual(client.get(asset.group(0)).status_code, 200)

    def test_create_poll_and_resume_events(self):
        async def fake_agent(*args, **kwargs):
            yield {"type": "model_call", "number": 1, "tool_count": 13, "tool_schema_chars": 100}
            yield {"type": "message", "content": "observed"}
            yield {"type": "done", "artifacts": []}

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"RUNTIME_API_KEY": "k" * 32, "DATA_ROOT": temporary}, clear=False
        ):
            from runtime.api import app as api

            sessions = Sessions(temporary)
            workspaces = WorkspaceRegistry(temporary)
            runs = RunCoordinator(sessions, workspaces)
            sessions.cleanup = workspaces.cleanup_session
            chat_id = str(uuid.uuid4())
            headers = {
                "Authorization": "Bearer " + "k" * 32,
                "X-User-ID": "alice",
                "X-Chat-ID": chat_id,
            }
            with patch.object(api, "sessions", sessions), patch.object(api, "workspaces", workspaces), patch.object(api, "runs", runs), patch(
                "runtime.session.coordinator.stream_agent", fake_agent
            ), TestClient(api.app) as client:
                self.assertEqual(client.post("/sessions", headers=headers, json={"chat_id": chat_id}).status_code, 200)
                response = client.post("/runs", headers=headers, json={
                    "messages": [{
                        "role": "user", "content": "inspect",
                        "input_id": "input_first_visible_message",
                    }],
                    "asset_ids": [], "use_tools": True,
                })
                self.assertEqual(response.status_code, 200, response.text)
                run_id = response.json()["id"]
                for _ in range(50):
                    state = client.get(f"/runs/{run_id}", headers=headers).json()["state"]
                    if state == "completed":
                        break
                    time.sleep(0.01)
                self.assertEqual(state, "completed")
                events = client.get(f"/runs/{run_id}/events?after=0", headers=headers).json()
                self.assertEqual([item["type"] for item in events["events"]], [
                    "user_message", "run_state", "model_call", "message", "done", "run_state",
                ])
                self.assertEqual(
                    events["events"][0]["input_id"],
                    "input_first_visible_message",
                )
                cursor = events["next"]
                empty = client.get(f"/runs/{run_id}/events?after={cursor}", headers=headers).json()
                self.assertEqual(empty["events"], [])

                continued = client.post(
                    f"/runs/{run_id}/messages", headers=headers, json={
                        "content": "continue with new evidence",
                        "input_id": "input_second_visible_message",
                    }
                )
                self.assertEqual(continued.status_code, 200, continued.text)
                for _ in range(50):
                    continued_events = client.get(
                        f"/runs/{run_id}/events?after={cursor}", headers=headers
                    ).json()["events"]
                    if any(
                        item.get("input_id") == "input_second_visible_message"
                        for item in continued_events
                    ):
                        break
                    time.sleep(0.01)
                continued_user = next(
                    item for item in continued_events
                    if item.get("input_id") == "input_second_visible_message"
                )
                self.assertEqual(continued_user["type"], "user_message")


if __name__ == "__main__":
    unittest.main()
