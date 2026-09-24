"""Build the synthetic "data inventory and suitability" fixtures.

Both conditions of ``capability.inventory`` use data with an independently known
answer:

* ``normal`` -- a mixed directory whose true composition is known because this
  script created it, with two planted quality problems (a truncated point-cloud
  file and a CSV whose data row is malformed);
* ``gap`` -- the same directory plus an archive whose bytes are truncated, so the
  honest answer is a partial inventory plus an explicit statement that one item
  cannot be assessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = PROJECT_ROOT / "evaluation" / "fixtures"
GOLD = FIXTURES / "gold"

PROMPT_NORMAL = """请盘点附件目录：说明有哪些数据类型、数量，是否存在缺项或质量问题，
并判断这些数据是否足以支持“生成研究区冠层高度模型（CHM）”这个目标。

请只做只读检查。在回答末尾给出一个 JSON 代码块，字段如下：

```json
{
  "counts": {"<扩展名>": 0},
  "quality_problems": [
    {"path": "文件名", "kind": "问题类型", "detail": "一句话说明"}
  ],
  "usable_for_chm": false,
  "missing_evidence": ["缺少什么证据"],
  "photogrammetry_started": false
}
```

要求：
- 数量按扩展名统计，只统计目录内的文件；**目录里出现的每一种扩展名都要出现，
  没有出现的扩展名不要写进去**（上面的字段示例不代表答案，必须自己枚举目录）；
- `quality_problems` 只列你确实发现的问题，没有就留空数组；
- `usable_for_chm` 表示“用这批数据能否生成可解释的 CHM”；
- `missing_evidence` 列出若要生成 CHM 还缺什么；
- 不要开始任何摄影测量或建模作业。
"""

PROMPT_GAP = """请盘点附件目录：说明有哪些数据类型、数量，是否存在缺项或质量问题，
并判断这些数据是否足以支持“生成研究区冠层高度模型（CHM）”这个目标。

请只做只读检查。在回答末尾给出一个 JSON 代码块，字段如下：

```json
{
  "counts": {"<扩展名>": 0},
  "quality_problems": [
    {"path": "文件名", "kind": "问题类型", "detail": "一句话说明"}
  ],
  "usable_for_chm": false,
  "missing_evidence": ["缺少什么证据"],
  "photogrammetry_started": false
}
```

要求：
- 数量按扩展名统计，只统计目录内的文件；**目录里出现的每一种扩展名都要出现，
  没有出现的扩展名不要写进去**（上面的字段示例不代表答案，必须自己枚举目录）；
