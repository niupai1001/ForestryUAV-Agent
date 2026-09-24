"""Directory-observation checks for the ``core.paths`` contract.

The case asks for a read-only inventory of a directory whose names are deliberately
awkward: CJK names one character apart, an embedded space, a name that is a prefix of
its sibling's, and an extension no domain tool understands. All three checks compare
against the *frozen* listing rather than against whatever the tool happened to return,
so a tool that silently drops or rewrites a name cannot certify itself.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .base import Verdict
from .core_files import load_events


def _answer_text(trial: Path) -> str:
    return "".join(
        str(event.get("content") or "")
        for event in load_events(trial) if event.get("type") == "message"
    )


def _listed_entries(trace: dict[str, Any]) -> list[dict[str, str]]:
    """Entries a successful ``fs_list`` actually returned, in observation order."""
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for step in trace.get("steps", []):
        if step.get("tool") != "fs_list" or step.get("status") != "success":
            continue
        for item in _entries_from_result(step.get("result_excerpt")):
            if item["name"] not in seen:
                seen.add(item["name"])
                entries.append(item)
    return entries


LIST_KEYS = ("items", "entries", "files", "paths", "children", "names")


def _listing_items(value: Any) -> list[Any] | None:
    """The first listing-shaped list in *value*, looking through wrapper objects.

    ``fs_list`` returns ``{"items": [...]}`` under a ``data`` envelope, so a parser
    that only inspected the top level found nothing on a successful call. That is how
    a real ``core.paths`` run reported ``references: unknown`` while its answer listed
    the directory perfectly.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in LIST_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
        for child in value.values():
            found = _listing_items(child)
            if found is not None:
                return found
    return None


def _entries_from_result(result: Any) -> list[dict[str, str]]:
    """Read ``{name, type}`` pairs from a recorded listing result."""
    found: list[dict[str, str]] = []
    for item in _listing_items(result) or []:
        if isinstance(item, str):
            found.append({"name": item, "type": ""})
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("path")
        if not isinstance(name, str):
            continue
        kind = item.get("type") or item.get("kind") or ""
        found.append({"name": name, "type": str(kind).casefold()})
    return found


