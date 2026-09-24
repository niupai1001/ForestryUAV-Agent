"""Read-only audit of the frozen grounded-v1 selection against the pinned shard.

The plan's phase A step 1 requires a hand review of the selected samples'
valid domain, label encoding and source distribution before the pilot runs.
This script produces that evidence: it re-reads the pinned parquet, does not
touch the public/private roots, and prints a JSON record per task.

Usage:
    python evaluation/grounded_v1/audit_sources.py \
        --shard data/oam_tcd/test-00000-of-00001.parquet \
        --manifest data/oam_tcd/grounded_v1_private/manifest.json
"""

from __future__ import annotations

import argparse
import io
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from PIL import Image
from rasterio.io import MemoryFile


def _locate(reader: pq.ParquetFile, wanted: set[int]) -> dict[int, tuple[int, int]]:
    found: dict[int, tuple[int, int]] = {}
    for group in range(reader.metadata.num_row_groups):
        for offset, row in enumerate(reader.read_row_group(group, columns=["image_id"]).to_pylist()):
            identifier = int(row["image_id"])
            if identifier in wanted and identifier not in found:
                found[identifier] = (group, offset)
    return found


def audit(shard: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reader = pq.ParquetFile(shard)
    tasks = manifest["tasks"]
    wanted = {int(task["image_id"]) for task in tasks}
    located = _locate(reader, wanted)
    if len(located) != len(wanted):
        raise ValueError(f"Only located {len(located)} of {len(wanted)} selected rows")

    records = []
    for task in tasks:
        identifier = int(task["image_id"])
        group, offset = located[identifier]
        row = reader.read_row_group(
            group, columns=["image", "annotation", "meta", "segments", "coco_annotations", "validation_fold"]
        ).slice(offset, 1).to_pylist()[0]

        annotation = np.asarray(Image.open(io.BytesIO(row["annotation"]["bytes"])).convert("RGB"))
        colours, counts = np.unique(annotation.reshape(-1, 3), axis=0, return_counts=True)
        order = np.argsort(-counts)
        spectrum = [
            {"rgb": colours[i].tolist(), "pixels": int(counts[i])}
            for i in order[:6]
        ]
        canopy = np.any(annotation != 0, axis=2)

        with MemoryFile(row["image"]["bytes"]) as image_file, image_file.open() as source:
            stack = source.read()
            declared_nodata = source.nodata
            grid = {
                "crs": str(source.crs),
                "height": source.height,
                "width": source.width,
                "res": [float(value) for value in source.res],
                "transform": [float(value) for value in source.transform[:6]],
                "count": source.count,
                "dtype": source.dtypes[0],
                "declared_nodata": None if declared_nodata is None else float(declared_nodata),
            }

        all_zero = (stack == 0).all(axis=0)
        records.append({
            "task_id": task["task_id"],
            "kind": task["kind"],
            "image_id": identifier,
            "oam_id": task["oam_id"],
            "biome": task["biome"],
            "validation_fold": row["validation_fold"],
            "meta": row["meta"],
            "has_instance_annotations": bool(row["segments"] not in ("", "[]") or row["coco_annotations"] not in ("", "[]")),
            "grid": grid,
            "annotation_spectrum": spectrum,
            "annotation_is_binary": len(colours) <= 2,
            "distinct_annotation_colours": int(len(colours)),
            "canopy_pixels": int(canopy.sum()),
            "canopy_fraction": round(float(canopy.mean()), 6),
            "declared_gold_canopy_pixels": task["gold_canopy_pixels"],
            "canopy_matches_manifest": int(canopy.sum()) == int(task["gold_canopy_pixels"]),
            "fully_zero_image_pixels": int(all_zero.sum()),
            "zero_image_fraction": round(float(all_zero.mean()), 6),
            "image_band_minima": [int(stack[b].min()) for b in range(stack.shape[0])],
            "image_band_maxima": [int(stack[b].max()) for b in range(stack.shape[0])],
            "image_band_means": [round(float(stack[b].mean()), 3) for b in range(stack.shape[0])],
        })

    return {
        "manifest_version": manifest["version"],
        "dataset": manifest["dataset"],
        "revision": manifest["revision"],
        "test_shard_sha256": manifest["test_shard_sha256"],
        "tasks": records,
        "summary": {
            "task_count": len(records),
            "distinct_sources": len({record["oam_id"] for record in records}),
            "canopy_tasks": sum(1 for record in records if record["kind"] == "canopy"),
            "spatial_tasks": sum(1 for record in records if record["kind"] == "spatial"),
            "empty_foreground_tasks": [record["task_id"] for record in records if record["canopy_pixels"] == 0],
            "grid_mismatches": [record["task_id"] for record in records if not record["canopy_matches_manifest"]],
            "non_binary_annotations": [record["task_id"] for record in records if not record["annotation_is_binary"]],
            "tasks_with_declared_nodata": [record["task_id"] for record in records if record["grid"]["declared_nodata"] is not None],
            "tasks_with_zero_image_pixels": [record["task_id"] for record in records if record["fully_zero_image_pixels"] > 0],
            "biome_distribution": dict(Counter(record["biome"] for record in records)),
            "canopy_source_count": len({record["oam_id"] for record in records if record["kind"] == "canopy"}),
            "spatial_source_count": len({record["oam_id"] for record in records if record["kind"] == "spatial"}),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    report = audit(args.shard, args.manifest)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
