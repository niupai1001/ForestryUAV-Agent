"""Merge per-plot heights into one summary row per plot."""

import csv
import json
from pathlib import Path


INPUT = Path("samples.csv")
OUTPUT = Path("summary.json")


def load_rows():
    with INPUT.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def summarise(rows):
    plots = {}
    for row in rows:
        plot = row["plot_id"]
        bucket = plots.setdefault(plot, {"heights": [], "missing": 0})
        height = row["height_m"].strip()
        if height:
            bucket["heights"].append(float(height))
        else:
            bucket["missing"] += 1
    return plots


def main():
    rows = load_rows()
    plots = summarise(rows)
    summary = []
    for plot, bucket in sorted(plots.items()):
        heights = bucket["heights"]
        summary.append({
            "plot_id": plot,
            "tree_records": len(heights) + bucket["missing"],
            "observed_heights": len(heights),
            "missing_heights": bucket["missing"],
            # BUG: divides by the count of observed heights, which is zero for P-03,
            # whose only record has no height.
            "mean_height_m": sum(heights) / len(heights),
        })
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"plots": len(summary), "output": str(OUTPUT)}))


if __name__ == "__main__":
    main()
