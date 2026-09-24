"""Paired A/B comparison over two evidence roots.

The comparison answers five separate questions rather than collapsing them into one
score, because they have different causes and different fixes:

* **engineering reliability** -- did the Runtime deliver a graded run at all, or did
  slots end as crashes, timeouts or infrastructure errors?
* **task correctness** -- did the Run produce what the task required, judged by the
  case's independent checks?
* **judgement quality** -- which specific checks were satisfied, so a family's
  weakness is attributable to a judgement rather than to a total;
* **reproducibility** -- did the three repeats agree, so a difference between arms is
  not just sampling noise;
* **cost** -- model calls and tool calls, so a correctness gain that costs an order of
  magnitude more is visible as such.

Slots are paired by ``(case_id, condition, repeat)``. A pair where either side is
missing is reported as unpaired rather than dropped, because silently discarding the
slots an arm failed to produce is exactly how a broken arm looks better than it is.

    python -m evaluation.compare_arms --a evaluation/work/arm-a --b evaluation/work/arm-b
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.ab_experiment import ARMS  # noqa: E402

DIMENSIONS = ("engineering", "correctness", "judgement", "reproducibility", "cost")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _trace_of(trial: Path) -> dict:
    return _read_json(trial / "trace.json") or {}


def _events_of(trial: Path) -> list[dict]:
    payload = _read_json(trial / "raw" / "events.json")
    return payload if isinstance(payload, list) else []


def _token_usage(trace: dict, events: list[dict]) -> dict:
    usage = dict(trace.get("token_usage") or {})
    if not usage:
        for event in events:
            if event.get("type") == "usage" and isinstance(event.get("usage"), dict):
                usage.update(event["usage"])
    # One-shot continuation reviews run outside the main PydanticAI request limit.
    # Their provider usage is an additional cost, not part of the main trace total.
    for event in events:
        if event.get("type") != "continuation_review" or not isinstance(event.get("usage"), dict):
            continue
        for key in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"):
            value = event["usage"].get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[key] = (usage.get(key) or 0) + value
    return usage


def _model_calls(trace: dict, events: list[dict]) -> int:
    """Count main steps and one-shot review requests without double-counting steps."""
    numbers = {
        event.get("number") for event in events
        if event.get("type") == "model_call" and event.get("number") is not None
    }
    if numbers:
        reviews = {
            event.get("action_id") for event in events
            if event.get("type") == "review_model_call" and event.get("action_id")
        }
        return len(numbers) + len(reviews)
    return sum(1 for step in trace.get("steps", []) if step.get("tool") == "code_run")


def _slot_key(record: dict) -> tuple[str, str, int]:
    """A slot is a case, an input condition and a repeat."""
    return (
        str(record.get("case_id")),
        str(record.get("condition") or "normal"),
        int(record.get("repeat") or 0),
    )


def load_arm(root: Path) -> dict[tuple[str, str, int], dict]:
    """Every graded slot in one evidence root, keyed by slot."""
    slots: dict[tuple[str, str, int], dict] = {}
    for record_path in sorted(root.glob("*/record.json")):
        payload = _read_json(record_path)
        if not isinstance(payload, dict) or not payload.get("case_id"):
            continue
        trial = record_path.parent
        trace = _trace_of(trial)
        events = _events_of(trial)
        collection = _read_json(trial / "collection.json") or {}
        slots[_slot_key(payload)] = {
            "record": payload,
            "trial": trial,
            "trace": trace,
            "events": events,
            "model_calls": _model_calls(trace, events),
            "tool_calls": len(trace.get("steps", [])),
            "token_usage": _token_usage(trace, events),
            "terminal_state": trace.get("terminal_state"),
            "runtime_environment": collection.get("runtime_environment") or {},
        }
    return slots


def _arm_environments(slots: dict) -> dict[str, int]:
    """How many slots recorded each runtime environment, as evidence of the arm.

    The directory name is an operator's claim; the switches recorded at collection
    time are what the Runtime was actually started with. A root whose slots report
    mixed environments is neither arm, and the comparison says so instead of
    producing a number.
    """
    counts: Counter[str] = Counter()
    for slot in slots.values():
        environment = slot.get("runtime_environment") or {}
        if not environment:
            counts["unrecorded"] += 1
            continue
        marker = ",".join(
            f"{key}={environment.get(key, '')}" for key in sorted(environment)
        )
        counts[marker] += 1
    return dict(counts)


def _verdicts(record: dict) -> dict[str, str]:
    return {
        str(name): str(check.get("verdict"))
        for name, check in (record.get("checks") or {}).items()
    }


def _slot_verdict(record: dict) -> str:
    """The slot's own verdict, derived the way the scorecard derives it."""
    if record.get("status") in {"timeout", "crash"}:
        return "fail"
    verdicts = set(_verdicts(record).values())
    if "fail" in verdicts:
        return "fail"
    if record.get("status") != "evaluated":
        return "unknown"
    if verdicts <= {"pass", "not_applicable"}:
        return "pass"
    return "unknown"


def _configuration(root: Path) -> dict:
    return _read_json(root / "configuration-agent.json") or {}


