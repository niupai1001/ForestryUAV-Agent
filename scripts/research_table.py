"""把逐项记录汇总成主报告用的结果表与计数。

只读 records/*.json，不改变任何评测结论；用于把"计划/已跑/未跑、成功/部分/失败/阻塞"
如实写进 docs/RESEARCH-20261021-zh.md，避免手工抄写走样。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RECORDS = ROOT / "workspace/research-20261021/records"
SUITE = ROOT / "research/tasks.research-v1.json"

VERDICT_LABEL = {
    "completed": "成功",
    "partial": "部分",
    "failed": "失败",
    "blocked": "阻塞",
}


def load_suite() -> list[dict]:
    data = json.loads(SUITE.read_text(encoding="utf-8"))
    return data["tasks"]


def load_record(task_id: str) -> dict | None:
    path = RECORDS / f"task-{task_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def short(text: str, limit: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main() -> int:
    tasks = load_suite()
    rows, counts = [], {"completed": 0, "partial": 0, "failed": 0, "blocked": 0, "not_run": 0}
    for spec in tasks:
        record = load_record(spec["id"])
        if not record:
            counts["not_run"] += 1
            rows.append(f"| {spec['id']} | {spec['title']} | 未运行 | — | — | — | — |")
            continue
        verdict = record.get("verdict") or "failed"
        counts[verdict] = counts.get(verdict, 0) + 1
        metrics = record.get("metrics") or {}
        failed = []
        for item in record.get("checks", []):
            if item.get("passed"):
                continue
            spec_text = item.get("check") or {}
            name = spec_text.get("type", "check") if isinstance(spec_text, dict) else str(spec_text)
            failed.append(name)
        rows.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            spec["id"], spec["title"], VERDICT_LABEL.get(verdict, verdict),
            metrics.get("requests", "—"), metrics.get("tool_actions", "—"),
            round(metrics.get("wall_seconds") or 0, 1),
            short(", ".join(failed)) or "全部通过"))
    header = ("| 编号 | 任务 | 判定 | 模型请求 | 工具动作 | 墙钟秒 | 未通过项 |\n"
              "|---|---|---|---|---|---|---|")
    total = len(tasks)
    summary = (f"计划 {total} 项，已跑 {total - counts['not_run']} 项，未跑 {counts['not_run']} 项；"
               f"成功 {counts['completed']}、部分 {counts['partial']}、"
               f"失败 {counts['failed']}、阻塞 {counts['blocked']}。")
    print(header + "\n" + "\n".join(rows) + "\n\n" + summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
