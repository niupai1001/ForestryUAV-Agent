"""Render the grounded-v1 source audit as a review table.

Reads the JSON produced by ``audit_sources.py`` and prints a compact Markdown
table so the phase-A hand review can be filed with the frozen suite.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def render(report: dict) -> str:
    lines = [
        "| task | kind | biome | fold | canopy frac | zero-image frac | ann colours | declared nodata |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for task in report["tasks"]:
        lines.append(
            "| {task_id} | {kind} | {biome} | {fold} | {canopy:.4f} | {zeros:.4f} | {colours} | {nodata} |".format(
                task_id=task["task_id"],
                kind=task["kind"],
                biome=task["biome"],
                fold=task["validation_fold"],
                canopy=task["canopy_fraction"],
                zeros=task["zero_image_fraction"],
                colours=task["distinct_annotation_colours"],
                nodata=task["grid"]["declared_nodata"],
            )
        )
    summary = report["summary"]
    lines += [
        "",
        f"- tasks: {summary['task_count']} ({summary['canopy_tasks']} canopy / {summary['spatial_tasks']} spatial)",
        f"- distinct sources: {summary['distinct_sources']} "
        f"(canopy {summary['canopy_source_count']}, spatial {summary['spatial_source_count']})",
        f"- empty-foreground tasks: {summary['empty_foreground_tasks']}",
        f"- manifest mismatches: {summary['grid_mismatches']}",
        f"- declared nodata: {summary['tasks_with_declared_nodata']}",
        f"- tiles containing all-zero pixels: {summary['tasks_with_zero_image_pixels']}",
        f"- biome distribution: {summary['biome_distribution']}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    text = render(json.loads(args.audit.read_text(encoding="utf-8")))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
