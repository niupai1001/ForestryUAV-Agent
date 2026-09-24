"""Generate the frozen core.* fixtures and their evaluator-only truth.

Three cases share this generator because they exercise the same basic loop --
observe a real directory, then act on what was observed:

* ``core_paths``  a read-only inventory of awkward names: CJK names one character
  apart, an embedded space, a name that is a prefix of its sibling's, and an
  extension no domain tool handles. The gold listing is frozen so the verifier
  compares names exactly instead of accepting a plausible-looking answer.

  The files sit at the top level on purpose. A collection uploads one asset per
  file, so a nested tree would arrive flattened anyway; a fixture that pretended
  otherwise would make the gold unreachable and fail every Run for a reason that
  is not the agent's.

* ``core_repair`` a script that fails for one real, diagnosable reason on one plot.
  The gold records the error and the expected output, so "fixed it" is checked by
  rerunning the agent's own code rather than by reading its claim.

* ``core_changed_input`` the same script plus a changed value, so a rerun must
  produce a new result instead of restating the first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


FLAT_FILES = (
    "IMG_0001.JPG",
    "IMG_0001.JPG.bak",
    "IMG_0003.JPG",
    "reference_after.JPG",
    "reference_before.JPG",
    "1605白桦.tif",
    "1605桦.tif",
    "notes with spaces.txt",
    "README",
)

PATH_PROMPT = """附件是同一批无人机影像的归档目录。请只做只读盘点。

要求：
1. 报告该目录的第一层内容：每个条目的名称和类型（文件还是目录）。
2. 不要读取影像内容，也不要创建、修改或删除任何文件。
3. 名称必须与磁盘上完全一致，包括中文、空格与大小写；不要补空格、改写或翻译名称。
4. 最后用一个 JSON 代码块报告，字段固定为：
   {"entries": [{"name": "<名称>", "type": "file" 或 "directory"}],
    "entry_count": <整数>}
"""

BROKEN_SCRIPT = '''"""Merge per-plot heights into one summary row per plot."""

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
'''

REPAIR_PROMPT = """附件 broken.py 应当把 samples.csv 按样地汇总成一个 JSON。

现在运行它会失败。要求：
1. 先真实运行它，观察实际的错误信息，不要凭猜测修改。
2. 定位并修复这个错误，使它对全部样地都能成功。
3. 再次真实运行，确认成功。
4. 最后用一个 JSON 代码块报告，字段固定为：
   {"observed_error": "<你第一次运行时看到的错误类型>", "fixed": true,
    "plot_count": <整数>, "output_file": "<产出文件名>"}
"""

SAMPLES = """plot_id,tree_id,height_m
P-01,T-001,10.0
P-01,T-002,
P-01,T-003,12.0
P-02,T-004,8.5
P-02,T-005,9.5
P-03,T-006,
"""

# The state core.changed_input must end in. The fixture uploads the version above --
# the state *before* the change -- so the uploaded input can never contain the answer.
SAMPLES_UPDATED = SAMPLES.replace("P-02,T-005,9.5", "P-02,T-005,11.5")

CHANGED_PROMPT = """附件 samples.csv 与 broken.py 已经修好可用，附件本身是只读的。

要求：
1. 先把附件复制到工作区，运行 broken.py 生成 summary.json。
2. 把工作区副本中 P-02 的 T-005 高度从 9.5 改为 11.5。附件原件必须保持不变。
3. 再次运行 broken.py，生成反映新数值的 summary.json。
4. 最后用一个 JSON 代码块报告，字段固定为：
   {"new_p02_mean_height_m": <数值>, "rerun": true}
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_paths(fixtures_root: Path, gold_root: Path) -> dict:
    directory = fixtures_root / "core_paths"
    for name in FLAT_FILES:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.casefold() in {".jpg", ".tif"}:
            # Real bytes are unnecessary; the case reads names and types only.
            target.write_bytes(b"\xff\xd8\xff\xe0synthetic")
        else:
            _write(target, f"synthetic fixture for {name}\n")
    _write(directory / "prompt.txt", PATH_PROMPT)

    entries = [
        {"name": name, "type": "file"} for name in sorted(FLAT_FILES)
    ]
    gold = {
        "version": "core.paths-v1",
        "root": ".",
        "fixture_files": [],
        "entries": entries,
        "entry_count": len(entries),
    }
    gold_root.mkdir(parents=True, exist_ok=True)
    target = gold_root / "core_paths.json"
    target.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fixture": str(directory), "gold": str(target), "entries": entries}


def build_repair(fixtures_root: Path, gold_root: Path, *, case: str,
                 samples: str, prompt: str, answer_claims: dict,
                 expected_samples: str | None = None) -> dict:
    directory = fixtures_root / case
    _write(directory / "broken.py", BROKEN_SCRIPT)
    _write(directory / "samples.csv", samples)
    _write(directory / "prompt.txt", prompt)

    graded = expected_samples or samples
    rows = [line.split(",") for line in graded.strip().splitlines()[1:]]
    plots: dict[str, list[float | None]] = {}
    for plot, _tree, height in rows:
        plots.setdefault(plot, []).append(float(height) if height.strip() else None)
    expected = {
        plot: {
            "tree_records": len(values),
            "observed_heights": sum(1 for value in values if value is not None),
            "missing_heights": sum(1 for value in values if value is None),
            # P-03 has no observed height, so its mean is genuinely absent rather
            # than zero. A repair that reports 0.0 changes the answer, not the bug.
            "mean_height_m": (
                round(sum(v for v in values if v is not None)
                      / sum(1 for v in values if v is not None), 10)
                if any(value is not None for value in values) else None
            ),
        }
        for plot, values in sorted(plots.items())
    }
    gold = {
        "version": f"{case}-v1",
        "fixture_files": ["samples.csv", "broken.py"],
        # Running the fixture raises ZeroDivisionError on the all-missing plot.
        "observed_error": "ZeroDivisionError",
        "samples_sha256": hashlib.sha256(samples.encode("utf-8")).hexdigest(),
        "graded_samples_sha256": hashlib.sha256(graded.encode("utf-8")).hexdigest(),
        # The file a replay must seed before running the agent's code: the fixture's own
        # content for core.repair, and the post-change content for core.changed_input.
        "graded_samples": graded,
        "plot_count": len(plots),
        "expected_summary": expected,
        "answer_claims": answer_claims,
    }
    gold_root.mkdir(parents=True, exist_ok=True)
    target = gold_root / f"{case}.json"
    target.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fixture": str(directory), "gold": str(target), "expected": expected}


def build(fixtures_root: Path, gold_root: Path) -> dict:
    result = {"core.paths": build_paths(fixtures_root, gold_root)}
    result["core.repair"] = build_repair(
        fixtures_root, gold_root, case="core_repair", samples=SAMPLES,
        prompt=REPAIR_PROMPT,
        answer_claims={"plot_count": 3, "fixed": True},
    )
    result["core.changed_input"] = build_repair(
        fixtures_root, gold_root, case="core_changed_input", samples=SAMPLES,
        prompt=CHANGED_PROMPT,
        # The expected numbers are the post-change ones. A verifier replaying the
        # agent's own code sees the pre-change file on disk, so the replay has to seed
        # the updated value -- otherwise a correct agent is graded against the input
        # the prompt told it to change.
        expected_samples=SAMPLES_UPDATED,
        answer_claims={"rerun": True},
    )
    return result


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
