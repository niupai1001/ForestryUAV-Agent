"""Approved public sdist -> offline build/repair -> two actual uses.

No model is invoked. Public package bytes are never unpacked/imported on the
host; all package execution uses existing restricted Docker Jobs.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from smoke_source_build import host_packages

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.jobs import TERMINAL
from forestry_agent.resources import file_hash, native_path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = {"name":"sampleproject", "version":"4.0.0", "filename":"sampleproject-4.0.0.tar.gz"}
REQUIREMENTS = ["setuptools==80.9.0", "wheel==0.45.1", "peppercorn==0.6"]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/app.wsl.example.yaml")
    parser.add_argument("--source", choices=["pypi", "tuna"], default="pypi")
    args = parser.parse_args()
    trial = str(uuid4())
    directory = ROOT/"workspace"/"public-source-trials"/trial
    directory.mkdir(parents=True)
    config = load_config(args.config).model_copy(update={"workspace_root":directory/"runtime"})
    evidence = {"trial_id":trial, "model_used":False, "autonomous_source_fallback":False,
        "scientific_accuracy":"not_assessed", "general_stage_acceptance":"incomplete",
        "all_passed":False, "cases":[]}
    output = ROOT/"workspace"/"evidence"/("public-source-real-"+trial+".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    before_host = host_packages()
    app = create_app(config, enable_agent=False)
    engine, jobs = app.state.engine, app.state.engine.jobs
    owned = []
    with TestClient(app, base_url="http://127.0.0.1", headers={"origin":"http://127.0.0.1"}) as client:
        client.get("/session").raise_for_status()
        response = client.post("/tasks", json={"request_id":str(uuid4()),
            "objective":"公开sampleproject源码下载、断网构建、缺失依赖修复与同一环境两次运行；仅软件集成验证",
            "constraints":{"allowed_capabilities":["environment.acquire", "environment.prepare", "code.execute", "job.observe", "artifact.read"]}})
        response.raise_for_status()
        task_id = response.json()["task_id"]
        deps = Deps(engine, task_id, "public-source-smoke-"+trial, 1)
        evidence["task_id"] = task_id
        print(json.dumps({"trial_id":trial, "task_id":task_id}), flush=True)

        def observe(reply, scenario, expected="succeeded"):
            assert reply["kind"] == "completed", reply
            job_id = reply["job"]["job_id"]
            owned.append(job_id)
            deadline = time.monotonic()+240
            while time.monotonic() < deadline:
                jobs.tick()
                job = jobs.read(job_id, task_id)
                if job["status"] in TERMINAL or job.get("blocker"):
                    break
                time.sleep(0.3)
            case = {"scenario":scenario, "expected_status":expected, "job":job,
                "logs":jobs.logs(job_id, task_id), "container":jobs.docker.inspect(job["container_name"])}
            evidence["cases"].append(case)
            print(json.dumps({"scenario":scenario, "job_id":job_id, "status":job["status"], "blocker":job.get("blocker")}), flush=True)
            assert job["status"] == expected, case["logs"]
            return case

        def approve(reply):
            assert reply["kind"] == "approval_required", reply
            grant = engine.network.list(task_id)[-1]
            client.post(f"/tasks/{task_id}/approvals/{grant['approval_id']}", json={"request_id":str(uuid4()),
                "expected_spec_revision":1, "request_sha256":grant["request_sha256"], "verdict":"approve"}).raise_for_status()
            return grant["approval_id"]

        def acquire(call, wheels, sources=None, approval=None, lock=None):
            payload = {"wheels":wheels, "source":args.source, "approval_id":approval,
                "resolution_lock_artifact_id":lock, "source_packages":sources or []}
            return engine.perform(deps, call, "environment.acquire", payload,
                lambda action:engine.acquisition.acquire(deps, action, wheels, args.source, approval, lock, source_packages=sources))

        try:
            proposal = acquire("source-proposal", [], [SOURCE])
            evidence["source_proposal"] = proposal
            source_result = acquire("approved-source", [], [SOURCE], approve(proposal))
            evidence["source_acquisition"] = source_result
            assert source_result["kind"] == "completed", source_result
            source_id = source_result["source_artifact_ids"][0]
            source = engine.environments.acquired_source(task_id, 1, source_id)
            source_sha = file_hash(native_path(source["locator"]))

            def prepare(call, wheels, expected="succeeded"):
                payload = {"source_artifact_ids":[source_id], "wheel_artifact_ids":wheels, "input_refs":[]}
                return observe(engine.perform(deps, call, "environment.prepare", payload,
                    lambda action:engine.environments.prepare(task_id, 1, action, [],
                        source_artifact_ids=[source_id], wheel_artifact_ids=wheels)), call, expected)

            failed_build = prepare("missing_build_dependencies", [], "failed")
            assert "setuptools" in json.dumps(failed_build["logs"])

            def resolve(call, target=None, approval=None):
                return engine.perform(deps, call, "environment.acquire", {"requirements":REQUIREMENTS,
                    "target_artifact_id":target, "approval_id":approval},
                    lambda action:engine.resolution.resolve(deps, action, REQUIREMENTS, target, approval))

            probe = observe(resolve("target-probe"), "actual_target_probe")
            target = next(ref for ref in probe["job"]["artifact_ids"]
                if engine.resources.artifact(ref)["name"] == "execution-target.json")
            resolution_proposal = resolve("resolution-proposal", target)
            evidence["resolution_proposal"] = resolution_proposal
            resolution = resolve("approved-resolution", target, approve(resolution_proposal))
            evidence["resolution"] = resolution
            assert resolution["kind"] == "completed", resolution
            lock = resolution["resolution_lock_artifact_id"]
            wheel_proposal = acquire("wheel-proposal", resolution["wheels"], lock=lock)
            evidence["wheel_proposal"] = wheel_proposal
            wheels = acquire("approved-wheels", resolution["wheels"], approval=approve(wheel_proposal), lock=lock)
            evidence["wheel_acquisition"] = wheels
            assert wheels["kind"] == "completed", wheels
            build_ids = [w["artifact_id"] for w in wheels["wheels"] if w["name"] != "peppercorn"]
            failed_runtime = prepare("missing_runtime_dependency", build_ids, "failed")
            assert "peppercorn" in json.dumps(failed_runtime["logs"])
            ready = prepare("build_and_install_full_closure", wheels["wheel_artifact_ids"])
            observed = engine.perform(deps, "observe-build", "job.observe", {"job_id":ready["job"]["job_id"]},
                lambda _:jobs.observation(ready["job"]["job_id"], task_id))
            archive = observed["prepared_environment"]["artifact_id"]
            environment = engine.environments.resolve(task_id, 1, archive)
            evidence["environment"] = environment
            results = []
            for index in range(2):
                code = f'''import importlib.metadata,json,os,socket
from pathlib import Path
from sample.simple import add_one
import peppercorn
try:
    socket.create_connection(('1.1.1.1',443),timeout=1)
    blocked=False
except OSError:
    blocked=True
Path('/output/answer-{index}.json').write_text(json.dumps(dict(answer=add_one(41),version=importlib.metadata.version('sampleproject'),dependency_version=importlib.metadata.version('peppercorn'),uid=os.getuid(),network_blocked=blocked)),encoding='utf-8')
'''
                computed = observe(engine.perform(deps, "reuse-"+str(index), "code.execute",
                    {"environment_artifact_id":archive, "input_refs":[]},
                    lambda action, code=code:jobs.submit(task_id, 1, action, code, [], 30,
                        environment_artifact_id=archive)), "reuse_"+str(index))
                ref = computed["job"]["artifact_ids"][0]
                readback = engine.perform(deps, "read-"+str(index), "artifact.read", {"artifact_id":ref},
                    lambda _, ref=ref:engine.resources.read_artifact(task_id, ref))
                computed["readback"], computed["numbers"] = readback, json.loads(readback["text"])
                results.append(computed)
            built = environment["manifest"]["built_wheels"]
            checks = {
                "official_typed_hashed_sdist":source_result["source_packages"][0]["sha256"] == source_sha and source_result["wheels"] == [],
                "three_distinct_exact_approvals_consumed":len(engine.network.list(task_id)) == 3 and all(g["status"] == "consumed" for g in engine.network.list(task_id)),
                "actual_target_and_complete_resolved_wheels":{w["name"] for w in resolution["wheels"]} == {"setuptools","wheel","peppercorn"},
                "missing_build_and_runtime_failures_preserved":failed_build["job"]["status"] == failed_runtime["job"]["status"] == "failed",
                "sdist_reused_as_readonly_acquired_artifact":ready["job"]["source_recipe"][0]["source_kind"] == "acquired_artifact" and ready["job"]["source_recipe"][0]["resource_id"] == source_id,
                "built_wheel_bound_to_official_source":len(built) == 1 and built[0]["source_sha256"] == source_sha and built[0]["filename"] == "sampleproject-4.0.0-py3-none-any.whl",
                "native_pep517_isolation":environment["manifest"]["build_frontend"]["isolation"] == "native_pep517",
                "two_exact_actual_readbacks":all(c["numbers"] == {"answer":42,"version":"4.0.0","dependency_version":"0.6","uid":65534,"network_blocked":True} for c in results),
                "same_environment_and_image":all(c["job"]["environment"]["artifact_id"] == archive and c["container"]["Image"] == environment["image_id"] for c in results),
                "all_containers_restricted":all(c["container"]["HostConfig"]["NetworkMode"] == "none" and c["container"]["HostConfig"]["ReadonlyRootfs"] and "ALL" in c["container"]["HostConfig"]["CapDrop"] and c["container"]["Config"]["User"] == "65534:65534" for c in evidence["cases"]),
                "source_archive_unchanged":file_hash(native_path(source["locator"])) == source_sha,
                "host_packages_unchanged":host_packages() == before_host,
            }
            evidence.update(checks=checks, all_passed=all(checks.values()))
        except Exception as error:
            logging.getLogger(__name__).exception("Public source trial failed; original evidence retained")
            evidence["error"] = {"type":type(error).__name__, "message":str(error)}
        finally:
            cleanup = []
            for job_id in owned:
                job = jobs.read(job_id, task_id)
                if job["status"] not in TERMINAL:
                    jobs.request_cancel(job_id, task_id)
                    jobs.tick()
                    job = jobs.read(job_id, task_id)
                cleanup.append({"job_id":job_id, "status":job["status"], "confirmed_terminal":job["status"] in TERMINAL})
            evidence["cleanup"] = cleanup
            evidence["all_passed"] &= all(c["confirmed_terminal"] for c in cleanup)
            evidence["state"], evidence["events"] = engine.store.describe_task(task_id), engine.store.events(task_id, limit=1000)
            output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(output, flush=True)
    return 0 if evidence["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
