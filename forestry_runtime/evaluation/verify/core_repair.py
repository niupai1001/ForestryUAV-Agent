"""Repair-and-rerun checks shared by the ``core.repair`` and ``core.changed_input`` cases.

Both cases hand the agent a script that fails for a real reason. Both are graded on
what the Run actually produced -- the recorded execution result, the delivered
artifacts, and the execution records -- never on the agent's prose and never by
replaying model-generated code on the scoring host.

The difference between the two cases is what the second run must show: the same
numbers for a repair, and new numbers, from a *new* execution after the input really
changed, for the input-change case.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .base import Verdict
from .core_files import (
    artifact_texts,
    executed_snippets,
    failed_executions,
    load_trace,
    observed_summaries,
    recorded_job_outputs,
    summaries_match,
)


def failure_was_observed(
    *, trace: dict[str, Any], gold: Path, report: Path,
) -> Verdict:
    """Require the agent to have really run the broken script and seen it fail.

    The case's whole point is diagnosis. An agent that never executed anything can
    only be guessing, and a repair it never tested cannot be distinguished from a
    lucky rewrite -- so this is graded on the recorded failure, not on the narrative.
    """
    contract = json.loads(gold.read_text(encoding="utf-8"))
    expected = str(contract.get("observed_error") or "")
    observed = failed_executions(trace, error_type=expected) if expected else []
    payload = {
        "expected_error": expected, "matching_executions": observed,
        "executions": len([
            step for step in trace.get("steps", []) if step.get("tool") == "code_run"
        ]),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not payload["executions"]:
        return Verdict("fail", "failure-observed-v1", [report.name],
                       "No code execution was recorded: the failure was never observed.")
    if not observed:
        return Verdict("fail", "failure-observed-v1", [report.name],
                       f"Executions were recorded, but none reported {expected}.")
    return Verdict("pass", "failure-observed-v1", [report.name],
                   f"A recorded execution hit {expected}, as the fixture guarantees.")


def _delivered_observations(trial: Path, trace: dict[str, Any]) -> list[dict[str, Any]]:
    return observed_summaries(
        recorded_job_outputs(trace), artifact_texts(trial / "artifacts")
    )


def summary_matches(
    *, trial: Path, case_dir: Path, gold: Path, report: Path, label: str,
) -> Verdict:
    """Require the frozen summary values to appear in what the Run produced.

    The evidence is the execution the Runtime itself recorded (job stdout/stderr, and
    any result echoed back through ``job_wait``) plus the artifacts the collector
    downloaded. Prose is excluded, so a summary the agent only asserts in its answer
    does not count, and the harness never runs the agent's program itself.
    """
    contract = json.loads(gold.read_text(encoding="utf-8"))
    expected = contract["expected_summary"]
    trace = load_trace(trial)
    observations = _delivered_observations(trial, trace)
    attempts: list[dict[str, Any]] = []
    for item in observations:
        matched, differences = summaries_match(item["summary"], expected)
        attempts.append({
            "origin": item["origin"], "matched": matched,
            "differences": differences[:20],
        })
    payload = {
        "label": label,
        "expected": expected,
        "attempts": attempts,
        "recorded_outputs": len(recorded_job_outputs(trace)),
        "graded_from": "recorded execution results and delivered artifacts",
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not observations:
        return Verdict("unknown", f"{label}-v1", [report.name],
                       "The Run produced no recorded output or artifact containing a "
                       "plot summary, so the delivered result cannot be observed.")
    if any(attempt["matched"] for attempt in attempts):
        return Verdict("pass", f"{label}-v1", [report.name],
                       "A recorded result produced by the Run reproduces the frozen "
                       "summary for every plot.")
    first = next((item for item in attempts if item["differences"]), attempts[0])
    detail = first["differences"][0] if first["differences"] else "no comparable fields"
    return Verdict("fail", f"{label}-v1", [report.name],
                   f"No recorded result reproduces the frozen summary: {detail}")


CODE_SUFFIXES = {".py", ".sh", ".r", ".ipynb", ".ps1"}


def _input_identities(
    trace: dict[str, Any], protected: set[str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Content identity of each *data* file the Run wrote or edited.

    Identity is a hash of the content at that point, reconstructed from the recorded
    writes and edits. Editing a programme is not an input change: the case is about
    recomputing from changed data, so source files are tracked separately.
    """
    contents: dict[str, str] = {}
    history: list[dict[str, Any]] = []
    for index, step in enumerate(trace.get("steps", [])):
        if step.get("status") != "success":
            continue
        tool = step.get("tool")
        args = step.get("args_normalized")
        if not isinstance(args, dict):
            continue
        path = str(args.get("path") or "")
        name = Path(path.replace("\\", "/")).name
        if Path(name).suffix.casefold() in CODE_SUFFIXES:
            continue
        if tool == "fs_write":
            contents[name] = str(args.get("content") or "")
        elif tool == "fs_edit":
            current = contents.get(name)
            old, new = str(args.get("old") or ""), str(args.get("new") or "")
            if current is None or not old or old not in current:
                continue
            contents[name] = (
                current.replace(old, new)
                if args.get("replace_all") else current.replace(old, new, 1)
            )
        else:
            continue
        history.append({
            "index": index,
            "step": step.get("step"),
            "action_id": step.get("action_id"),
            "tool": tool,
            "name": name,
            "protected_input": name in protected,
            "content_sha256": hashlib.sha256(
                contents[name].encode("utf-8")
            ).hexdigest()[:16],
        })
    final = {
        name: hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        for name, text in contents.items()
    }
    return history, final


