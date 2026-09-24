"""Frozen tool contract used as the independent reference for `tools_snapshot`.

This file is authored on the evaluation side and is deliberately **not** derived
from ``runtime.kernel.registry`` at freeze time.

The earlier implementation hashed whatever the Runtime's registry reported, which
made the measured system its own reference: changing a tool's contract changed the
"frozen" snapshot in the same edit, so a configuration mismatch could never be
detected. The snapshot is meant to answer "was the agent's tool surface the same as
when the baseline was taken?", and only an independent copy can answer that.

Update this file deliberately, together with a new evidence root and a fresh
baseline. Never regenerate it from the running Runtime to make a run pass.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

TOOL_CONTRACT_PATH = Path(__file__).with_name("tool_contract.json")


def load_tool_contract() -> dict:
    return json.loads(TOOL_CONTRACT_PATH.read_text(encoding="utf-8"))


def expected_tool_names() -> list[str]:
    return sorted(entry["name"] for entry in load_tool_contract()["tools"])


def tools_snapshot() -> str:
    """Hash of the frozen contract, not of the Runtime under test."""
    contract = load_tool_contract()
    payload = json.dumps(
        [
            {
                "name": entry["name"],
                "side_effect": entry["side_effect"],
                "equivalent": entry["equivalent"],
                "returns": entry["returns"],
            }
            for entry in sorted(contract["tools"], key=lambda item: item["name"])
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "TOOL_CONTRACT_PATH", "expected_tool_names", "load_tool_contract",
    "tools_snapshot",
]
