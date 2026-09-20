"""Legacy in-process two-turn smoke; not part of independent evaluation."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import uuid

from runtime.agent import stream_agent
from runtime.lifecycle import Sessions
from runtime.workspace import WorkspaceRegistry


async def collect(store, registry, messages, chat_id, database):
    return [event async for event in stream_agent(
        store, "acceptance", [], messages,
        workspace_registry=registry,
        max_requests=8,
        agent_run_id="agent_" + uuid.uuid4().hex,
        chat_id=chat_id,
        persistence_database=database,
    )]


async def main(source: str, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    source_anchor = Path(source).anchor
    os.environ.setdefault("UAV_INPUT_HOST_ROOT", source_anchor)
    os.environ.setdefault("UAV_INPUT_ROOT", source_anchor)
    data = output / uuid.uuid4().hex
    sessions = Sessions(data)
    registry = WorkspaceRegistry(data)
    chat_id = str(uuid.uuid4())
    sessions.create("acceptance", chat_id)
    store = sessions.acquire("acceptance", chat_id)
    database = data / "agent_steps.sqlite3"

    first = await collect(
        store, registry,
        [{"role": "user", "content": f"请列出 {source} 下的文件夹。"}],
        chat_id, database,
    )
    second = await collect(
        store, registry,
        [{"role": "user", "content": "检查1605白桦文件夹下的影像是否适合正射拼接，但不要启动任务。"}],
        chat_id, database,
    )
    payload = {"chat_id": chat_id, "first": first, "second": second}
    (data / "events.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    first_lists = [
        event for event in first
        if event.get("type") == "tool_end" and event.get("name") == "fs_list"
    ]
    second_inspections = [
        event for event in second
        if event.get("type") == "tool_end" and event.get("name") == "inspect_uav_source"
    ]
    if not first_lists or not first_lists[-1].get("ok"):
        raise AssertionError("Qwen did not successfully list the requested directory")
    names = {
        item.get("name")
        for item in first_lists[-1]["result"]["data"].get("items", [])
    }
    if "1605白桦" not in names:
        raise AssertionError(f"Observed directory names did not contain 1605白桦: {sorted(names)}")
    if not second_inspections or not second_inspections[-1].get("ok"):
        raise AssertionError("Qwen did not inspect the observed 1605白桦 directory")

    print("PASS: first-turn directory observation persisted")
    print("PASS: second turn resolved and inspected 1605白桦")
    print("events:", data / "events.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--output", type=Path, default=Path("evaluation/work/two-turn-path"))
    args = parser.parse_args()
    os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:11434")
    os.environ.setdefault("HOST_BRIDGE_URL", "http://127.0.0.1:8011")
    os.environ.setdefault("REMOTE_SENSING_PLUGINS_ENABLED", "true")
    asyncio.run(main(args.source, args.output))