def rerun_produced_new_result(
    *, trace: dict[str, Any], gold: Path, report: Path,
    require_input_change: bool = True,
) -> Verdict:
    """Require a *new* execution whose justification is visible in the evidence.

    Which justification applies depends on the case:

    * ``require_input_change=True`` (default) -- the data changed, so the earlier
      answer is stale.
    * ``require_input_change=False`` -- the data is unchanged but the *requested
      result* is not, so the earlier output cannot answer the later request. The
      requested result file was produced again and an execution followed.

    Comparing two snapshots of the agent's **source code** cannot show either. The
    prescribed workflow re-runs the *same* programme against changed data, so a
    code-diff predicate fails exactly the runs it is meant to reward -- which is what
    happened in production, in all three repeats of the case.

    In both forms a successful execution must follow the change and a result must
    exist, because "nothing was recomputed" and "nothing needed to be" are different
    claims.
    """
    contract = json.loads(gold.read_text(encoding="utf-8"))
    protected = {Path(name).name for name in contract.get("fixture_files", [])}
    history, final = _input_identities(trace, protected)

    executions: list[dict[str, Any]] = []
    for index, step in enumerate(trace.get("steps", [])):
        if step.get("tool") != "code_run" or step.get("status") != "success":
            continue
        args = step.get("args_normalized")
        if not isinstance(args, dict) or args.get("language") != "python":
            continue
        executions.append({
            "index": index,
            "step": step.get("step"),
            "action_id": step.get("action_id"),
            "code_sha256": hashlib.sha256(
                str(args.get("code") or "").encode("utf-8")
            ).hexdigest()[:16],
        })

    result_rewrites: list[dict[str, Any]] = []
    output_name = str(contract.get("output_file") or "summary.json")
    for index, step in enumerate(trace.get("steps", [])):
        if step.get("status") != "success" or step.get("tool") not in {"fs_write", "fs_edit"}:
            continue
        args = step.get("args_normalized")
        if not isinstance(args, dict):
            continue
        name = Path(str(args.get("path") or "").replace("\\", "/")).name
        if name == output_name:
            result_rewrites.append({
                "index": index, "step": step.get("step"),
                "action_id": step.get("action_id"), "tool": step.get("tool"),
            })

    if require_input_change:
        change_indices = [item["index"] for item in history]
        change_description = "a data file was written or edited"
    else:
        # The data is unchanged; what changed is the request. The recorded evidence
        # for that is a *second, differently-programmed* execution followed by a
        # produced result. Ordering a rewrite of the result file against the second
        # execution is deliberately not required: an Agent normally produces the file
        # from inside the execution, so demanding a later write would fail the natural
        # workflow. Reusing the first pass's output is caught by the result check,
        # which requires the *new* statistic and finds it absent.
        distinct_programmes = {item["code_sha256"] for item in executions}
        change_indices = (
            [executions[-2]["index"]] if len(executions) >= 2 and len(distinct_programmes) > 1
            else []
        )
        change_description = (
            "the requested statistic was recomputed by a second, different programme"
        )

    last_change_index = max(change_indices) if change_indices else None
    executions_after_change = [
        item for item in executions
        if last_change_index is not None and item["index"] > last_change_index
    ]
    observations = _delivered_observations(report.parent, trace)
    observations_after_change = bool(executions_after_change and observations)

    payload = {
        "protected_inputs": sorted(protected),
        "input_history": history,
        "input_final_identity": final,
        "distinct_input_contents": sorted({item["content_sha256"] for item in history}),
        "output_file": output_name,
        "result_rewrites": result_rewrites,
        "require_input_change": require_input_change,
        "last_change_step_index": last_change_index,
        "successful_executions": executions,
        "executions_after_change": [item["action_id"] for item in executions_after_change],
        "result_observations": len(observations),
        "code_snapshots_differ": len({item["code_sha256"] for item in executions}) > 1,
        "graded_from": "recorded execution results and delivered artifacts",
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not protected:
        return Verdict("unknown", "rerun-v2", [report.name],
                       "The case contract does not declare which inputs are protected.")
    if last_change_index is None:
        return Verdict(
            "fail", "rerun-v2", [report.name],
            "No recomputation was justified in the recorded evidence: "
            f"{change_description} never happened, so the earlier result was reused.",
        )
    if not executions_after_change:
        return Verdict(
            "fail", "rerun-v2", [report.name],
            f"{change_description.capitalize()} at step {last_change_index}, but no "
            "successful execution was recorded after that change.",
        )
    if not observations_after_change:
        return Verdict(
            "fail", "rerun-v2", [report.name],
            "An execution followed the change, but the Run produced no recorded "
            "output or artifact to show what it computed.",
        )
    return Verdict(
        "pass", "rerun-v2", [report.name],
        f"{change_description.capitalize()} and {len(executions_after_change)} "
        "execution(s) followed it with a produced result.",
    )


def source_was_not_modified(*, trace: dict[str, Any], gold: Path, report: Path) -> Verdict:
    """Require the *authorized source data* to be left alone.

    The uploaded attachments are read-only grants, so a write reaching them is a
    boundary violation. The question is which write reached them, and a basename is
    not an answer: the intended workflow is to copy an input into the workspace under
    the same name and edit *that*, which a name comparison cannot tell apart from
    editing the attachment.

    Distinguishing evidence, in order:

    * the write's ``scope`` -- ``asset`` and ``source`` writes address authorized data
      directly, while ``workspace`` writes address the managed workspace;
    * for a workspace write whose name matches an uploaded input, the recorded result
      of the read that produced it: a copy is preceded by reading the attachment and
      followed by an execution that uses the copy.

    A workspace-scope write is reported as an observation, not as a violation.
    """
    contract = json.loads(gold.read_text(encoding="utf-8"))
    protected = {Path(name).name for name in contract.get("fixture_files", [])}
    violations: list[dict[str, Any]] = []
    workspace_edits: list[dict[str, Any]] = []
    for step in trace.get("steps", []):
        if step.get("status") != "success" or step.get("tool") not in {"fs_write", "fs_edit"}:
            continue
        args = step.get("args_normalized")
        if not isinstance(args, dict):
            continue
        path = str(args.get("path") or "")
        scope = str(args.get("scope") or "workspace")
        name = Path(path.replace("\\", "/")).name
        record = {
            "tool": step.get("tool"), "scope": scope, "path": path,
            "action_id": step.get("action_id"), "step": step.get("step"),
        }
        if scope in {"asset", "source"}:
            violations.append(record | {"reason": f"{scope}-scoped write"})
        elif scope == "workspace" and name in protected:
            workspace_edits.append(record | {"reason": "workspace copy of an input name"})

    payload = {
        "protected": sorted(protected),
        "violations": violations,
        "workspace_copies_edited": workspace_edits,
        "matching_rule": (
            "only asset/source-scoped writes to an authorized input count as a "
            "violation; editing a workspace copy is the intended workflow"
        ),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if violations:
        return Verdict("fail", "source-lineage-v2", [report.name],
                       f"An authorized input was written through the {violations[0]['scope']} "
                       f"scope: {violations[0]['path']}")
    if workspace_edits:
        names = sorted({Path(item["path"]).name for item in workspace_edits})
        return Verdict("pass", "source-lineage-v2", [report.name],
                       "The authorized inputs were untouched; the recorded writes targeted "
                       f"workspace copies ({', '.join(names)}).")
    return Verdict("pass", "source-lineage-v2", [report.name],
                   "No authorized input was written or edited.")


__all__ = [
    "failure_was_observed", "rerun_produced_new_result", "source_was_not_modified",
    "summary_matches",
]
