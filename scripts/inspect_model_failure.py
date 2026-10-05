"""Print protocol validation errors; never print thinking parts or unrestricted traces."""
import argparse
import asyncio

from pydantic_ai.messages import RetryPromptPart, ToolCallPart
from pydantic_ai_harness.step_persistence import SqliteStepStore

from forestry_agent.config import load_config


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("turn_id")
    args = parser.parse_args()
    config = load_config()
    store = SqliteStepStore(database=config.workspace_root / ".forestry" / "model-steps.sqlite")
    snapshots = await store.list_snapshots(run_id=args.turn_id, include_interrupted=True)
    if not snapshots:
        print("No snapshot")
        return
    snapshot = max(snapshots, key=lambda s: len(s.messages))
    for message in snapshot.messages[-10:]:
        for part in message.parts:
            if isinstance(part, RetryPromptPart):
                print("VALIDATION", str(part.content)[:2000])
            elif isinstance(part, ToolCallPart) and part.tool_name == "final_result":
                print("OUTPUT", str(part.args)[:1600])


if __name__ == "__main__":
    asyncio.run(main())
