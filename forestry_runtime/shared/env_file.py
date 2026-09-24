"""Read the deployment's ``.env`` into a process that Docker Compose does not launch.

The Runtime container receives its credentials through ``env_file: .env``. A process on
the host gets nothing: ``docker compose`` reads that file, the host does not. So the
evaluation runner needed ``RUNTIME_API_KEY`` in its own environment, never found it,
and every agent collection died with a bare ``RuntimeError`` -- while the agent itself,
running inside the container, was perfectly healthy.

Two rules keep this from becoming a second configuration system:

* The file is the single source. Nothing is defaulted or generated here, and a value
  already present in the environment wins, so an explicit override still works.
* Parsing is deliberately small -- ``KEY=VALUE``, ``#`` comments, optional surrounding
  quotes -- and an unreadable line is skipped rather than guessed at.

It sits in ``shared/`` for the same reason as :mod:`shared.paths`: the Runtime is
containerised, the host-side tooling is not, and this package imports nothing.
"""

from __future__ import annotations

import os
from pathlib import Path


def parse_env(text: str) -> dict[str, str]:
    """Parse the subset of dotenv syntax that deployment files actually use."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path, *, override: bool = False) -> dict[str, str]:
    """Load *path* into ``os.environ`` and return every value it declared.

    A var already set in the environment is kept unless *override* is true: the
    process environment is the more specific statement of intent.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    declared = parse_env(text)
    for key, value in declared.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return declared


__all__ = ["load_env_file", "parse_env"]
