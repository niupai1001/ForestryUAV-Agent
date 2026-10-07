"""Real public acquisition tool -> offline install -> two restricted computations.

This exercises the application's model-callable tool and explicit approval API,
without invoking a language model. It is not autonomous model/UI acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import sys
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.jobs import TERMINAL
from forestry_agent.resources import file_hash

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)
WHEELS = [
    {"name": "numpy", "version": "2.2.6", "filename": "numpy-2.2.6-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"},
    {"name": "python-dateutil", "version": "2.9.0.post0", "filename": "python_dateutil-2.9.0.post0-py2.py3-none-any.whl"},
    {"name": "six", "version": "1.17.0", "filename": "six-1.17.0-py2.py3-none-any.whl"},
]


def host_packages():
    content = sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())
    return hashlib.sha256(json.dumps(content).encode()).hexdigest()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/app.wsl.example.yaml")
    parser.add_argument("--source", choices=["pypi", "tuna"], default="pypi")
    parser.add_argument("--resolve", action="store_true", help="从两个根需求解析传递依赖并绑定解析锁")
    args = parser.parse_args()
    trial = str(uuid4())
    directory = ROOT / "workspace" / "acquisition-trials" / trial
    directory.mkdir(parents=True)
    config = load_config(args.config).model_copy(update={"workspace_root": directory / "runtime"})
    initial_packages = host_packages()
    evidence = {"trial_id": trial, "model_used": False, "agent_tool_acquisition": True,
        "autonomous_model_acquisition": False, "source": args.source, "all_passed": False,
        "automatic_resolution": args.resolve,
        "general_stage_acceptance": "incomplete", "scientific_accuracy": "not_assessed", "cases": []}
    output = ROOT / "workspace" / "evidence" / ("acquisition-real-" + trial + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(config, enable_agent=False)
    engine, jobs = app.state.engine, app.state.engine.jobs
    owned = []
    with TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}) as client:
        client.get("/session").raise_for_status()
        source = directory / "heights.csv"
        source.write_bytes(b"height_m\n8.25\n12.50\n16.75\n")
        initial_source = file_hash(source)
        grant = client.post("/resources/register", json={"request_id": str(uuid4()), "path": str(source), "classification": "private", "read_only": True})
        grant.raise_for_status()
        ref = grant.json()["resource_id"]
        response = client.post("/tasks", json={"request_id": str(uuid4()), "objective": "测试公开依赖获取、断网容器安装和只读软件夹具计算",
            "input_refs": [ref], "constraints": {"allowed_capabilities": ["environment.acquire", "environment.prepare", "code.execute", "job.observe", "artifact.read"]}})
        response.raise_for_status()
        task_id = response.json()["task_id"]
        evidence["task_id"] = task_id
        deps = Deps(engine, task_id, "acquisition-smoke-" + trial, 1)
        wheels, resolution_lock = WHEELS, None
        print(json.dumps({"trial_id": trial, "task_id": task_id}), flush=True)
        def get_wheels(call, approval=None):
            payload = {"wheels": wheels, "source": args.source, "approval_id": approval}
            if resolution_lock:
                payload['resolution_lock_artifact_id'] = resolution_lock
            return engine.perform(deps, call, "environment.acquire", payload,
                lambda action: engine.acquisition.acquire(deps, action, wheels, args.source, approval, resolution_lock))
        def observe(reply, scenario):
            assert reply["kind"] == "completed", reply
            job_id = reply["job"]["job_id"]
            owned.append(job_id)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                jobs.tick()
                job = jobs.read(job_id, task_id)
                if job["status"] in TERMINAL or job.get("blocker"):
                    break
                time.sleep(0.3)
            case = {"scenario": scenario, "job": job, "logs": jobs.logs(job_id, task_id), "container": jobs.docker.inspect(job["container_name"])}
            evidence["cases"].append(case)
            print(json.dumps({"scenario": scenario, "job_id": job_id, "status": job["status"], "blocker": job.get("blocker")}), flush=True)
            assert job["status"] == "succeeded", case["logs"]
            return case
        try:
            if args.resolve:
                roots = ['numpy==2.2.6', 'python-dateutil==2.9.0.post0']
                probe = engine.perform(deps, 'probe-target', 'environment.acquire', {'requirements': roots},
                    lambda action: engine.resolution.resolve(deps, action, roots))
                target_case = observe(probe, 'probe_target_without_inputs')
                target = next(r for r in target_case['job']['artifact_ids'] if engine.resources.artifact(r, task_id)['name'] == 'execution-target.json')
                def resolution(call, approval=None):
                    return engine.perform(deps, call, 'environment.acquire', {'requirements': roots, 'target_artifact_id': target, 'approval_id': approval},
                        lambda action: engine.resolution.resolve(deps, action, roots, target, approval))
                metadata_proposal = resolution('resolution-proposal')
                assert metadata_proposal['kind'] == 'approval_required', metadata_proposal
                metadata_approval = engine.network.list(task_id)[0]
                client.post(f"/tasks/{task_id}/approvals/{metadata_approval['approval_id']}", json={
                    'request_id': str(uuid4()), 'expected_spec_revision': 1,
                    'request_sha256': metadata_approval['request_sha256'], 'verdict': 'approve'}).raise_for_status()
                resolved = resolution('approved-resolution', metadata_approval['approval_id'])
                evidence['resolution'] = resolved
                print(json.dumps({'resolution_kind': resolved['kind'], 'selected': resolved.get('wheels'), 'error': resolved.get('error')}), flush=True)
                assert resolved['kind'] == 'completed', resolved
                wheels, resolution_lock = resolved['wheels'], resolved['resolution_lock_artifact_id']
                assert resolution('approved-resolution', metadata_approval['approval_id']) == resolved
                evidence['resolution_lock'] = engine.resolution.lock(deps, resolution_lock)[0]
            before_proposal = list(engine.store.task(task_id)['artifact_ids'])
            proposal = get_wheels("proposal")
            evidence["proposal"] = proposal
            assert proposal["kind"] == "approval_required"
            records = engine.network.list(task_id)
            approval = records[-1]
            assert engine.store.task(task_id)["artifact_ids"] == before_proposal
            reply = client.post(f"/tasks/{task_id}/approvals/{approval['approval_id']}", json={"request_id": str(uuid4()),
                "expected_spec_revision": 1, "request_sha256": approval["request_sha256"], "verdict": "approve"})
            reply.raise_for_status()
            acquired = get_wheels("approved-acquisition", approval["approval_id"])
            evidence["acquisition"] = acquired
            print(json.dumps({"acquisition_kind": acquired["kind"], "wheels": len(acquired.get("wheels", [])), "error": acquired.get("error")}), flush=True)
            assert acquired["kind"] == "completed", acquired
            wheel_ids = acquired["wheel_artifact_ids"]
            replay = get_wheels("approved-acquisition", approval["approval_id"])
            assert replay == acquired
            preparation = engine.perform(deps, "prepare", "environment.prepare", {"wheel_artifact_ids": wheel_ids},
                lambda action: engine.environments.prepare(task_id, 1, action, [], wheel_artifact_ids=wheel_ids))
            ready = observe(preparation, "install_acquired_wheels")
            receipt = engine.perform(deps, "observe-environment", "job.observe", {"job_id": ready["job"]["job_id"]},
                lambda _: jobs.observation(ready["job"]["job_id"], task_id))
            archive = receipt["prepared_environment"]["artifact_id"]
            environment = engine.environments.resolve(task_id, 1, archive)
            computations = []
            for n in range(2):
                code = f'''import csv,json,os,socket
from pathlib import Path
import numpy as np,six,dateutil
from dateutil.parser import parse
values=np.array([float(r['height_m']) for r in csv.DictReader(Path('/inputs/{ref}').open())])
try:
    socket.create_connection(('1.1.1.1',443),timeout=1)
    blocked=False
except OSError:
    blocked=True
result=dict(tree_count=int(values.size),total_height_m=float(values.sum()),mean_height_m=float(values.mean()),
    parsed_date=parse('2026-10-06').date().isoformat(),numpy_version=np.__version__,six_version=six.__version__,
    dateutil_version=dateutil.__version__,uid=os.getuid(),network_blocked=blocked)
Path('/output/acquisition-result-{n}.json').write_text(json.dumps(result),encoding='utf-8')
'''
                executed = engine.perform(deps, "compute-" + str(n), "code.execute", {"input_refs": [ref], "code": code, "environment_artifact_id": archive},
                    lambda action, code=code: jobs.submit(task_id, 1, action, code, [ref], 60, environment_artifact_id=archive))
                case = observe(executed, "reuse_" + str(n))
                product = case["job"]["artifact_ids"][0]
                readback = engine.perform(deps, "read-" + str(n), "artifact.read", {"artifact_id": product},
                    lambda _, product=product: engine.resources.read_artifact(task_id, product))
                case["readback"] = readback
                case["numbers"] = json.loads(readback["text"])
                computations.append(case)
            expected = {"tree_count": 3, "total_height_m": 37.5, "mean_height_m": 12.5, "parsed_date": "2026-10-06",
                "numpy_version": "2.2.6", "six_version": "1.17.0", "dateutil_version": "2.9.0.post0", "uid": 65534, "network_blocked": True}
            checks = {"exact_approved_request": all(g['status'] == 'consumed' for g in engine.network.list(task_id)),
                "three_actual_downloads": len(acquired["wheels"]) == 3,
                "hashes_match_official_and_frozen": all(file_hash(Path(engine.environments.acquired_wheel(task_id, 1, r["artifact_id"])["locator"])) == r["sha256"] for r in acquired["wheels"]),
                "one_successful_installation": ready["job"]["status"] == "succeeded",
                "installed_exact_versions": {d["name"].lower(): d["version"] for d in environment["manifest"]["installed"]} == {"numpy": "2.2.6", "python-dateutil": "2.9.0.post0", "six": "1.17.0"},
                "two_real_computations_and_readbacks": all(c["numbers"] == expected for c in computations),
                "same_archive_and_image": all(c["job"]["environment"]["artifact_id"] == archive and c["container"]["Image"] == environment["image_id"] for c in computations),
                "containers_remain_restricted": all(c["container"]["HostConfig"]["NetworkMode"] == "none" and c["container"]["HostConfig"]["ReadonlyRootfs"] and "ALL" in c["container"]["HostConfig"]["CapDrop"] for c in evidence["cases"]),
                "readonly_input_unchanged": file_hash(source) == initial_source, "host_packages_unchanged": host_packages() == initial_packages,
                "replay_preserves_original_ids": replay == acquired}
            if args.resolve:
                checks.update({'automatic_transitive_six': 'six' not in roots and {w['name'] for w in wheels} == {'numpy', 'python-dateutil', 'six'},
                    'target_has_no_private_input_mounts': target_case['job']['mounts'] == [],
                    'probed_image_matches_installation': target_case['container']['Image'] == environment['image_id'],
                    'download_hashes_match_resolution': {w['name']: w['sha256'] for w in acquired['wheels']} == resolved['wheel_sha256']})
            evidence.update(checks=checks, all_passed=all(checks.values()))
        except Exception as error:
            logger.exception("Real acquisition/install trial failed; original evidence retained")
            evidence["error"] = {"type": type(error).__name__, "message": str(error)}
            print(json.dumps(evidence["error"], ensure_ascii=False), flush=True)
        finally:
            cleanup = []
            for job_id in owned:
                try:
                    job = jobs.read(job_id, task_id)
                    if job["status"] not in TERMINAL:
                        jobs.request_cancel(job_id, task_id)
                        jobs.tick()
                        job = jobs.read(job_id, task_id)
                    cleanup.append({"job_id": job_id, "status": job["status"], "confirmed_terminal": job["status"] in TERMINAL})
                except Exception as error:
                    logger.exception("Owned acquisition job cleanup was not confirmed")
                    cleanup.append({"job_id": job_id, "confirmed_terminal": False, "error": str(error)})
            evidence["cleanup"] = cleanup
            evidence["all_passed"] &= all(j["confirmed_terminal"] for j in cleanup)
            evidence["state"] = engine.store.describe_task(task_id)
            evidence["events"] = engine.store.events(task_id, limit=1000)
            output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(output, flush=True)
    return 0 if evidence["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