- 无法读取或无法核对的条目必须写进 `quality_problems`，不要静默跳过；
- `usable_for_chm` 表示“用这批数据能否生成可解释的 CHM”；
- `missing_evidence` 列出若要生成 CHM 还缺什么；
- 不要开始任何摄影测量或建模作业。
"""

CSV_HEADER = "plot_id,tree_id,species,height_m,dbh_cm\n"
CSV_ROWS = [
    "P-01,T-001,Pinus,12.4,18.2",
    "P-01,T-002,Pinus,11.8,",
    "P-01,T-003,Quercus,9.6,14.0",
    "P-02,T-004,Pinus,10.2,15.5",
    "P-02,T-005,Quercus,8.9,12.7",
    "P-03,T-006,Pinus,,",
]
CSV_BROKEN_ROW = "P-03,T-007,Pinus\n"


def _png_bytes(width: int = 4, height: int = 4) -> bytes:
    """A tiny but structurally valid 8-bit greyscale PNG."""
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes((row * 20) % 256 for _ in range(width)) for row in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _tif_bytes(width: int = 6, height: int = 5) -> bytes:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    profile = {
        "driver": "GTiff", "width": width, "height": height, "count": 1,
        "dtype": "float32", "crs": "EPSG:32650",
        "transform": from_origin(500000.0, 3000000.0, 1.0, 1.0),
        "nodata": -9999.0,
    }
    data = (np.arange(width * height, dtype="float32").reshape(height, width) % 7) + 1.0
    import io

    buffer = io.BytesIO()
    with rasterio.open(buffer, "w", **profile) as dataset:
        dataset.write(data, 1)
    return buffer.getvalue()


def _jpeg_bytes(shade: int = 128, size: int = 8) -> bytes:
    """A small but genuinely decodable JPEG.

    Edge-case fixtures must contain exactly the problems the contract declares. A
    hand-rolled JPEG marker soup would add a *third*, undeclared problem, and the
    agent would be right to report it while the contract only expected two.
    """
    import io

    from PIL import Image

    image = Image.new("L", (size, size), shade)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def _las_header(point_count: int) -> bytes:
    header = bytearray(227)
    header[0:4] = b"LASF"
    header[24] = 1
    header[25] = 2
    struct.pack_into("<I", header, 96, 0)
    struct.pack_into("<H", header, 94, 227)
    struct.pack_into("<H", header, 104, 1)
    struct.pack_into("<I", header, 107, point_count)
    return bytes(header)


def _zip_bytes(entries: dict[str, bytes], *, truncate: int = 0) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    raw = buffer.getvalue()
    return raw[:-truncate] if truncate else raw


def build(destination: Path, *, gap: bool = False) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    for existing in destination.iterdir():
        if existing.is_file():
            existing.unlink()

    files: dict[str, bytes] = {}
    for index in range(1, 5):
        files[f"DJI_{index:04d}.PNG"] = _png_bytes()
    files["flight_index.csv"] = (
        CSV_HEADER + "\n".join(CSV_ROWS[:2]) + "\n" + CSV_BROKEN_ROW
    ).encode("utf-8")
    files["plot_trees.csv"] = (CSV_HEADER + "\n".join(CSV_ROWS) + "\n").encode("utf-8")
    files["参考板_起飞前_01.JPG"] = _jpeg_bytes(90)
    files["参考板_起飞后_01.JPG"] = _jpeg_bytes(170)
    files["canopy_tiles.tif"] = _tif_bytes()
    # A point cloud whose header promises 500 points but supplies 12 bytes of them.
    files["points.las"] = _las_header(500) + b"\x00" * 12
    if gap:
        files["flight_archive.zip"] = _zip_bytes(
            {"DJI_0001.PNG": _png_bytes()}, truncate=40
        )

    written: dict[str, str] = {}
    for name, payload in files.items():
        target = destination / name
        target.write_bytes(payload)
        written[name] = hashlib.sha256(payload).hexdigest()

    counts = {
        "png": sum(1 for name in files if name.casefold().endswith(".png")),
        "jpg": sum(1 for name in files if name.casefold().endswith(".jpg")),
        "tif": sum(1 for name in files if name.casefold().endswith(".tif")),
        "csv": sum(1 for name in files if name.casefold().endswith(".csv")),
        "las": sum(1 for name in files if name.casefold().endswith(".las")),
        "zip": sum(1 for name in files if name.casefold().endswith(".zip")),
    }
    quality = [
        {
            "path": "points.las",
            "kind": "truncated_point_cloud",
            "why": "the LAS header declares 500 points but the file ends after 12 "
                   "bytes of point data, so the cloud cannot be read completely",
        },
        {
            "path": "flight_index.csv",
            "kind": "malformed_row",
            "why": "a data row has fewer fields than the header, so the index cannot "
                   "be parsed reliably",
        },
    ]
    if gap:
        quality.append({
            "path": "flight_archive.zip",
            "kind": "unreadable_archive",
            "why": "the ZIP central directory is missing (the file is truncated), so "
                   "its contents cannot be listed or extracted",
        })
    return {
        "version": "capability_inventory-v1",
        "condition": "gap" if gap else "normal",
        "files": sorted(files),
        "file_sha256": written,
        "counts_by_extension": counts,
        "quality_problems": quality,
        "usable_for_chm": False,
        "usable_for_chm_reason": (
            "the directory holds a single small DSM-like tile and no DTM, no "
            "survey-grade products and no ground reference, so a canopy height model "
            "cannot be produced or validated from it"
        ),
        "required_evidence_for_chm": [
            "a DTM matching the DSM grid, CRS and vertical reference",
            "an explicit vertical reference for both surfaces",
        ],
        "forbidden_claims": [
            "photogrammetry or modelling was started",
            "the products are geometrically or radiometrically validated",
            "species, biomass or DBH were derived",
        ],
    }


def main() -> int:
    for condition, gap in (("normal", False), ("gap", True)):
        directory = FIXTURES / f"capability_inventory_{condition}"
        contract = build(directory, gap=gap)
        (directory / "prompt.txt").write_text(
            PROMPT_GAP if gap else PROMPT_NORMAL, encoding="utf-8"
        )
        target = GOLD / f"capability_inventory_{condition}.json"
        target.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"{condition}: {len(contract['files'])} files -> {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
