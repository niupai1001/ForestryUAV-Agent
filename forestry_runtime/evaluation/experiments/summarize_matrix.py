"""Summarize a matrix run (see ``MATRIX.md``): one row per trial, one table per arm.

Reads what the collector already wrote (``record.json`` + ``raw/events.json``);
no runtime calls. The M4 validity gate lives here too: an on-phase trial whose
trace shows no retrieval signal (``retrieval_mode`` never set and no ``delegate``
in the visible tools) invalidates the arm, the same failure mode that silently
inverted the first pilot.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX_ROOT = ROOT / "data" / "runs" / "matrix"

ARMS: dict[str, dict] = {
    "E1": dict(phase="off", context=32768,  model="qwen3.5:4b"),
    "E2": dict(phase="on",  context=32768,  model="qwen3.5:4b"),
    "E3": dict(phase="off", context=131072, model="qwen3.5:4b"),
    "E4": dict(phase="on",  context=131072, model="qwen3.5:4b"),
    "E5": dict(phase="off", context=32768,  model="qwen3.8:27b"),
    "E6": dict(phase="on",  context=32768,  model="qwen3.8:27b"),
    "E7": dict(phase="off", context=131072, model="qwen3.8:27b"),
    "E8": dict(phase="on",  context=131072, model="qwen3.8:27b"),
}


def trial_dirs(arm: str) -> list[Path]:
    base = MATRIX_ROOT / arm
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir()
                  if p.is_dir() and p.name.startswith("grounded-"))


def analyse_trial(trial_dir: Path) -> dict:
    row: dict = {"dir": trial_dir.name}
    events_path = trial_dir / "raw" / "events.json"
    if not events_path.exists():
        row["valid"] = False
        row["reason"] = "no events.json"
        return row
    events = json.loads(events_path.read_text(encoding="utf-8"))
    model_calls = [e for e in events if e.get("type") == "model_call"]
    tool_starts = [e for e in events if e.get("type") == "tool_start"]
    done = [e for e in events if e.get("type") == "done"]
    row["valid"] = True
    row["model_calls"] = len(model_calls)
    row["tool_calls"] = len(tool_starts)
    row["tool_names"] = dict(Counter(str(e.get("name")) for e in tool_starts))
    row["delegate_calls"] = row["tool_names"].get("delegate", 0)

    # M4 signals, per model call
    modes = [ (e.get("context") or {}).get("retrieval_mode") for e in model_calls ]
    row["retrieval_modes"] = sorted({str(m) for m in modes if m is not None})
    row["retrieval_calls"] = sum(
        1 for m in modes if m not in (None, "off", "None", "none"))
    row["retrieval_count_total"] = sum(
        int((e.get("context") or {}).get("retrieval_count") or 0)
        for e in model_calls)
    row["retrieval_dropped_total"] = sum(
        int((e.get("context") or {}).get("retrieval_dropped") or 0)
        for e in model_calls)

    limits = {(e.get("context") or {}).get("context_limit_tokens")
              for e in model_calls}
    row["context_limits"] = sorted(x for x in limits if x is not None)
    tool_counts = {(e.get("context") or {}).get("tool_count") for e in model_calls}
    row["tool_counts"] = sorted(x for x in tool_counts if x is not None)
    delegate_visible = any(
        "delegate" in ((e.get("context") or {}).get("tool_names") or [])
        for e in model_calls)
    row["delegate_in_schema"] = delegate_visible

    inputs = [(e.get("context") or {}).get("estimated_input_tokens") or 0
              for e in model_calls]
    row["input_tokens_first"] = inputs[0] if inputs else None
    row["input_tokens_last"] = inputs[-1] if inputs else None
    row["input_tokens_max"] = max(inputs) if inputs else None
    row["input_tokens_sum"] = sum(inputs)

    # M2: the run's own terminal state lives at the top level of the done
    # event, together with the transport-level usage totals (input tokens are
    # cumulative, incl. what cache absorbed).
    row["run_state"] = done[-1].get("state") if done else "no_done_event"
    usage = (done[-1].get("usage") or {}) if done else {}
    row["requests"] = usage.get("requests")
    row["input_tokens_total"] = usage.get("input_tokens")
    row["cache_read_tokens"] = usage.get("cache_read_tokens")
    row["output_tokens"] = usage.get("output_tokens")
    row["usage_tool_calls"] = usage.get("tool_calls")

    # record file name pattern: {arm}-{task}-r{slot:02d}.record.json, e.g. E1-oam-07-r01
    slot_no = int(trial_dir.name.rsplit("-r", 1)[-1])
    candidates = sorted(MATRIX_ROOT.glob(
        f"{trial_dir.parent.name}-*r{slot_no:02d}.record.json"))
    elapsed = None
    for cand in candidates:
        try:
            record = json.loads(cand.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if record.get("arm") == trial_dir.parent.name:
            elapsed = record.get("elapsed_seconds")
            row["returncode"] = record.get("returncode")
            row["record"] = cand.name
            break
    row["elapsed_seconds"] = elapsed

    # M4 verdict for this trial: on-phase arms must show a signal, off-phase
    # arms must not (that asymmetry is what inverted the first pilot).
    on_expected = ARMS[trial_dir.parent.name]["phase"] == "on"
    got_signal = bool(row["retrieval_modes"]) or row["delegate_in_schema"]
    row["m4_ok"] = got_signal if on_expected else (not got_signal)
    return row


def fmt_row(arm: str, row: dict) -> str:
    if not row.get("valid"):
        return f"| {arm} | {row['dir']} | INVALID ({row.get('reason')}) | | | | | |"
    flags = "M4 ok" if row["m4_ok"] else "M4 FAIL"
    modes = ",".join(row["retrieval_modes"]) or "-"
    tools = row["tool_counts"]
    tools_s = "/".join(str(t) for t in tools) or "-"
    in_tok = row.get("input_tokens_total")
    out_tok = row.get("output_tokens")
    cache = row.get("cache_read_tokens")
    return (f"| {arm} | {row['dir'].replace('grounded-', '')} "
            f"| {row['model_calls']} | {row['tool_calls']} | "
            f"{in_tok if in_tok is not None else '-'} "
            f"(cache {cache if cache is not None else '-'}, out {out_tok if out_tok is not None else '-'}) "
            f"| {modes} | {tools_s} | "
            f"{'/'.join(str(x) for x in row['context_limits']) or '-'} | "
            f"{row.get('elapsed_seconds', '-')} | {row['run_state']} "
            f"| {flags} |")


def main() -> int:
    print("# Matrix pilot summary\n")
    print("Per-trial rows. tool_counts = distinct values of tool_count across "
          "model calls (off schema = 27 tools; on schema adds delegate).\n")
    header = ("| arm | trial | model_calls | tool_calls | tokens (in total / "
              "cache_read / out) | retrieval_mode | tools(schema) | ctx_limit "
              "| elapsed_s | end_state | verdict |")
    print(header)
    print("|" + "---|" * 11)
    totals: dict[str, dict] = {}
    for arm in sorted(ARMS):
        rows = [analyse_trial(d) for d in trial_dirs(arm)]
        for row in rows:
            print(fmt_row(arm, row))
        valid = [r for r in rows if r.get("valid")]
        states = Counter(str(r.get("run_state")) for r in valid)

        def mean(field: str) -> float | None:
            vals = [r.get(field) for r in valid
                    if isinstance(r.get(field), (int, float))]
            return round(sum(vals) / len(vals), 1) if vals else None

        totals[arm] = {
            "trials": len(rows),
            "valid": len(valid),
            "states": dict(states),
            "m4_fail": sum(1 for r in valid if not r.get("m4_ok")),
            "model_calls_avg": mean("model_calls"),
            "tool_calls_avg": mean("tool_calls"),
            "input_tokens_avg": mean("input_tokens_total"),
            "cache_read_avg": mean("cache_read_tokens"),
            "output_tokens_avg": mean("output_tokens"),
            "elapsed_avg": mean("elapsed_seconds"),
            "delegate_calls_total": sum(r["delegate_calls"] for r in valid),
            "retrieval_calls_total": sum(r["retrieval_calls"] for r in valid),
        }
    print("\n# Per-arm means\n")
    print("| arm | phase | trials | end states | M4 fail | model_calls | "
          "tool_calls | in tokens | cache_read | out tokens | elapsed_s "
          "| delegate | retrieval calls |")
    print("|" + "---|" * 13)
    for arm, t in totals.items():
        print(f"| {arm} | {ARMS[arm]['phase']} | {t['trials']} "
              f"| {json.dumps(t['states'], ensure_ascii=False)} | {t['m4_fail']} "
              f"| {t['model_calls_avg']} | {t['tool_calls_avg']} "
              f"| {t['input_tokens_avg']} | {t['cache_read_avg']} "
              f"| {t['output_tokens_avg']} | {t['elapsed_avg']} "
              f"| {t['delegate_calls_total']} | {t['retrieval_calls_total']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
