"""Phase 3 acceptance: artifacts are delivered, and the checks stay separate.

The failure this replaces: the preview tool registered a PNG and returned only asset
metadata; the conversation pane rendered model text and no artifact; the model wrote a
made-up ``https://workspace/...`` address; and the real route answered with
``application/octet-stream`` and an attachment disposition. The image existed the whole
time and the user could not see it.

What is checked here:

* the Runtime builds the preview and download addresses from the chat and asset ids,
  so the model never has to invent one;
* four questions are answered separately -- a process ran, a file exists, the server
  can read it, a browser can display it -- and "does it answer the task" is reported as
  unverified rather than assumed;
* a GeoTIFF product reports its own bands, grid, valid area and statistics;
* the model can be handed a bounded picture of the same sampled window whose numbers
  it was given.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import numpy as np
from PIL import Image
from rasterio.transform import from_origin
import rasterio

from runtime.agent import IMAGE_ATTACHMENT_LIMIT_BYTES, _bounded_tool_result, _image_attachment
from runtime.capabilities.artifacts.delivery import (
    asset_urls,
    describe_artifact,
    product_qa,
)
from runtime.capabilities.domain_runtime import RemoteSensingTools
from runtime.capabilities.runtime import RuntimeTools
from runtime.lifecycle import Sessions
from runtime.storage import AssetError
from runtime.workspace import WorkspaceRegistry


def _write_tiff(path: Path, values=None, *, count=1, dtype="uint8") -> None:
    array = np.asarray(
        values if values is not None else [[0, 1], [2, 3]], dtype=dtype,
    )
    height, width = array.shape[-2:]
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=count,
        dtype=dtype, crs="EPSG:32650",
        transform=from_origin(500000, 3100000, 0.1, 0.1),
    ) as dataset:
        if count == 1:
            dataset.write(array, 1)
        else:
            for index in range(1, count + 1):
                dataset.write(array + index, index)


class DeliveryDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_urls_are_built_from_the_chat_and_asset_ids(self):
        urls = asset_urls("chat-1", "asset_abc", inline=True)
        self.assertEqual(urls["download_url"], "/files/chat-1/asset_abc?download=1")
        self.assertEqual(urls["inline_url"], "/files/chat-1/asset_abc?inline=1")
        self.assertIsNone(asset_urls("chat-1", "asset_abc", inline=False)["inline_url"])

    def test_an_image_is_displayable_and_a_table_is_not(self):
        png = self.root / "mask.png"
        Image.fromarray(np.zeros((4, 4), dtype="uint8")).save(png)
        image = describe_artifact(
            {"id": "asset_png", "name": "mask.png", "media_type": "image/png",
             "size": png.stat().st_size},
            "chat-1", path=png,
        )
        self.assertTrue(image["checks"]["file_exists"])
        self.assertTrue(image["checks"]["server_can_read"])
        self.assertTrue(image["checks"]["browser_can_display"])
        self.assertTrue(image["urls"]["inline_url"])

        table = self.root / "summary.csv"
        table.write_text("a,b\n1,2\n", encoding="utf-8")
        described = describe_artifact(
            {"id": "asset_csv", "name": "summary.csv", "media_type": "text/csv",
             "size": table.stat().st_size},
            "chat-1", path=table,
        )
        self.assertFalse(described["checks"]["browser_can_display"])
        self.assertIsNone(described["urls"]["inline_url"])

    def test_answers_task_is_unverified_rather_than_assumed(self):
        png = self.root / "canopy.png"
        Image.fromarray(np.zeros((4, 4), dtype="uint8")).save(png)
        described = describe_artifact(
            {"id": "asset_png", "name": "canopy.png", "media_type": "image/png"},
            "chat-1", path=png,
        )
        self.assertIsNone(described["checks"]["answers_task"])
        self.assertIn("semantic judgment", described["checks"]["answers_task_reason"])
        self.assertEqual(described["semantic_check"]["status"], "unverified")

    def test_a_missing_file_is_reported_as_missing_not_as_delivered(self):
        described = describe_artifact(
            {"id": "asset_gone", "name": "gone.tif", "media_type": "image/tiff"},
            "chat-1", path=self.root / "gone.tif",
        )
        self.assertFalse(described["checks"]["file_exists"])
        self.assertFalse(described["checks"]["server_can_read"])
        self.assertTrue(described["urls"]["download_url"])

    def test_a_geotiff_reports_bands_grid_valid_area_and_statistics(self):
        target = self.root / "product.tif"
        _write_tiff(target, [[0, 2], [4, 6]], count=1)
        qa = product_qa(target)
        self.assertEqual(qa["band_count"], 1)
        self.assertEqual(qa["width"], 2)
        self.assertEqual(qa["height"], 2)
        self.assertEqual(qa["crs"], "EPSG:32650")
        self.assertEqual(qa["pixel_size"], [0.1, 0.1])
        self.assertEqual(qa["bands"][0]["min"], 0.0)
        self.assertEqual(qa["bands"][0]["max"], 6.0)
        self.assertAlmostEqual(qa["bands"][0]["mean"], 3.0)
        self.assertEqual(qa["valid_fraction_sampled"], 1.0)

    def test_a_product_with_an_invalid_area_reports_it(self):
        target = self.root / "product.tif"
        values = np.array([[1, 2], [3, 4]], dtype="uint8")
        with rasterio.open(
            target, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint8",
            nodata=0, crs="EPSG:32650", transform=from_origin(500000, 3100000, 1, 1),
        ) as dataset:
            dataset.write(values, 1)
            dataset.write_mask(np.array([[255, 0], [255, 255]], dtype="uint8"))
        qa = product_qa(target)
        self.assertAlmostEqual(qa["valid_fraction_sampled"], 0.75)

    def test_a_non_raster_has_no_product_qa(self):
        png = self.root / "x.png"
        Image.fromarray(np.zeros((2, 2), dtype="uint8")).save(png)
        self.assertIsNone(product_qa(png))


class DeliveredToolResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = Sessions(self.root)
        self.chat_id = str(uuid.uuid4())
        self.sessions.create("alice", self.chat_id)
        self.store = self.sessions.acquire("alice", self.chat_id)
        self.sessions.release(self.chat_id)
        self.registry = WorkspaceRegistry(self.root)
        self.workspace = self.registry.workspace(self.store)
        self.box = RuntimeTools(self.store, "alice", [], self.registry, "")

    def tearDown(self):
        self.temp.cleanup()

    def domain(self, asset_ids=()):
        return RemoteSensingTools(
            self.store, "alice", list(asset_ids), self.registry, "",
        )

    def test_a_preview_carries_a_real_inline_url(self):
        _write_tiff(self.workspace / "input.tif")
        # Through the dispatch boundary the harness actually uses, because that is
        # where a deliverable gets its addresses.
        result = self.box._execute_domain("preview_image", {"path": "input.tif"})
        self.assertTrue(result["ok"], result)
        delivery = result["data"]["preview"]["delivery"]
        self.assertEqual(delivery["checks"]["browser_can_display"], True)
        self.assertEqual(
            delivery["urls"]["inline_url"],
            f"/files/{self.chat_id}/{delivery['asset_id']}?inline=1",
        )
        self.assertEqual(delivery["preview_method"], "band-stretch")

    def test_every_created_artifact_is_described_for_the_done_event(self):
        _write_tiff(self.workspace / "input.tif")
        self.box.execute("artifacts_preview", {"path": "input.tif"})
        self.assertTrue(self.box.created)
        for asset in self.box.created:
            self.assertIn("delivery", asset)
            self.assertEqual(
                asset["delivery"]["urls"]["download_url"],
                f"/files/{self.chat_id}/{asset['id']}?download=1",
            )

    def test_the_thumbnail_tool_returns_numbers_and_a_picture_of_the_same_window(self):
        _write_tiff(
            self.workspace / "region.tif",
            np.arange(16, dtype="uint8").reshape(4, 4),
        )
        result = self.box._execute_domain("inspect_raster_region", {
            "path": "region.tif", "band": 1, "window": [0, 0, 2, 2], "max_size": 64,
        })
        self.assertTrue(result["ok"], result)
        data = result["data"]
        stats = data["statistics"]
        self.assertEqual(stats["window"], [0, 0, 2, 2])
        self.assertEqual(stats["valid_pixels"], 4)
        self.assertEqual(stats["min"], 0.0)
        self.assertEqual(stats["max"], 5.0)
        self.assertEqual(stats["sampled_shape"], {"width": 2, "height": 2})
        thumbnail = data["thumbnail"]
        self.assertEqual(thumbnail["media_type"], "image/png")
        self.assertEqual(data["attach_image"]["asset_id"], thumbnail["id"])
        delivery = thumbnail["delivery"]
        self.assertEqual(delivery["checks"]["browser_can_display"], True)
        with Image.open(self.store.path(thumbnail["id"], "alice")) as image:
            self.assertEqual(image.size, (2, 2))

    def test_a_window_outside_the_raster_is_refused_with_its_bounds(self):
        _write_tiff(self.workspace / "region.tif")
        result = self.domain().execute("inspect_raster_region", {
            "path": "region.tif", "window": [1, 1, 8, 8],
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["code"], "raster_window_out_of_bounds")
        self.assertEqual(result["failure"]["raster_size"], {"width": 2, "height": 2})

    def test_a_missing_band_names_the_band_that_exists(self):
        _write_tiff(self.workspace / "region.tif")
        result = self.domain().execute("inspect_raster_region", {
            "path": "region.tif", "band": 4,
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["code"], "raster_band_missing")
        self.assertEqual(result["failure"]["band_count"], 1)


class ModelAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sessions = Sessions(self.root)
        self.chat_id = str(uuid.uuid4())
        self.sessions.create("alice", self.chat_id)
        self.store = self.sessions.acquire("alice", self.chat_id)
        self.sessions.release(self.chat_id)
        self.registry = WorkspaceRegistry(self.root)
        self.workspace = self.registry.workspace(self.store)
        self.box = RuntimeTools(self.store, "alice", [], self.registry, "")

    def tearDown(self):
        self.temp.cleanup()

    def _thumbnail_result(self):
        _write_tiff(self.workspace / "input.tif", np.arange(16, dtype="uint8").reshape(4, 4))
        return self.box._execute_domain("inspect_raster_region", {"path": "input.tif"})

    def test_no_image_is_attached_while_vision_is_off(self):
        result = self._thumbnail_result()
        with patch.dict(os.environ, {"MODEL_VISION_ENABLED": "false"}):
            self.assertIsNone(_image_attachment(result, self.box))

    def test_the_bounded_thumbnail_is_attached_when_vision_is_on(self):
        result = self._thumbnail_result()
        with patch.dict(os.environ, {"MODEL_VISION_ENABLED": "true"}):
            attachment = _image_attachment(result, self.box)
        self.assertIsNotNone(attachment)
        self.assertEqual(attachment.media_type, "image/png")
        self.assertEqual(attachment.data[:4], b"\x89PNG")

    def test_an_oversized_image_is_not_attached(self):
        result = self._thumbnail_result()
        oversized = {"data": {"attach_image": {"asset_id": "asset_x", "media_type": "image/png"}}}
        with patch.dict(os.environ, {"MODEL_VISION_ENABLED": "true"}), patch(
            "runtime.agent.IMAGE_ATTACHMENT_LIMIT_BYTES", 1,
        ):
            self.assertIsNone(_image_attachment(result, self.box))
        self.assertGreater(IMAGE_ATTACHMENT_LIMIT_BYTES, 1024)

    def test_bounding_a_large_result_keeps_the_attachment_request(self):
        payload = {
            "ok": True,
            "data": {
                "attach_image": {"asset_id": "asset_x", "media_type": "image/png"},
                "filler": "x" * 40000,
            },
        }
        bounded = _bounded_tool_result(payload)
        self.assertTrue(bounded["truncated"])
        self.assertEqual(bounded["attach_image"]["asset_id"], "asset_x")


class FileRouteTests(unittest.TestCase):
    def test_the_route_serves_inline_for_display_and_attachment_for_download(self):
        from fastapi.testclient import TestClient

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.dict(os.environ, {
            "DATA_ROOT": temp.name,
            "RUNTIME_API_KEY": "test-key-with-more-than-24-characters",
        }):
            from runtime.api import app as api
            from runtime.lifecycle import Sessions
            from runtime.workspace import WorkspaceRegistry
            from runtime.session.coordinator import RunCoordinator
            manager = Sessions(Path(temp.name) / "sessions-api")
            registry = WorkspaceRegistry(Path(temp.name) / "sessions-api")
            coordinator = RunCoordinator(manager, registry)
            manager.cleanup = registry.cleanup_session
            with patch.object(api, "sessions", manager), patch.object(
                api, "workspaces", registry
            ), patch.object(api, "runs", coordinator), TestClient(api.app) as client:
                chat_id = str(uuid.uuid4())
                headers = {
                    "Authorization": "Bearer test-key-with-more-than-24-characters",
                    "X-User-ID": "alice", "X-Chat-ID": chat_id,
                }
                self.assertEqual(client.post(
                    "/sessions", headers=headers, json={"chat_id": chat_id},
                ).status_code, 200)
                buffer = io.BytesIO()
                Image.fromarray(np.zeros((4, 4), dtype="uint8")).save(buffer, format="PNG")
                upload = client.post(
                    "/assets", headers=headers,
                    files={"file": ("preview.png", buffer.getvalue(), "image/png")},
                )
                self.assertEqual(upload.status_code, 200)
                asset_id = upload.json()["id"]

                inline = client.get(
                    f"/files/{chat_id}/{asset_id}", params={"inline": 1}, headers=headers,
                )
                self.assertEqual(inline.status_code, 200)
                self.assertEqual(inline.headers["content-type"], "image/png")
                self.assertTrue(inline.headers["content-disposition"].startswith("inline"))

                download = client.get(
                    f"/files/{chat_id}/{asset_id}", params={"download": 1}, headers=headers,
                )
                self.assertEqual(download.status_code, 200)
                self.assertTrue(
                    download.headers["content-disposition"].startswith("attachment")
                )


if __name__ == "__main__":
    unittest.main()
