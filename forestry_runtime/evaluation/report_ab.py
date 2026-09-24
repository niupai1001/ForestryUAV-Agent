"""Render the A/B comparison as the report the plan asks for.

`compare_arms` produces the numbers; this turns them into a document a reader can
check, with the five dimensions separate, every per-case cell spelled out, and every
claim traceable to a file on disk. It deliberately writes no aggregate score: v1
reports the dimensions separately, because they have different causes and different
fixes, and a single number would hide which one moved.

    python -m evaluation.report_ab --a evaluation/work/arm-a \
        --b evaluation/work/arm-b --output evaluation/AB_REPORT.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.compare_arms import compare  # noqa: E402

VERDICT_ORDER = ("pass", "fail", "unknown", "not_applicable")


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _statuses(counts: dict) -> str:
    return ", ".join(f"{name} {count}" for name, count in sorted(counts.items())) or "—"


def _verdict_cells(counts: dict) -> str:
    return " / ".join(str(counts.get(name, 0)) for name in VERDICT_ORDER)


def render(report: dict, *, root_a: str, root_b: str) -> str:
    lines: list[str] = []
    add = lines.append

    add("# A 组 / B 组 能力对照报告")
    add("")
    add(f"- A 组（通用文件/代码能力）：`{root_a}`")
    add(f"- B 组（相同通用能力 + 林业遥感工具与按需指南）：`{root_b}`")
    add(f"- 配对槽位：{report['slots_paired']} / {report['slots_total']}，"
        f"未配对 {len(report['slots_unpaired'])}")
    add(f"- 冻结配置一致：**{report['configuration_identical']}**"
        + (f"（漂移：{', '.join(report['configuration_drift'])}）"
           if report["configuration_drift"] else ""))
    add(f"- 证据可对照：**{report['comparable']}**")
    add("")
    if not report["comparable"]:
        add("> 结论前置：本报告的配对统计**不可作为对照结论**，原因见下方「污染」一节。")
        add("")
    add("本报告不给出单一总分。五个维度分开汇报，因为它们的成因与修法都不同：")
    add("工程可靠性、任务正确性、判断质量、可复现性、时间/Token 成本。")
    add("")

    add("## 一、工程可靠性")
    add("")
    add("| | 已评分 | 覆盖率 | 状态分布 | 终态分布 | 模型调用 | 工具调用 |")
    add("|---|---|---|---|---|---|---|")
    for arm, label in (("a", "A 通用"), ("b", "B 领域")):
        total = report["totals"][arm]
        add(f"| {label} | {total['graded']}/{report['slots_total']} "
            f"| {_pct(total['graded_rate'])} | {_statuses(total['statuses'])} "
            f"| {_statuses(total['terminals'])} | {total['model_calls']} "
            f"| {total['tool_calls']} |")
    add("")
    add("`crash` 指 Run 被 Runtime 主动暂停（模型调用预算耗尽或重复失败调用被拦截），")
    add("是 Agent 侧结果；`infra_error` 指 Runtime 不可达，不计入能力。")
    add("")

    add("## 二、三、任务正确性与判断质量")
    add("")
    add("每个「案例 × 条件」一行，单元格为 `pass / fail / unknown / not_applicable` 计数。")
    add("")
    add("| 案例 × 条件 | A 组 | B 组 | 配对 | A/B 逐次差异 |")
    add("|---|---|---|---|---|")
    for name, entry in sorted(report["by_case"].items()):
        a, b = entry["a"], entry["b"]
        differences = "; ".join(
            f"r{d['repeat']}: A={d['a']} B={d['b']}" for d in entry["differences"]
        ) or "—"
        add(f"| `{name}` | {_verdict_cells(a)} | {_verdict_cells(b)} "
            f"| {entry['paired']}/{entry['slots']} | {differences} |")
    add("")
    add("逐项判据（check）的分布，用来定位「哪一条判断不同」，而不是只看总数：")
    add("")
    add("| 判据 | A 组 | B 组 |")
    add("|---|---|---|")
    for name, counts in sorted(report["checks"].items()):
        add(f"| `{name}` | {_verdict_cells(counts['a'])} | {_verdict_cells(counts['b'])} |")
    add("")

    add("## 四、可复现性")
    add("")
    add("同条件三次重复是否给出一致结论（`unanimous` 为 True 表示三次相同）。")
    add("")
    add("| 案例 × 条件 | A 组 | B 组 |")
    add("|---|---|---|")
    for name, entry in sorted(report["reproducibility"].items()):
        a, b = entry["a"], entry["b"]
        add(f"| `{name}` | {a['unanimous']} ({a['distinct']} 种 / {a['repeats']} 次) "
            f"| {b['unanimous']} ({b['distinct']} 种 / {b['repeats']} 次) |")
    add("")

    add("## 五、时间与 Token 成本")
    add("")
    add("模型调用与工具调用来自每槽位的 `trace.json` / `raw/events.json`，不是估算。")
    add("")
    add("| | 模型调用合计 | 工具调用合计 | 每次已评分槽位平均模型调用 |")
    add("|---|---|---|---|")
    for arm, label in (("a", "A 通用"), ("b", "B 领域")):
        total = report["totals"][arm]
        graded = max(1, int(total["graded"]))
        add(f"| {label} | {total['model_calls']} | {total['tool_calls']} "
            f"| {total['model_calls'] / graded:.1f} |")
    add("")

    if report["slots_unpaired"]:
        add("## 未配对槽位")
        add("")
        add("这些槽位只有一组产出。它们**不并入任何一组的分母**——把失败的槽位丢掉，")
        add("正是让坏掉的一组显得更好的方式。")
        add("")
        add("| 案例 × 条件 | 重复 | 缺失 |")
        add("|---|---|---|")
        for item in report["slots_unpaired"]:
            add(f"| `{item['case_id']}@{item['condition']}` | {item['repeat']} "
                f"| {', '.join(item['missing'])} |")
        add("")

    if report["contamination"]:
        add("## 污染")
        add("")
        for item in report["contamination"]:
            add(f"- {item}")
        add("")

    add("## 判读规则")
    add("")
    for note in report["notes"]:
        add(f"- {note}")
    add("- 未配对槽位、`infra_error` 与 `unknown` 都不计入通过率分子，但都保留在报告里。")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", type=Path, required=True)
    parser.add_argument("--b", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison", type=Path,
                        help="also write the raw comparison JSON here")
    args = parser.parse_args()

    report = compare(args.a.resolve(), args.b.resolve())
    args.output.write_text(
        render(report, root_a=str(args.a), root_b=str(args.b)), encoding="utf-8"
    )
    if args.comparison:
        args.comparison.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    print(f"wrote {args.output}")
    print(f"comparable={report['comparable']} paired={report['slots_paired']}"
          f"/{report['slots_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
