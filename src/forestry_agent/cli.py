from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

from .config import load_config
from .doctor import doctor


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="forestry-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor")
    check.add_argument("--config", default="config/app.example.yaml")
    check.add_argument("--output", default="workspace/deployment.lock.json")
    serve = commands.add_parser("serve")
    serve.add_argument("--config", default="config/app.example.yaml")
    run = commands.add_parser("run")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--objective")
    source.add_argument("--spec", type=Path)
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--model", choices=["small", "large"])
    run.add_argument("--harness", choices=["H0", "H1", "H2"])
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--config", default="config/app.example.yaml")
    inspect = commands.add_parser("tasks")
    inspect.add_argument("operation", choices=["inspect", "candidate", "accept", "request_changes"])
    inspect.add_argument("--task", required=True)
    inspect.add_argument("--config", default="config/app.example.yaml")
    inspect.add_argument("--comment", default="")
    schema = commands.add_parser("schema")
    schema.add_argument("--output", default="schemas")
    args = parser.parse_args()
    if args.command == "schema":
        from pydantic_ai_harness.planning import PlanItem

        from . import contracts
        from .roles import AssignmentRequest, AssignmentResult, RoleCatalog
        target = Path(args.output)
        target.mkdir(parents=True, exist_ok=True)
        for name in ["TaskCreate", "Message", "Amendment", "Control", "RegisterResource", "SettingsChange", "Acceptance", "MemorySave", "MemoryForget", "ApprovalResolution"]:
            model = getattr(contracts, name)
            (target / f"{name}.schema.json").write_text(
                json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
        for model in [RoleCatalog, AssignmentRequest, AssignmentResult, PlanItem]:
            (target / f"{model.__name__}.schema.json").write_text(
                json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
        return
    config = load_config(args.config)
    if args.command == "run" or args.command == "tasks" and args.operation != "inspect":
        from .client import LocalClient
        from .contracts import Failure
        client = None
        try:
            client = LocalClient(config)
            if args.command == "run":
                if args.timeout < 1:
                    parser.error("--timeout must be positive")
                payload = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec else {
                    "request_id": str(uuid4()), "objective": args.objective, "model_profile": args.model}
                if args.model:
                    payload["model_profile"] = args.model
                if args.harness:
                    payload["harness_mode"] = args.harness
                result = client.run(payload, args.input, args.timeout)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                raise SystemExit(3 if result["wait_timed_out"] else
                    0 if result["state"]["status"] in {"waiting_user", "completed"} else 2)
            result = client.request("GET", f"/tasks/{args.task}/acceptance-candidate") if args.operation == "candidate" else client.acceptance(args.task, args.operation, args.comment)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        except (Failure, OSError, ValueError, httpx.HTTPError) as error:
            print(json.dumps({"error": {"code": getattr(error, "code", "CLI_ERROR"), "message": str(error)}}, ensure_ascii=False))
            raise SystemExit(2) from error
        finally:
            if client:
                client.close()
    if args.command == "doctor":
        report = asyncio.run(doctor(config))
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if report["startup_ready"] else 1)
    if args.command == "tasks":
        from .store import Store
        print(json.dumps(Store(config.workspace_root / ".forestry" / "state.sqlite").task(args.task),
                         ensure_ascii=False, indent=2))
        return
    if args.command == "serve":
        import uvicorn

        from .app import create_app
        uvicorn.run(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
