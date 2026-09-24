"""Corpus-wide quality audit of a pinned OAM-TCD shard (read-only).

The frozen pilot must be defensible: a sample with no foreground or a tile that
is largely empty cannot carry an IoU or a coverage statistic. This script reads
every row of the pinned parquet, measures the properties that decide usability,
and emits the evidence used to accept or reject a source. It never writes to the
frozen public/private roots.

Usage:
    python evaluation/grounded_v1/audit_shard.py \
        --shard data/oam_tcd/test-00000-of-00001.parquet \
        --out evaluation/work/oam_shard_audit.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from PIL import Image
from rasterio.io import MemoryFile

PINNED_SHA256 = "5c10ac4cb8afaf18dae5aad5b22cc86bb80977116a8bcd8c16d41ae48b9b13cf"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def measure_row(row: dict) -> dict:
    annotation = np.asarray(Image.open(io.BytesIO(row["annotation"]["bytes"])).convert("RGB"))
    colours = np.unique(annotation.reshape(-1, 3), axis=0)
    canopy = np.any(annotation != 0, axis=2)
    with MemoryFile(row["image"]["bytes"]) as image_file, image_file.open() as source:
        stack = source.read()
        grid_ok = bool(source.count == 3 and source.crs is not None and not source.crs.is_geographic)
        crs = str(source.crs)
        shape = [source.height, source.width]
        res = [float(value) for value in source.res]
    all_zero = int((stack == 0).all(axis=0).sum())
    pixels = int(canopy.size)
    canopy_pixels = int(canopy.sum())
    return {
        "canopy_pixels": canopy_pixels,
        "pixels": pixels,
        "canopy_fraction": round(canopy_pixels / pixels, 8) if pixels else 0.0,
        "empty_foreground": canopy_pixels == 0,
        "all_zero_image_pixels": all_zero,
        "zero_image_fraction": round(all_zero / pixels, 8) if pixels else 0.0,
        "annotation_colours": int(len(colours)),
        "grid_ok": grid_ok,
        "crs": crs,
        "shape": shape,
        "res": res,
    }


def audit(shard: Path) -> dict:
    digest = _sha256(shard)
    if digest != PINNED_SHA256:
        raise ValueError(f"shard sha256 {digest} does not match the pinned revision")

    reader = pq.ParquetFile(shard)
    records: list[dict] = []
    for batch in reader.iter_batches(
        batch_size=16,
        columns=["image_id", "oam_id", "license", "biome", "biome_name", "crs", "validation_fold", "image", "annotation"],
    ):
        for row in batch.to_pylist():
            record = {
                "image_id": int(row["image_id"]),
                "oam_id": row["oam_id"],
                "license": row["license"],
                "biome": row["biome"],
                "biome_name": row["biome_name"],
                "declared_crs": row["crs"],
                "validation_fold": row["validation_fold"],
            }
            record.update(measure_row(row))
            records.append(record)

    per_source: dict[str, dict] = {}
    for record in records:
        entry = per_source.setdefault(record["oam_id"], {"oam_id": record["oam_id"], "images": 0, "canopy_pixels": 0, "empty": 0, "zero_fraction_max": 0.0})
        entry["images"] += 1
        entry["canopy_pixels"] += record["canopy_pixels"]
        entry["empty"] += 1 if record["empty_foreground"] else 0
        entry["zero_fraction_max"] = max(entry["zero_fraction_max"], record["zero_image_fraction"])
    for entry in per_source.values():
        entry["empty_share"] = round(entry["empty"] / entry["images"], 4)
        entry["zero_fraction_max"] = round(entry["zero_fraction_max"], 4)

    usable = [record for record in records if not record["empty_foreground"] and record["grid_ok"]]
    return {
        "shard_sha256": digest,
        "rows": len(records),
        "sources": len(per_source),
        "licenses": dict(Counter(record["license"] for record in records)),
        "crs_values": dict(Counter(record["crs"] for record in records)),
        "grid_failures": [record["image_id"] for record in records if not record["grid_ok"]],
        "empty_foreground_rows": sum(1 for record in records if record["empty_foreground"]),
        "rows_with_zero_image_pixels": sum(1 for record in records if record["all_zero_image_pixels"] > 0),
        "rows_over_50pct_zero": sum(1 for record in records if record["zero_image_fraction"] > 0.5),
        "usable_rows": len(usable),
        "usable_sources": len({record["oam_id"] for record in usable}),
        "per_source": sorted(per_source.values(), key=lambda entry: entry["oam_id"]),
        "per_row": sorted(records, key=lambda record: (record["oam_id"], record["image_id"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    report = audit(args.shard)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("per_source", "per_row")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
