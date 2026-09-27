"""Phase 1 acceptance: one input reference that every file tool accepts.

The behaviour under test, stated as the failure it replaces: a file could be listed
in the workspace and still be unusable from the domain tools, because those accepted
only an ``asset_id`` while the file was named by a relative ``path``. The model saw
one file and two incompatible ways to name it, and nothing said which conversion was
legal.

So the contract is:

* a workspace file, an attachment and a file under an authorized source directory are
  all reachable from inspection, analysis and preview tools;
* the reference that resolved is returned with the result, so the next tool call is a
  copy rather than a guess;
* a reference that cannot be resolved reports what was checked, not a bare failure.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import numpy as np
from rasterio.transform import from_origin
import rasterio

from runtime.capabilities.domain_runtime import RemoteSensingTools
from runtime.capabilities.runtime import RuntimeTools
from runtime.workspace import WorkspaceRegistry


def _write_rgb(path: Path, *, width=16, height=12, count=3) -> bytes:
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=count,
        dtype="uint16", crs="EPSG:32650",
        transform=from_origin(500000, 3100000, 0.1, 0.1), nodata=0,
    ) as dataset:
        dataset.write(
            np.arange(count * height * width, dtype="uint16").reshape(count, height, width)
        )
        dataset.set_band_description(1, "Red")
        dataset.set_band_description(2, "Green")
        dataset.set_band_description(3, "NIR")
    return path.read_bytes()


class _Bridge:
    """Minimal host bridge: directory resolution, listing and staging."""

    available = True
    key = "x" * 32

    def __init__(self):
        self.staged: list[dict] = []

    def resolve(self, path):
        target = Path(path).resolve(strict=True)
        return {"path": str(target), "type": "directory" if target.is_dir() else "file"}

    def fs(self, operation, payload):
        root = Path(payload["root"])
        if operation == "stage":
            source = root / str(payload["path"])
            if not source.is_file():
                from runtime.workspace import BridgeRequestError
                raise BridgeRequestError("missing", status_code=404, code="path_not_found")
            workspace = Path(payload["workspace"])
            directory = workspace / ".runtime" / "staged"
            directory.mkdir(parents=True, exist_ok=True)
            staged = directory / source.name
            staged.write_bytes(source.read_bytes())
            self.staged.append({"path": str(source), "staged": str(staged)})
            return {
                "staged_path": staged.relative_to(workspace).as_posix(),
                "relative_path": str(payload["path"]),
                "name": source.name,
                "size_bytes": staged.stat().st_size,
                "content_id": "deadbeef",
                "reused": False,
                "read_only": True,
            }
        if operation == "list":
            target = root / str(payload.get("path") or ".")
            rows = [
                {"name": item.name, "path": item.name,
                 "type": "directory" if item.is_dir() else "file",
                 "size": item.stat().st_size if item.is_file() else None}
                for item in sorted(target.iterdir(), key=lambda value: value.name.casefold())
            ]
            return {"items": rows, "page": 1, "page_size": 100, "total": len(rows),
                    "has_more": False}
        raise AssertionError(f"unexpected bridge operation {operation}")

    def job(self, operation, payload):
        raise AssertionError("no job is expected in these tests")


class InputReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        from runtime.lifecycle import Sessions

        self.sessions = Sessions(self.root)
        self.chat_id = str(uuid.uuid4())
        self.sessions.create("alice", self.chat_id)
        self.store = self.sessions.acquire("alice", self.chat_id)
        self.sessions.release(self.chat_id)
        self.registry = WorkspaceRegistry(self.root)
        self.bridge = _Bridge()
        self.registry.bridge = self.bridge
        self.workspace = self.registry.workspace(self.store)
        self.box = RuntimeTools(self.store, "alice", [], self.registry, "")

    def tearDown(self):
        self.temp.cleanup()

    def domain(self, asset_ids=()):
        return RemoteSensingTools(
            self.store, "alice", list(asset_ids), self.registry, "",
        )

    # ------------------------------------------------------------------ workspace

    def test_workspace_file_is_inspectable_without_an_asset_id(self):
        _write_rgb(self.workspace / "oam-02.tif")
        result = self.domain().execute("inspect_file", {"path": "oam-02.tif"})
        self.assertTrue(result["ok"], result)
        data = result["data"]
        self.assertEqual(data["width"], 16)
        self.assertEqual(data["band_count"], 3)
        self.assertEqual(data["exact_reference"], {"scope": "workspace", "path": "oam-02.tif"})
        self.assertNotIn("id", data)

    def test_exact_reference_passes_unchanged_to_the_next_tool(self):
        _write_rgb(self.workspace / "oam-02.tif")
        tools = self.domain()
        inspected = tools.execute("inspect_file", {"path": "oam-02.tif"})
        reference = inspected["data"]["exact_reference"]
        # The whole point: the next call is a copy of a returned value.
        raster = tools.execute("inspect_raster", dict(reference))
        self.assertTrue(raster["ok"], raster)
        self.assertEqual(raster["data"]["width"], 16)
        self.assertEqual(raster["data"]["exact_reference"], reference)
        preview = tools.execute("preview_image", dict(reference))
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["data"]["bands_used"], [1, 2, 3])
        chained = preview["data"]["exact_reference"]
        self.assertEqual(chained, {"scope": "workspace", "path": "oam-02.tif"})

    def test_workspace_multispectral_runs_the_whole_chain(self):
        _write_rgb(self.workspace / "ms.tif", count=3)
        tools = self.domain()
        ndvi = tools.execute("calculate_ndvi", {
            "path": "ms.tif", "bands": {"red": 1, "nir": 3},
        })
        self.assertTrue(ndvi["ok"], ndvi)
        ndvi_id = ndvi["data"]["ndvi"]["id"]
        segmented = tools.execute("segment_canopy", {"asset_id": ndvi_id})
        self.assertTrue(segmented["ok"], segmented)
        self.assertEqual(segmented["data"]["mask"]["parent_id"], ndvi_id)

    def test_missing_workspace_path_names_what_was_checked(self):
        result = self.domain().execute("inspect_file", {"path": "absent.tif"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["code"], "workspace_path_not_found")
        self.assertEqual(
            result["failure"]["checked_scope"], {"scope": "workspace", "path": "absent.tif"}
        )

    # ------------------------------------------------------------------- assets

    def test_attachment_is_still_addressed_by_asset_id(self):
        payload = _write_rgb(self.root / "attached.tif")
        asset = self.store.put(io.BytesIO(payload), "attached.tif", "alice", "image/tiff")
        tools = self.domain([asset["id"]])
        result = tools.execute("inspect_file", {"asset_id": asset["id"]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["id"], asset["id"])
        self.assertEqual(result["data"]["exact_reference"],
                         {"scope": "asset", "asset_id": asset["id"]})

    def test_unknown_asset_reports_the_candidates_it_could_use(self):
        payload = _write_rgb(self.root / "attached.tif")
        asset = self.store.put(io.BytesIO(payload), "attached.tif", "alice", "image/tiff")
        result = self.domain([asset["id"]]).execute("inspect_file", {"asset_id": "asset_nope"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["code"], "asset_not_available")
        self.assertEqual(result["failure"]["candidates"][0]["asset_id"], asset["id"])

    # ------------------------------------------------------------------- source

    def test_authorized_source_file_is_staged_and_usable(self):
        host = self.root / "hostdata"
        host.mkdir()
        payload = _write_rgb(host / "flight.tif")
        grant = self.registry.grant("alice", self.chat_id, str(host), "user asked", "read")
        with patch.dict(os.environ, {"RUNTIME_DATA_HOST_ROOT": str(self.root)}):
            result = self.domain().execute("inspect_file", {
                "scope": "source", "source_id": grant["id"], "path": "flight.tif",
            })
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["width"], 16)
        self.assertEqual(result["data"]["exact_reference"],
                         {"scope": "source", "source_id": grant["id"], "path": "flight.tif"})
        self.assertEqual(result["data"]["provenance"]["kind"], "source")
        self.assertTrue(result["data"]["provenance"]["read_only"])
        self.assertEqual(len(self.bridge.staged), 1)
        self.assertEqual(Path(self.bridge.staged[0]["path"]).read_bytes(), payload)

    def test_source_file_is_read_only_and_never_written_back(self):
        host = self.root / "hostdata"
        host.mkdir()
        original = _write_rgb(host / "flight.tif")
        grant = self.registry.grant("alice", self.chat_id, str(host), "user asked", "read")
        with patch.dict(os.environ, {"RUNTIME_DATA_HOST_ROOT": str(self.root)}):
            result = self.domain().execute("inspect_file", {
                "scope": "source", "source_id": grant["id"], "path": "flight.tif",
            })
        self.assertTrue(result["ok"], result)
        self.assertEqual((host / "flight.tif").read_bytes(), original)
        self.assertEqual(grant["access"], "read")

    # ----------------------------------------------------------------- previews

    def test_artifacts_inspect_accepts_workspace_and_asset(self):
        payload = _write_rgb(self.root / "picture.tif")
        (self.workspace / "picture.tif").write_bytes(payload)
        asset = self.store.put(io.BytesIO(payload), "attached.tif", "alice", "image/tiff")
        box = RuntimeTools(self.store, "alice", [asset["id"]], self.registry, "")

        by_path = box.execute("artifacts_inspect", {"path": "picture.tif"})
        self.assertTrue(by_path["ok"], by_path)
        self.assertEqual(by_path["data"]["exact_reference"],
                         {"scope": "workspace", "path": "picture.tif"})

        by_asset = box.execute("artifacts_inspect", {"asset_id": asset["id"]})
        self.assertTrue(by_asset["ok"], by_asset)
        self.assertEqual(by_asset["data"]["exact_reference"],
                         {"scope": "asset", "asset_id": asset["id"]})

        preview = box.execute("artifacts_preview", {"asset_id": asset["id"]})
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["data"]["preview_asset"]["media_type"], "image/png")

    def test_artifacts_inspect_of_a_missing_path_is_a_precondition(self):
        box = RuntimeTools(self.store, "alice", [], self.registry, "")
        result = box.execute("artifacts_inspect", {"path": "nowhere.tif"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["code"], "workspace_path_not_found")


if __name__ == "__main__":
    unittest.main()
