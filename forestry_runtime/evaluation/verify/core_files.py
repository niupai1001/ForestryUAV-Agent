"""Recover what an agent actually produced, from recorded evidence only.

A Run's workspace is a virtual directory owned by the Runtime, so a verifier cannot
read the agent's working files after the fact. Everything a verifier can honestly
know therefore lives in the recorded evidence:

* ``code_run`` records the source it executed and the *result the Runtime returned*,
  including the job's own stdout and stderr;
* ``fs_write`` / ``fs_edit`` record the bytes the agent created and the exact
  replacement it applied;
* the collector downloads every artifact the trace references, so a file the agent
  produced is readable directly.

Three rules keep this module honest:

* Reconstruction happens only from recorded evidence. A file the trace never wrote
  is *absent*, never assumed.
* The agent's output is graded where it was actually produced -- in the recorded job
  result and in the delivered artifacts. The scoring host never executes
  model-generated code, because a harness that runs the candidate's program in its
  own process is trusting the thing it is measuring, and the result it observes is
  the harness's run, not the Run under test.
* A number the agent claims in prose is not evidence. Only a recorded computation or
  a delivered file counts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


CODE_TOOLS = {"code_run": "code", "fs_write": "content", "fs_edit": None}


def load_trace(trial: Path) -> dict[str, Any]:
    return json.loads((trial / "trace.json").read_text(encoding="utf-8"))


def load_events(trial: Path) -> list[dict[str, Any]]:
    """Recorded Runtime events, or an empty list when none were collected.

    A missing or unreadable event file is *absence of evidence*, not an error: a
    verifier that raises here would abort the whole record instead of reporting the
    check as unobserved.
    """
    path = trial / "raw" / "events.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _step_id(step: dict[str, Any]) -> str:
    return str(step.get("action_id") or step.get("seq") or "")


def reconstruct_files(trace: dict[str, Any]) -> dict[str, str]:
    """Replay recorded writes and edits into the final text of each workspace file.

    Edits are applied with the same literal-replacement semantics as ``fs_edit``: the
    first exact occurrence is replaced, or every occurrence when the record says so.
    An edit whose ``old`` text is not present is skipped rather than guessed at,
    because inventing content would let a broken script pass as repaired.
    """
    files: dict[str, str] = {}
    for step in trace.get("steps", []):
        if step.get("status") != "success":
            continue
        tool = step.get("tool")
        args = step.get("args_normalized")
        if not isinstance(args, dict):
            continue
        path = str(args.get("path") or "")
        if tool == "fs_write" and path:
            files[path] = str(args.get("content") or "")
        elif tool == "fs_edit" and path:
            current = files.get(path)
            if current is None:
                continue
            old, new = str(args.get("old") or ""), str(args.get("new") or "")
            if not old or old not in current:
                continue
            files[path] = (
                current.replace(old, new)
                if args.get("replace_all") else current.replace(old, new, 1)
            )
    return files


def executed_snippets(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Every Python snippet the agent ran or wrote, newest last.

    ``fs_write`` is included because an agent may repair a script and then run it
    through a second snippet; both are candidates for "the programme that computes
    the result", and grading the wrong one would fail a correct answer.
    """
    items: list[dict[str, Any]] = []
    for step in trace.get("steps", []):
        if step.get("status") != "success":
            continue
        args = step.get("args_normalized")
        if not isinstance(args, dict):
            continue
        tool = step.get("tool")
        if tool == "code_run" and args.get("language") == "python":
            code = str(args.get("code") or "")
            if code.strip():
                items.append({
                    "origin": "code_run", "action_id": _step_id(step),
                    "step": step.get("step"), "path": None, "code": code,
                })
        elif tool == "fs_write":
            path = str(args.get("path") or "")
            code = str(args.get("content") or "")
            if path.endswith(".py") and code.strip():
                items.append({
                    "origin": "fs_write", "action_id": _step_id(step),
                    "step": step.get("step"), "path": path, "code": code,
                })
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        digest = hashlib.sha256(item["code"].encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        item["code_sha256"] = digest[:16]
        unique.append(item)
    return unique


def failed_executions(trace: dict[str, Any], *, error_type: str) -> list[dict[str, Any]]:
    """Successful tool calls whose *result* reports a failure of *error_type*.

    A sandboxed snippet that raises still completes as a tool call, and the failure
    is reported inside the result payload -- so looking only at ``status`` would miss
    every real error the agent observed.

    Only the recorded result, failure and category are searched. Scanning the whole
    step would also match the *source the agent wrote*: a snippet that merely mentions
    ``ZeroDivisionError`` would count as having hit one.
    """
    found: list[dict[str, Any]] = []
    for step in trace.get("steps", []):
        if step.get("tool") != "code_run":
            continue
        haystack = json.dumps({
            "result": step.get("result_excerpt"),
            "failure": step.get("failure"),
            "category": step.get("failure_category"),
        }, ensure_ascii=False, default=str)
        if error_type in haystack:
            found.append({
                "action_id": _step_id(step), "step": step.get("step"),
                "status": step.get("status"),
                "failure": step.get("failure"),
                "failure_category": step.get("failure_category"),
            })
    return found


def run_snippet(
    code: str, *, case_dir: Path, timeout: int = 60,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Refuse to execute model-generated code on the scoring host.

    This function used to run the agent's Python here, with the harness process's own
    privileges, and it was on the scoring path for two capability cases. It is kept
    only as an explicit refusal so any surviving caller fails loudly instead of
    silently reintroducing the hole.

    Scoring evidence must come from what the Run actually produced: the recorded
    ``code_run`` result, the delivered artifacts, and the execution records. A
    recalculated number produced by the harness is not evidence about the agent.
    """
    raise RuntimeError(
        "Scoring must not execute model-generated code on the host. "
        "Grade the recorded execution result, the delivered artifacts and the "
        "execution records instead of replaying the agent's program."
    )


def _jobs_started_by_the_run(trace: dict[str, Any]) -> set[str]:
    """Job ids the Run itself submitted.

    A job observation only describes this Run's work if the Run started that job.
    Without this check an orphaned observation -- a ``job_wait`` whose ``code_run``
    was never recorded -- would be graded as if the Run had produced it.
    """
    started: set[str] = set()
    for step in trace.get("steps", []):
        if step.get("tool") not in {"code_run", "dependency_install"}:
            continue
        if step.get("status") != "success":
            continue
        payload = _as_payload(step.get("result_excerpt"))
        if payload is None:
            continue
        job_id = _extract_job_id(payload)
        if job_id:
            started.add(job_id)
    return started


def recorded_job_outputs(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Every stdout/stderr the Runtime's own job execution recorded, newest last.

    ``code_run`` returns a job reference; the job's output reaches the trace through
    the ``job_wait`` / ``job_status`` result that followed it. Those payloads are
    first-hand evidence: they are what the sandbox actually ran. Observations for
    jobs this Run never started are ignored.
    """
    started = _jobs_started_by_the_run(trace)
    outputs: list[dict[str, Any]] = []
    for step in trace.get("steps", []):
        if step.get("status") != "success":
            continue
        result = step.get("result_excerpt")
        payload = _as_payload(result)
        if payload is None:
            continue
        job_id = _extract_job_id(payload)
        if job_id is None or job_id not in started:
            continue
        text = _extract_output(payload)
        if not text:
            continue
        outputs.append({
            "tool": step.get("tool"),
            "action_id": _step_id(step),
            "step": step.get("step"),
            "job_id": job_id,
            "state": _extract_state(payload),
            "output": text,
            "truncated": bool(payload.get("truncated")),
        })
    return outputs


def _as_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, dict):
            return {**value, **data}
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            decoded = json.loads(value)
        except ValueError:
            return None
        return _as_payload(decoded)
    return None


def _extract_output(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("output", "stdout", "log", "content", "excerpt", "head", "tail"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            chunks.append(value)
    return "\n".join(chunks)


def _extract_job_id(payload: dict[str, Any]) -> str | None:
    for key in ("job_id",):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_state(payload: dict[str, Any]) -> str | None:
    value = payload.get("state")
    return str(value) if isinstance(value, str) else None


def artifact_texts(artifacts: Path, *, limit_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    """Read the delivered artifacts as text, skipping binaries and oversized files."""
    if not artifacts.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(artifacts.glob("asset_*")):
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > limit_bytes:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found.append({"name": path.name, "text": text})
    return found


def observed_summaries(
    job_outputs: list[dict[str, Any]],
    texts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every plottable summary the Run actually produced, with its provenance.

    Prose is deliberately excluded: only recorded job output and delivered files are
    searched, so a summary the agent merely *claims* in its answer cannot pass.
    """
    found: list[dict[str, Any]] = []
    for item in job_outputs:
        for document in _json_documents(item["output"]):
            parsed = plot_summaries(document)
            if parsed:
                found.append({
                    "origin": f"{item['tool']}:{item.get('job_id') or item['action_id']}",
                    "summary": parsed,
                })
    for item in texts:
        for document in _json_documents(item["text"]):
            parsed = plot_summaries(document)
            if parsed:
                found.append({"origin": f"artifact:{item['name']}", "summary": parsed})
    return found


def plot_summaries(payload: Any) -> dict[str, dict[str, float]] | None:
    """Read a ``[{plot_id, mean_height_m, ...}]`` summary, or return None.

    Accepts both the fixture's declared schema and the common variants an agent
    reaches for (a mapping keyed by plot, or a wrapper object), because the case
    contract fixes the *values*, not the transport. Numeric-looking strings are
    accepted: a CSV-quoted number is the same measurement as a JSON number.
    """
    if isinstance(payload, dict):
        for key in ("summary", "plots", "rows", "data", "result"):
            if key in payload:
                return plot_summaries(payload[key])
        if payload and all(isinstance(value, dict) for value in payload.values()):
            payload = [
                {"plot_id": key, **value} for key, value in payload.items()
            ]
    if not isinstance(payload, list):
        return None
    summaries: dict[str, dict[str, float]] = {}
    for row in payload:
        if not isinstance(row, dict):
            return None
        plot = row.get("plot_id") or row.get("plot") or row.get("id")
        if plot is None:
            return None
        record: dict[str, float] = {}
        for field in (
            "mean_height_m", "median_height_m", "tree_records", "tree_count",
            "observed_heights", "missing_heights",
        ):
            if row.get(field) is None:
                continue
            try:
                record[field] = float(row[field])
            except (TypeError, ValueError):
                return None
        summaries[str(plot)] = record
    return summaries or None


def _json_documents(text: str) -> list[Any]:
    documents: list[Any] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("[", index)
        brace = text.find("{", index)
        candidates = [item for item in (start, brace) if item != -1]
        if not candidates:
            break
        start = min(candidates)
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        documents.append(value)
        index = start + end
    return documents


def summaries_match(
    observed: dict[str, dict[str, float]], expected: dict[str, dict[str, float]],
    *, tolerance: float = 1e-6,
) -> tuple[bool, list[str]]:
    """Compare plot summaries field by field and report each difference.

    An expected field whose value is ``None`` means "genuinely absent", so omitting it
    passes while reporting ``0.0`` fails. That is the case's whole point: P-03 has no
    observed height, so its mean is undefined, and answering zero is a different claim.
    """
    problems: list[str] = []
    for plot in sorted(set(expected) | set(observed)):
        if plot not in observed:
            problems.append(f"{plot}: missing")
            continue
        if plot not in expected:
            problems.append(f"{plot}: unexpected")
            continue
        for field, want in expected[plot].items():
            got = observed[plot].get(field)
            if want is None:
                if got is not None:
                    problems.append(f"{plot}.{field}: expected empty, got {got}")
            elif got is None:
                problems.append(f"{plot}.{field}: missing")
            elif abs(got - want) > tolerance:
                problems.append(f"{plot}.{field}: expected {want}, got {got}")
    return not problems, problems


__all__ = [
    "artifact_texts", "executed_snippets", "failed_executions", "load_events",
    "load_trace", "observed_summaries", "plot_summaries", "recorded_job_outputs",
    "reconstruct_files", "run_snippet", "summaries_match",
]