def _image_identity(snapshot: object) -> str:
    """The part of an ``environment_snapshot`` the two arms must agree on.

    The snapshot is ``<platform> Python-<version> image-<reference>``. The reference is
    either a tag or a build digest, and the two arms are inevitably collected by two
    separate builds, so the references differ for the same repository image. What the
    two arms must share is the *platform and interpreter* that ran the code, plus the
    fact that a container image was used at all.

    Which exact build produced each copy is a build-reproducibility question, not a
    difference between the treatments: it is recorded verbatim in each root's
    ``configuration-agent.json`` for a reader who wants it, and the two arms are
    nonetheless required to agree on ``code_snapshot`` and ``tools_snapshot`` -- which
    is the claim that they ran the same code. Comparing image references here would
    make a routine rebuild between arms look like a treatment difference and block a
    comparison that is in fact controlled.
    """
    text = str(snapshot or "")
    head, _, tail = text.partition(" image-")
    return f"{head}|{'image' if tail else 'no-image'}"


def compare(root_a: Path, root_b: Path) -> dict:
    slots_a = load_arm(root_a)
    slots_b = load_arm(root_b)
    keys = sorted(set(slots_a) | set(slots_b))
    pairs: list[dict] = []
    for key in keys:
        left = slots_a.get(key)
        right = slots_b.get(key)
        pairs.append({
            "case_id": key[0], "condition": key[1], "repeat": key[2],
            "a": left, "b": right,
            "a_verdict": _slot_verdict(left["record"]) if left else None,
            "b_verdict": _slot_verdict(right["record"]) if right else None,
        })

    by_case: dict[str, dict] = {}
    for case_id in sorted({key[0] for key in keys}):
        case_pairs = [item for item in pairs if item["case_id"] == case_id]
        for condition in sorted({item["condition"] for item in case_pairs}):
            scoped = [item for item in case_pairs if item["condition"] == condition]
            entry: dict[str, Any] = {
                "case_id": case_id, "condition": condition,
                "slots": len(scoped),
                "paired": sum(1 for item in scoped if item["a"] and item["b"]),
            }
            for arm in ("a", "b"):
                verdicts = [item[f"{arm}_verdict"] for item in scoped if item[arm]]
                counts = Counter(verdicts)
                entry[arm] = {
                    "graded": len(verdicts),
                    "pass": counts["pass"], "fail": counts["fail"],
                    "unknown": counts["unknown"],
                    "statuses": dict(Counter(
                        str(item[arm]["record"].get("status"))
                        for item in scoped if item[arm]
                    )),
                    "terminals": dict(Counter(
                        str(item[arm].get("terminal_state"))
                        for item in scoped if item[arm]
                    )),
                    "model_calls": sum(item[arm]["model_calls"] for item in scoped if item[arm]),
                    "tool_calls": sum(item[arm]["tool_calls"] for item in scoped if item[arm]),
                }
            changed = [
                item for item in scoped
                if item["a"] and item["b"] and item["a_verdict"] != item["b_verdict"]
            ]
            entry["differences"] = [
                {
                    "repeat": item["repeat"], "a": item["a_verdict"],
                    "b": item["b_verdict"],
                    "a_reasons": item["a"]["record"].get("status"),
                    "b_reasons": item["b"]["record"].get("status"),
                }
                for item in changed
            ]
            by_case[f"{case_id}@{condition}"] = entry

    def totals(arm: str) -> dict:
        items = [item for item in pairs if item[arm]]
        verdicts = [item[f"{arm}_verdict"] for item in items]
        counts = Counter(verdicts)
        return {
            "graded": len(items),
            "pass": counts["pass"], "fail": counts["fail"], "unknown": counts["unknown"],
            "pass_rate": (counts["pass"] / len(items)) if items else None,
            "graded_rate": (len(items) / len(pairs)) if pairs else None,
            "statuses": dict(Counter(str(item[arm]["record"].get("status")) for item in items)),
            "terminals": dict(Counter(str(item[arm].get("terminal_state")) for item in items)),
            "model_calls": sum(item[arm]["model_calls"] for item in items),
            "tool_calls": sum(item[arm]["tool_calls"] for item in items),
        }

    # Judgement quality: per check, how often each arm satisfied it.
    checks: dict[str, dict[str, Counter]] = defaultdict(lambda: {"a": Counter(), "b": Counter()})
    for item in pairs:
        for arm in ("a", "b"):
            if not item[arm]:
                continue
            for name, verdict in _verdicts(item[arm]["record"]).items():
                checks[f"{item['case_id']}@{item['condition']}:{name}"][arm][verdict] += 1

    # Reproducibility: agreement across the repeats of one condition.
    reproducibility = {}
    for name, entry in by_case.items():
        record: dict[str, Any] = {}
        for arm in ("a", "b"):
            scoped = [
                item[f"{arm}_verdict"] for item in pairs
                if item["case_id"] == entry["case_id"]
                and item["condition"] == entry["condition"] and item[arm]
            ]
            record[arm] = {
                "repeats": len(scoped),
                "distinct": len(set(scoped)),
                "unanimous": len(set(scoped)) == 1 if scoped else None,
            }
        reproducibility[name] = record

    configuration_a = _configuration(root_a)
    configuration_b = _configuration(root_b)
    drift = [
        field for field in ("model_digest", "prompt_snapshot", "tools_snapshot",
                            "sampling", "budgets", "environment_snapshot")
        if (
            _image_identity(configuration_a.get(field))
            != _image_identity(configuration_b.get(field))
        )
    ]
    environments_a = _arm_environments(slots_a)
    environments_b = _arm_environments(slots_b)
    contamination: list[str] = []
    for label, environments, expected in (
        ("A 组（通用，领域层关闭）", environments_a, ARMS["a"]["env"]),
        ("B 组（领域，领域层开启）", environments_b, ARMS["b"]["env"]),
    ):
        for marker, count in environments.items():
            if marker == "unrecorded":
                contamination.append(
                    f"{label}: {count} 个槽位未记录运行环境，无法确认属于哪一组；"
                    "这些证据需重新采集"
                )
                continue
            declared = dict(
                item.split("=", 1) for item in marker.split(",") if "=" in item
            )
            if declared != expected:
                contamination.append(
                    f"{label}: {count} 个槽位记录的环境为 {marker}，与预期 "
                    f"{expected} 不符"
                )
    return {
        "arms": {"a": str(root_a), "b": str(root_b)},
        "slots_total": len(pairs),
        "slots_paired": sum(1 for item in pairs if item["a"] and item["b"]),
        "slots_unpaired": [
            {"case_id": item["case_id"], "condition": item["condition"],
             "repeat": item["repeat"],
             "missing": [arm for arm in ("a", "b") if not item[arm]]}
            for item in pairs if not (item["a"] and item["b"])
        ],
        "configuration_drift": drift,
        "configuration_identical": not drift,
        "arm_environments": {"a": environments_a, "b": environments_b},
        "contamination": contamination,
        "comparable": not drift and not contamination,
        "totals": {"a": totals("a"), "b": totals("b")},
        "by_case": by_case,
        "checks": {
            name: {
                "a": dict(counts["a"]), "b": dict(counts["b"]),
            }
            for name, counts in sorted(checks.items())
        },
        "reproducibility": reproducibility,
        "notes": [
            "A 组与 B 组仅领域层不同；configuration_drift 或 contamination 非空说明对照无效。",
            "各维度分开报告：工程可靠性、任务正确性、判断质量、可复现性、成本。",
            "未配对槽位单独列出，不并入任一组的分母。",
        ],
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def format_text(report: dict) -> str:
    lines = [
        "=== A/B 对照报告 ===",
        f"槽位：{report['slots_paired']}/{report['slots_total']} 已配对，"
        f"{len(report['slots_unpaired'])} 未配对",
        f"配置一致：{report['configuration_identical']}"
        + (f"（漂移：{', '.join(report['configuration_drift'])}）"
           if report["configuration_drift"] else ""),
        f"证据可对照：{report['comparable']}",
    ]
    for arm in ("a", "b"):
        lines.append(f"  记录到的运行环境（{arm.upper()} 根）：{report['arm_environments'][arm]}")
    if report["contamination"]:
        lines.append("")
        lines.append("！证据污染，不能作为对照结果：")
        for item in report["contamination"]:
            lines.append(f"  - {item}")
    lines.append("")
    lines.append("维度一：工程可靠性（是否有可评分的运行）")
    for arm, label in (("a", "A 通用"), ("b", "B 领域")):
        total = report["totals"][arm]
        lines.append(
            f"  {label}: 已评分 {total['graded']}，覆盖 {_percent(total['graded_rate'])}；"
            f"状态 {total['statuses']}；终态 {total['terminals']}"
        )
    lines.append("")
    lines.append("维度二/三：任务正确性与判断质量（按案例×条件）")
    for name, entry in sorted(report["by_case"].items()):
        a, b = entry["a"], entry["b"]
        lines.append(
            f"  {name}: A {a['pass']}/{a['graded']} pass，B {b['pass']}/{b['graded']} pass"
            f"（配对 {entry['paired']}/{entry['slots']}）"
        )
        for difference in entry["differences"]:
            lines.append(
                f"      repeat {difference['repeat']}: A={difference['a']} B={difference['b']}"
            )
    lines.append("")
    lines.append("维度四：可复现性（同条件三次重复是否一致）")
    for name, entry in sorted(report["reproducibility"].items()):
        lines.append(
            f"  {name}: A 一致={entry['a']['unanimous']} "
            f"({entry['a']['distinct']} 种结果/{entry['a']['repeats']} 次)，"
            f"B 一致={entry['b']['unanimous']} "
            f"({entry['b']['distinct']} 种结果/{entry['b']['repeats']} 次)"
        )
    lines.append("")
    lines.append("维度五：成本")
    for arm, label in (("a", "A 通用"), ("b", "B 领域")):
        total = report["totals"][arm]
        lines.append(
            f"  {label}: 模型调用 {total['model_calls']}，工具调用 {total['tool_calls']}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", type=Path, required=True)
    parser.add_argument("--b", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = compare(args.a.resolve(), args.b.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str)
          if args.json else format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
