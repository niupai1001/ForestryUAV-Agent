"""Generate the frozen forestry.inventory fixture and its evaluator-only truth.

The case asks for a read-only role manifest of one flight's archive: flight imagery,
the reference panels before and after takeoff, and the deliverables that already
exist. Its whole point is that those roles must stay separate -- a panel mixed into
the flight count is the classic way to overstate a photogrammetry input.

Two constraints decide the fixture's shape:

* A collection uploads one asset per file and the upload is flat, so the roles cannot
  be expressed as directories. They are carried in the **filenames** instead,
  using the same tokens the Runtime's own role classifier reads (``起飞前``,
  ``起飞后``, ``正射影像``). A fixture that nested them would arrive flattened and
  make the gold unreachable for reasons that are not the agent's.
* A single upload cannot show an *exposure group*, so the per-file capture and band
  facts ship as a real sidecar CSV inside the fixture. The case then tests
  cross-referencing an index against a listing, which is what an inventory is.

``manifest.csv`` is deliberately part of the fixture and not part of the manifest: an
agent that counts every uploaded file will overstate the flight count, and the gold
records that.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


FLIGHT = 6
PANEL_BEFORE = 2
PANEL_AFTER = 2
BANDS = ("Blue", "Green", "Red", "RedEdge", "NIR")

FLIGHT_GROUPS = ("flightA-0001", "flightA-0002", "flightA-0003")
PANEL_GROUPS = ("panelB-0001", "panelB-0002")

PRODUCT = "1605-白桦_正射影像.tif"
# Not "manifest.csv": the Runtime's product classifier matches tokens as substrings,
# and "manifest" contains "dom", so a file named that way is classified as an
# orthomosaic. The index is named to stay outside every product token.
INDEX = "capture_index.csv"

INVENTORY_PROMPT = """附件是同一航次上交的归档文件，全部为只读。

要求：
1. 只读盘点，不要修改、移动或删除任何文件，也不要启动摄影测量任务。
2. 把这些文件分成三类并分别报告数量：主航线的航片、参考板影像（起飞前/起飞后分开）、
   已有的地理成果。参考板不属于主航线，不要并入航片计数。
3. 附件 capture_index.csv 记录了每个文件的拍摄组与波段。据此报告：涉及多少个不同的拍摄组、
   以及主航线航片覆盖了哪些波段（每个波段各多少张）。
4. 不要从文件是否齐全推断重建一定会成功，也不要声称精度或 RTK 结论。
5. 最后用一个 JSON 代码块报告，字段固定为：
   {"flight_imagery": <整数>, "reference_panel_before": <整数>,
    "reference_panel_after": <整数>, "geospatial_products": <整数>,
    "capture_groups": <整数>, "bands": {"<波段名>": <整数>}}
"""


def _rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(FLIGHT):
        band = BANDS[index % len(BANDS)]
        rows.append({
            "file": f"DJI_{index + 1:04d}_{band}.JPG",
            "role": "flight_imagery",
            "band": band,
            "capture_group": FLIGHT_GROUPS[index % len(FLIGHT_GROUPS)],
        })
    for index in range(PANEL_BEFORE):
        rows.append({
            "file": f"参考板_起飞前_{index + 1:02d}_Red.JPG",
            "role": "reference_panel_before", "band": "Red",
            "capture_group": PANEL_GROUPS[index % len(PANEL_GROUPS)],
        })
    for index in range(PANEL_AFTER):
        rows.append({
            "file": f"参考板_起飞后_{index + 1:02d}_Red.JPG",
            "role": "reference_panel_after", "band": "Red",
            "capture_group": PANEL_GROUPS[index % len(PANEL_GROUPS)],
        })
    rows.append({"file": PRODUCT, "role": "geospatial_product", "band": "",
                 "capture_group": ""})
    return rows


def build(fixtures_root: Path, gold_root: Path) -> dict:
    directory = fixtures_root / "forestry_inventory"
    directory.mkdir(parents=True, exist_ok=True)
    for existing in directory.iterdir():
        if existing.is_file():
            existing.unlink()

    rows = _rows()
    for row in rows:
        target = directory / row["file"]
        if target.suffix.casefold() in {".jpg", ".jpeg"}:
            # Real bytes are unnecessary: the case reads names and the index.
            target.write_bytes(b"\xff\xd8\xff\xe0synthetic")
        else:
            target.write_bytes(b"synthetic geospatial product placeholder")
    (directory / INDEX).write_text(
        "file,role,band,capture_group\n" + "".join(
            f"{row['file']},{row['role']},{row['band']},{row['capture_group']}\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    (directory / "prompt.txt").write_text(INVENTORY_PROMPT, encoding="utf-8")

    flight = [row for row in rows if row["role"] == "flight_imagery"]
    bands: dict[str, int] = {}
    for row in flight:
        bands[row["band"]] = bands.get(row["band"], 0) + 1
    # Only files that belong to a capture have one; the existing orthomosaic does not,
    # so an empty group must not be counted as a group.
    groups = {row["capture_group"] for row in rows if row["capture_group"]}
    gold = {
        "version": "forestry.inventory-v1",
        "fixture_files": [],
        # Every uploaded file, with the role the Runtime's own classifier assigns it.
        # The verifier recomputes roles from these names rather than trusting a table.
        "files": [row["file"] for row in rows] + [INDEX],
        "index_file": INDEX,
        "roles": {
            "flight_imagery": len(flight),
            "reference_panel_before": PANEL_BEFORE,
            "reference_panel_after": PANEL_AFTER,
            "geospatial_product": 1,
        },
        "total_uploaded_files": len(rows) + 1,
        "capture_groups": len(groups),
        "flight_capture_groups": len(set(FLIGHT_GROUPS)),
        "bands": dict(sorted(bands.items())),
        "answer_claims": {
            "flight_imagery": len(flight),
            "reference_panel_before": PANEL_BEFORE,
            "reference_panel_after": PANEL_AFTER,
            "geospatial_products": 1,
            "capture_groups": len(groups),
            "bands": dict(sorted(bands.items())),
        },
        # Statements the case forbids: a complete file set is not evidence that
        # reconstruction succeeds, and nothing here measures accuracy.
        "forbidden_claims": ["精度", "RTK", "一定成功", "保证"],
    }
    gold_root.mkdir(parents=True, exist_ok=True)
    target = gold_root / "forestry_inventory.json"
    target.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fixture": str(directory), "gold": str(target), "roles": gold["roles"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("evaluation/fixtures"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/fixtures/gold"))
    args = parser.parse_args()
    print(json.dumps(build(args.fixtures.resolve(), args.gold.resolve()),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
