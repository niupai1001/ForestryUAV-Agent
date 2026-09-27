"""Shared vocabulary for reading resource failures out of a job's own log.

Both processes need this and neither may import the other: the Host Bridge owns
the container and the Runtime turns the observation into a model-visible failure.
A capacity failure is the case that motivated it. ``Errno 28`` was previously
reported as an unexplained ``install_failed``, so "the 512 MiB scratch disk inside
the install container is full" read the same as "the host disk is full" and as
"one requirement is unbuildable" — three different repairs behind one word.

The functions here are pure string/JSON readers. They never start a container and
never infer a cause from the absence of a message: a log that does not say a space
ran out produces ``None``, not a guess.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: A job script prints this marker followed by one JSON object when a resource
#: limit stopped it. The container knows the path and the filesystem statistics;
#: the host can only guess at them.
FAILURE_MARKER = "RUNTIME_JOB_FAILURE:"
#: Printed on the success path: what the container's writable areas actually held.
RESOURCE_MARKER = "RUNTIME_RESOURCE_REPORT:"

_NO_SPACE = re.compile(
    r"\[\s*Errno\s+28\s*\]\s*No space left on device(?::\s*'([^']*)')?",
    re.IGNORECASE,
)
#: pip and setuptools word the same condition without an errno.
_NO_SPACE_PLAIN = re.compile(r"No space left on device(?::\s*'([^']*)')?", re.IGNORECASE)
_QUOTED_PATH = re.compile(r"'((?:/|/tmp|/deps|/scratch)[^']*)'")
_CAPACITY_PATTERNS = (
    (28, "no_space_left_on_device", _NO_SPACE),
    (28, "no_space_left_on_device", _NO_SPACE_PLAIN),
    (122, "disk_quota_exceeded", re.compile(r"Disk quota exceeded", re.IGNORECASE)),
    (12, "out_of_memory", re.compile(r"Cannot allocate memory|MemoryError", re.IGNORECASE)),
    (24, "open_files_exhausted", re.compile(r"Too many open files", re.IGNORECASE)),
)


def format_bytes(value: Any) -> str | None:
    """Render a byte count the way a person reads it, or ``None`` for no value."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if number < 1024 or unit == "TiB":
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return None


def _matching_line(text: str, pattern: re.Pattern) -> tuple[str, str | None] | None:
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            captured = match.group(1) if match.groups() else None
            return line.strip()[:400], captured
    return None


def parse_resource_failure(output: str) -> dict | None:
    """Read a capacity failure out of a job log, with the path that ran out.

    Returns ``None`` when the log does not contain a recognized resource
    condition. The caller must not turn ``None`` into a diagnosis.
    """
    text = str(output or "")
    if not text:
        return None
    for errno, kind, pattern in _CAPACITY_PATTERNS:
        found = _matching_line(text, pattern)
        if found is None:
            continue
        line, quoted = found
        paths: list[str] = []
        if quoted:
            paths.append(quoted)
        for candidate in _QUOTED_PATH.findall(text):
            if candidate not in paths:
                paths.append(candidate)
        return {
            "kind": kind,
            "errno": errno,
            "evidence_line": line,
            "paths": paths[:8],
            "exhausted_path": paths[0] if paths else None,
        }
    return None


def parse_marker(output: str, marker: str) -> dict | None:
    """Read the last ``marker``-prefixed JSON object from a job log."""
    text = str(output or "")
    index = text.rfind(marker)
    if index < 0:
        return None
    tail = text[index + len(marker):].lstrip()
    line = tail.splitlines()[0] if tail else ""
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def filesystem_evidence(report: dict | None) -> list[dict]:
    """Normalize the ``space`` section a container reported into readable rows."""
    rows: list[dict] = []
    for path, entry in (report or {}).items():
        if not isinstance(entry, dict):
            continue
        row = {
            "path": entry.get("path") or path,
            "total_bytes": entry.get("total_bytes"),
            "free_bytes": entry.get("free_bytes"),
            "used_bytes": entry.get("used_bytes"),
        }
        row["total_human"] = format_bytes(row["total_bytes"])
        row["free_human"] = format_bytes(row["free_bytes"])
        if entry.get("error"):
            row["error"] = entry["error"]
        rows.append(row)
    return rows


def describe_resource_failure(failure: dict) -> str:
    """One sentence naming what ran out, where, and with what capacity.

    The exhausted path is usually *inside* a reported mount (``/scratch/tmp/pip-x``
    under ``/scratch``), so the mount is matched by longest prefix rather than by
    equality: an exact-key lookup finds nothing and then reports no capacity at all,
    which is the part the caller needs.
    """
    kind = str(failure.get("kind") or "resource_limit")
    path = failure.get("exhausted_path")
    space = {
        str(row.get("path")): row for row in filesystem_evidence(
            failure.get("container_space") or {}
        )
    }
    owner = None
    if path:
        candidates = [
            mount for mount in space
            if path == mount or path.startswith(mount.rstrip("/") + "/")
        ]
        if candidates:
            owner = max(candidates, key=len)
    if owner:
        row = space[owner]
        return (
            f"{kind} at {path}: the filesystem at {owner} holds "
            f"{row.get('total_human') or 'unknown'} in total and had "
            f"{row.get('free_human') or 'unknown'} free when the job failed."
        )
    if path:
        return f"{kind} at {path}."
    return f"{kind} in the job container."


__all__ = [
    "FAILURE_MARKER",
    "RESOURCE_MARKER",
    "describe_resource_failure",
    "filesystem_evidence",
    "format_bytes",
    "parse_marker",
    "parse_resource_failure",
]
