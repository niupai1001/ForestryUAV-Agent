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
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("operation", choices=["freeze", "run", "review", "rescore", "report"])
    evaluate.add_argument("--suite", type=Path, default=Path("fixtures/evaluation/general/suite.json"))
    evaluate.add_argument("--manifest", type=Path, default=Path("workspace/evaluation/general-pilot.json"))
    evaluate.add_argument("--directory", type=Path, default=Path("workspace/evaluation/trials"))
    evaluate.add_argument("--trial")
    evaluate.add_argument("--timeout", type=int, default=30)
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument("--review-file", type=Path)
    evaluate.add_argument("--config", default="config/app.example.yaml")
    backup = commands.add_parser("backup", help="离线备份、校验或恢复工作区状态与任务产物")
    backup.add_argument("operation", choices=["create", "inspect", "restore"])
    backup.add_argument("--path", type=Path, required=True, help="独立备份目录；create要求新位置")
    backup.add_argument("--config", default="config/app.example.yaml")
    cloud = commands.add_parser("inspect-cloud", help="离线只读检查LAS/LAZ头、坐标和有界样本")
    cloud.add_argument("--path", type=Path, required=True)
    cloud.add_argument("--declared", type=Path, help="有来源说明的元数据声明JSON，与文件观察分开")
    cloud.add_argument("--hash-file", action="store_true", help="流式计算整个源文件SHA256，可能耗时")
    cloud.add_argument("--output", type=Path, help="新JSON文件；不覆盖已有记录")
    terrain = commands.add_parser("lidar-terrain", help="授权原始LAS与声明资源→现有持久Jobs处理地形/HAG")
    terrain.add_argument("--config", default="config/app.example.yaml")
    terrain.add_argument("--task", required=True)
    terrain.add_argument("--cloud-ref", required=True)
    terrain.add_argument("--declaration-ref", required=True)
    terrain.add_argument("--revision", type=int, required=True)
    terrain.add_argument("--request-id", required=True, help="保留同请求ID以读取原作业；不自动重算")
    terrain.add_argument("--seconds", type=int, default=900)
    terrain.add_argument("--resume-job", help="本任务原失败Job；只读复用地形/HAG做后处理")
    terrain.add_argument("--resume-refs", type=Path, help="已明确登记/修订的恢复文件名→资源ID JSON")
    args = parser.parse_args()
    if args.command == "inspect-cloud":
        from pydantic import ValidationError

        from .cloud_inputs import CloudDeclaration, inspect_cloud
        from .contracts import Failure
        try:
            declaration = None
            if args.declared:
                if args.declared.stat().st_size > 32768:
                    raise Failure("INPUT_LIMIT", "声明JSON超过32KiB", 422)
                declaration = CloudDeclaration.model_validate_json(args.declared.read_bytes())
            result = inspect_cloud(args.path, declaration, hash_file=args.hash_file)
            output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
            if args.output:
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(output + "\n")
            print(output)
        except (Failure, OSError, ValueError, ValidationError) as error:
            print(json.dumps({"error": {"code": getattr(error, "code", "CLOUD_INSPECTION_ERROR"),
                                        "message": str(error)}}, ensure_ascii=False))
            raise SystemExit(2) from error
        return
    if args.command == "schema":
        from pydantic_ai_harness.planning import PlanItem

        from . import contracts
        from .acquisition import PackageRequest, WheelRequest
        from .evaluation import EvaluationDiagnosticReview, EvaluationReview, EvaluationSuite
        from .lidar_terrain import TerrainRequest
        from .roles import AssignmentRequest, AssignmentResult, RoleCatalog
        target = Path(args.output)
        target.mkdir(parents=True, exist_ok=True)
        for name in ["TaskCreate", "Message", "Amendment", "Control", "RegisterResource", "SettingsChange", "Acceptance", "MemorySave", "MemoryUpdate", "MemoryForget", "KnowledgeImport", "KnowledgeQuery", "ResourceRequest", "ApprovalResolution"]:
            model = getattr(contracts, name)
            (target / f"{name}.schema.json").write_text(
                json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
        for model in [RoleCatalog, AssignmentRequest, AssignmentResult, PlanItem, WheelRequest, PackageRequest,
                      EvaluationSuite, EvaluationReview, EvaluationDiagnosticReview, TerrainRequest]:
            (target / f"{model.__name__}.schema.json").write_text(
                json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
        return
    config = load_config(args.config)
    if args.command == "lidar-terrain":
        from .client import LocalClient
        from .contracts import Failure
        client = None
        try:
            client = LocalClient(config)
            payload = {
                "request_id": args.request_id, "expected_spec_revision": args.revision,
                "cloud_ref": args.cloud_ref, "declaration_ref": args.declaration_ref, "seconds": args.seconds}
            if args.resume_job or args.resume_refs:
                if not args.resume_job or not args.resume_refs or args.resume_refs.stat().st_size > 32768:
                    raise Failure("SCHEMA_INVALID", "恢复须同时提供原Job与32KiB内资源映射JSON", 422)
                payload.update(resume_job_id=args.resume_job,
                               resume_refs=json.loads(args.resume_refs.read_text(encoding="utf-8")))
            result = client.request("POST", f"/tasks/{args.task}/lidar/terrain", payload)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result.get("kind") == "failed":
                raise SystemExit(2)
        except (Failure, OSError, ValueError, httpx.HTTPError) as error:
            print(json.dumps({"error": {"code": getattr(error, "code", "CLI_ERROR"),
                                        "message": str(error)}}, ensure_ascii=False))
            raise SystemExit(2) from error
        finally:
            if client:
                client.close()
        return
    if args.command == "backup":
        import sqlite3

        from .backup import create_backup, inspect_backup, restore_backup
        from .contracts import Failure
        try:
            operation = {"create": lambda: create_backup(config, args.path),
                         "inspect": lambda: inspect_backup(args.path),
                         "restore": lambda: restore_backup(config, args.path)}[args.operation]
            print(json.dumps(operation(), ensure_ascii=False, indent=2))
        except (Failure, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            print(json.dumps({"error": {"code": getattr(error, "code", "BACKUP_ERROR"), "message": str(error)}}, ensure_ascii=False))
            raise SystemExit(2) from error
        return
    if args.command == "evaluate":
        from .client import LocalClient
        from .contracts import Failure
        from .evaluation import freeze_suite, rescore_trial, review_trial, run_trial, summarize, write_json
        try:
            if args.operation == 'freeze':
                if args.manifest.exists():
                    raise Failure('VERSION_CONFLICT', '冻结清单已存在；更改实验须使用新路径', 409)
                client = LocalClient(config)
                try:
                    report = freeze_suite(args.suite, config, client, asyncio.run(doctor(config, probe_search=False)))
                finally:
                    client.close()
                args.manifest.parent.mkdir(parents=True, exist_ok=True)
                with args.manifest.open('x', encoding='utf-8', newline='\n') as handle:
                    handle.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
                print(json.dumps({'manifest': str(args.manifest.resolve()), 'manifest_sha256': report['manifest_sha256'],
                    'scheduled': len(report['manifest']['trials']), 'instrumentation_missing': report['manifest']['instrumentation_missing']}, ensure_ascii=False))
            else:
                envelope = json.loads(args.manifest.read_text(encoding='utf-8'))
                if args.operation == 'run':
                    if not args.trial:
                        parser.error('evaluate run requires --trial')
                    result = run_trial(envelope, args.trial, args.directory, config, timeout=args.timeout)
                    print(json.dumps({'trial': result['trial'], 'task_id': result['task_id'],
                        'observation': result['observation'], 'interaction_required': result['interaction_required'],
                        'score': result.get('score'), 'accepted': result['accepted']}, ensure_ascii=False))
                elif args.operation == 'rescore':
                    if not args.review_file or not args.output:
                        parser.error('evaluate rescore requires --review-file and a separate --output')
                    if args.output.resolve() == args.manifest.resolve() or args.output.resolve().parent == args.directory.resolve():
                        raise Failure('VERSION_CONFLICT', '重新评分结果须与原清单/试次目录分开保存', 409)
                    if args.output.exists():
                        raise Failure('VERSION_CONFLICT', '重新评分结果已存在；使用新路径保留历史', 409)
                    result = rescore_trial(envelope, json.loads(args.review_file.read_text(encoding='utf-8')), args.directory, config)
                    write_json(args.output, result)
                    print(json.dumps({'trial': result['trial'], 'score': result['score'],
                        'output': str(args.output.resolve()), 'auto_accepted': False}, ensure_ascii=False))
                elif args.operation == 'review':
                    if not args.review_file:
                        parser.error('evaluate review requires --review-file')
                    result = review_trial(envelope, json.loads(args.review_file.read_text(encoding='utf-8')), args.directory, config)
                    print(json.dumps(result, ensure_ascii=False))
                else:
                    result = summarize(envelope, args.directory)
                    if args.output:
                        write_json(args.output, result)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        except (Failure, OSError, ValueError, httpx.HTTPError) as error:
            print(json.dumps({'error': {'code': getattr(error, 'code', 'EVALUATION_ERROR'), 'message': str(error)}}, ensure_ascii=False))
            raise SystemExit(2) from error
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
