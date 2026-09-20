"""Assemble trial records while rebasing evidence paths to records.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _rebase(items: list[str], source_root: Path, records_root: Path) -> list[str]:
    rebased = []
    for item in items:
        path = Path(item)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe evidence path in trial record: {item}")
        resolved = (source_root / path).resolve()
        if source_root.resolve() not in resolved.parents or not resolved.is_file():
            raise ValueError(f"Missing trial evidence: {item}")
        try:
            relative = resolved.relative_to(records_root.resolve())
        except ValueError as exc:
            raise ValueError("Trial evidence must be inside the records package") from exc
        rebased.append(relative.as_posix())
    return rebased


def assemble(record_files: list[Path], output: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record_file in record_files:
        record = json.loads(record_file.read_text(encoding="utf-8"))
        source_root = record_file.resolve().parent
        status = record.get("status_evidence", {})
        status["evidence"] = _rebase(
            status.get("evidence", []), source_root, output.resolve().parent
        )
        for check in record.get("checks", {}).values():
            check["evidence"] = _rebase(
                check.get("evidence", []), source_root, output.resolve().parent
            )
        records.append(record)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = assemble(args.record, args.output)
    print(json.dumps({"records": len(records), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