def _basename(name: str) -> str:
    return name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def directory_scope_is_read_only(
    *, trace: dict[str, Any], report: Path,
) -> Verdict:
    """Require a read-only inventory: no writes, no execution, no boundary crossing.

    A verifier cannot re-observe the agent's sandbox, so this is the only place the
    read-only requirement is checkable: it is decided from what the Run *did*.

    A mutation is detected from each Action's own declared side effect rather than
    from a list of tool names kept here, so a capability added later cannot slip
    past this check by being absent from the list.
    """
    mutations = [
        {"tool": step.get("tool"), "action_id": step.get("action_id"),
         "side_effect": step.get("side_effect")}
        for step in trace.get("steps", [])
        if step.get("status") == "success"
        and (
            step.get("side_effect") not in (None, "none")
            # code_run declares a durable job while writing nothing itself; running
            # code is still outside a read-only inventory, so it is named explicitly.
            or step.get("tool") == "code_run"
        )
    ]
    escapes: list[str] = []
    for step in trace.get("steps", []):
        args = step.get("args_normalized")
        if isinstance(args, dict):
            path = str(args.get("path") or "")
            if ".." in Path(path.replace("\\", "/")).parts:
                escapes.append(path)
    payload = {
        "mutating_actions": mutations, "path_escapes": escapes,
        "steps": len(trace.get("steps", [])),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not trace.get("steps"):
        return Verdict("unknown", "readonly-scope-v1", [report.name],
                       "The trace records no Action, so read-only behaviour is unobserved.")
    if escapes:
        return Verdict("fail", "readonly-scope-v1", [report.name],
                       f"An Action addressed a path outside the authorized directory: {escapes[0]}")
    if mutations:
        return Verdict("fail", "readonly-scope-v1", [report.name],
                       f"A read-only inventory ran {mutations[0]['tool']}.")
    return Verdict("pass", "readonly-scope-v1", [report.name],
                   "Every recorded Action only observed the directory.")


OBSERVING_TOOLS = ("fs_list", "fs_read")


def _observed_files(trace: dict[str, Any], expected: dict[str, str]) -> list[str]:
    """Expected names a *successful* observing tool actually returned.

    Both listing and reading count: an attachment that was read is as real as one that
    was listed. What does not count is a name that only ever appeared in the prompt --
    the agent's own input is where it learned the names, so treating the prompt as
    evidence would make this check pass for an agent that touched nothing at all.
    """
    observed: list[str] = []
    for step in trace.get("steps", []):
        if step.get("status") != "success" or step.get("tool") not in OBSERVING_TOOLS:
            continue
        blob = json.dumps(step.get("result_excerpt"), ensure_ascii=False, default=str)
        for name in expected:
            if name in blob and name not in observed:
                observed.append(name)
    return observed


def names_match_references(
    *, trace: dict[str, Any], gold: Path, report: Path,
) -> Verdict:
    """Require the frozen listing to have been observed, and observed accurately.

    This is the check that makes the awkward names matter: the fixture's ``1605白桦``
    and ``1605桦`` differ by one character, so a normalized or truncated name shows up
    as both a missing and an unexpected entry.

    A real ``core.paths`` run answered the inventory perfectly while its only
    successful Action listed ``.runtime`` -- the agent already knew the filenames
    because they are named in the request. The answer was right; the *observation* was
    absent. That is a distinct fact and it is graded as a failure here rather than
    being credited from the answer.
    """
    contract = json.loads(gold.read_text(encoding="utf-8"))
    expected = {item["name"]: item["type"] for item in contract["entries"]}
    entries = _listed_entries(trace)
    listed = [_basename(item["name"]) for item in entries]
    observed = _observed_files(trace, expected)
    unobserved = [name for name in expected if name not in observed]
    unexpected = [name for name in listed if name not in expected]
    # A name is not the same fact as its type: reporting a file as a directory answers
    # a different question, so the kind is compared exactly wherever the tool gave one.
    wrong_kind = [
        f"{item['name']}: reported {item['type']}, on disk it is "
        f"{expected[_basename(item['name'])]}"
        for item in entries
        if item["type"] and _basename(item["name"]) in expected
        and item["type"] != expected[_basename(item["name"])]
    ]
    payload = {
        "expected": expected, "listed": listed, "observed_in_results": observed,
        "unobserved": unobserved, "unexpected": unexpected,
        "wrong_kind": wrong_kind, "entry_count_expected": contract["entry_count"],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not listed and not observed:
        return Verdict("unknown", "directory-listing-v1", [report.name],
                       "No successful directory observation was recorded; the "
                       "inventory is unevidenced.")
    if unobserved:
        return Verdict("fail", "directory-listing-v1", [report.name],
                       f"{len(unobserved)} of {len(expected)} names were never returned "
                       f"by a successful tool call (first: {unobserved[0]}).")
    # Only a listing can contradict the frozen names. Reading each file proves it
    # exists but says nothing about what else the directory holds, so a full read is
    # accepted above and a full listing is what is compared here.
    if unexpected or wrong_kind:
        return Verdict("fail", "directory-listing-v1", [report.name],
                       f"The listing returned {len(listed)} names, {len(unexpected)} not "
                       f"on disk.")
    if listed:
        return Verdict("pass", "directory-listing-v1", [report.name],
                       "The observed names equal the frozen listing exactly.")
    return Verdict("pass", "directory-listing-v1", [report.name],
                   f"All {len(expected)} uploaded files were read individually.")


def answer_matches_listing(*, trial: Path, gold: Path, report: Path) -> Verdict:
    """Require the final answer to report the same inventory, with no invented files."""
    contract = json.loads(gold.read_text(encoding="utf-8"))
    expected = {item["name"]: item["type"] for item in contract["entries"]}
    answer = _answer_text(trial)
    observed: dict[str, str] | None = None
    for candidate in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.DOTALL)):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        entries = decoded.get("entries") if isinstance(decoded, dict) else None
        if not isinstance(entries, list):
            continue
        observed = {}
        for item in entries:
            if isinstance(item, dict) and item.get("name") is not None:
                observed[str(item["name"])] = str(item.get("type") or "").casefold()
        break
    problems: list[str] = []
    if observed is None:
        problems.append("no machine-readable entries block was found")
    else:
        for name, kind in expected.items():
            if name not in observed:
                problems.append(f"missing: {name}")
            elif observed[name] not in {kind, kind[:3]}:
                problems.append(f"{name}: reported {observed[name]}, on disk it is {kind}")
        problems.extend(
            f"not on disk: {name}" for name in observed if name not in expected
        )
        counted = None
        try:
            counted = json.loads(
                re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.DOTALL)[-1]
            ).get("entry_count")
        except (json.JSONDecodeError, IndexError, AttributeError):
            counted = None
        if counted is not None and int(counted) != len(expected):
            problems.append(f"entry_count: reported {counted}, on disk there are {len(expected)}")
    payload = {"expected": expected, "observed": observed, "problems": problems}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if observed is None:
        return Verdict("unknown", "inventory-answer-v1", [report.name],
                       "The answer carries no JSON entries block; a blind human review is required.")
    if problems:
        return Verdict("fail", "inventory-answer-v1", [report.name],
                       f"The reported inventory differs from disk: {problems[0]}")
    return Verdict("pass", "inventory-answer-v1", [report.name],
                   "The reported inventory matches the directory exactly.")


__all__ = ["answer_matches_listing", "directory_scope_is_read_only", "names_match_references"]
