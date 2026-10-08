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
    parser.add_argument("--build-only", action="store_true", help="Reuse one source-built wheel after missing runtime dependencies")
    parser.add_argument("--source-candidate", action="store_true", help="Resolve docopt from its source-only index without supplying an archive filename")
    parser.add_argument('--discover-build',action='store_true',help='Discover static and dynamic docopt build requirements in offline Jobs')
    args = parser.parse_args()
    if args.discover_build:args.source_candidate=True
    if args.source_candidate:args.build_only=True
    trial = str(uuid4())
    directory = ROOT/"workspace"/"public-source-trials"/trial
    directory.mkdir(parents=True)
    config = load_config(args.config).model_copy(update={"workspace_root":directory/"runtime"})
    evidence = {"trial_id":trial, "model_used":False, "autonomous_source_fallback":False, 'build_only':args.build_only, 'automatic_source_candidate':args.source_candidate,
        'automatic_build_requirement_discovery':args.discover_build,
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
            "objective":f"公开{'docopt' if args.source_candidate else 'sampleproject'}源码解析下载、断网构建及同一环境两次运行；仅软件集成验证",
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

        def resolve(call, target=None, approval=None, *, requirements=None, built=None):
            roots = requirements if requirements is not None else REQUIREMENTS[:2] if args.build_only else REQUIREMENTS
            payload = {"requirements":roots,"target_artifact_id":target,"approval_id":approval}
            if built:payload['built_wheel_artifact_ids'] = built
            return engine.perform(deps, call, "environment.acquire", payload,
                lambda action:engine.resolution.resolve(deps, action, roots, target, approval,built_wheel_artifact_ids=built))

        try:
            target=None
            source_request=SOURCE
            source_lock=None
            if args.source_candidate:
                probe=observe(resolve('source-target-probe',requirements=['docopt==0.6.2']),'actual_target_probe')
                target=next(ref for ref in probe['job']['artifact_ids'] if engine.resources.artifact(ref)['name']=='execution-target.json')
                candidate_proposal=resolve('source-candidate-proposal',target,requirements=['docopt==0.6.2'])
                evidence['source_candidate_proposal']=candidate_proposal
                candidate=resolve('approved-source-candidate',target,approve(candidate_proposal),requirements=['docopt==0.6.2'])
                evidence['source_candidate']=candidate
                assert candidate['kind']=='completed' and candidate['resolution_complete'] is False,candidate
                assert len(candidate['source_candidates'])==1 and candidate['wheels']==[],candidate
                source_request=candidate['source_candidates'][0]
                source_lock=candidate['resolution_lock_artifact_id']
            proposal = acquire("source-proposal", [], [source_request],lock=source_lock)
            evidence["source_proposal"] = proposal
            source_result = acquire("approved-source", [], [source_request], approve(proposal),source_lock)
            evidence["source_acquisition"] = source_result
            assert source_result["kind"] == "completed", source_result
            source_id = source_result["source_artifact_ids"][0]
            source = engine.environments.acquired_source(task_id, 1, source_id)
            source_sha = file_hash(native_path(source["locator"]))

            def prepare(call, wheels, expected="succeeded", *, build_only=False, with_source=True,inspect_build=None):
                sources = [source_id] if with_source else []
                payload = {"source_artifact_ids":sources, "wheel_artifact_ids":wheels, "input_refs":[], 'build_only':build_only}
                if inspect_build:payload['inspect_build']=inspect_build
                return observe(engine.perform(deps, call, "environment.prepare", payload,
                    lambda action:engine.environments.prepare(task_id, 1, action, [],
                        source_artifact_ids=sources, wheel_artifact_ids=wheels, build_only=build_only,
                        inspect_build=inspect_build)), call, expected)

            def inspect(call,wheels,mode):
                case=prepare(call,wheels,inspect_build=mode)
                observed=engine.perform(deps,call+'-observe','job.observe',{'job_id':case['job']['job_id']},
                    lambda _:jobs.observation(case['job']['job_id'],task_id))
                result=observed['build_requirements']
                ref=result['artifact_id']
                readback=engine.perform(deps,call+'-read','artifact.read',{'artifact_id':ref},
                    lambda _:engine.resources.read_artifact(task_id,ref))
                evidence[call]={'case':case,'result':result,'readback':readback}
                assert 'prepared_environment' not in observed and 'prepared_wheels' not in observed
                return result

            failed_build = prepare("missing_build_dependencies", [], "failed")
            assert "setuptools" in json.dumps(failed_build["logs"])

            if target is None:
                probe = observe(resolve("target-probe"), "actual_target_probe")
                target = next(ref for ref in probe["job"]["artifact_ids"]
                    if engine.resources.artifact(ref)["name"] == "execution-target.json")
            build_roots=None
            if args.discover_build:
                static=inspect('static-build-requirements',[],'static')
                build_roots=static['requirements']+static['frontend_requirements']
            resolution_proposal = resolve("resolution-proposal", target,requirements=build_roots)
            evidence["resolution_proposal"] = resolution_proposal
            resolution = resolve("approved-resolution", target, approve(resolution_proposal),requirements=build_roots)
            evidence["resolution"] = resolution
            assert resolution["kind"] == "completed", resolution
            lock = resolution["resolution_lock_artifact_id"]
            wheel_proposal = acquire("wheel-proposal", resolution["wheels"], lock=lock)
            evidence["wheel_proposal"] = wheel_proposal
            wheels = acquire("approved-wheels", resolution["wheels"], approval=approve(wheel_proposal), lock=lock)
            evidence["wheel_acquisition"] = wheels
            assert wheels["kind"] == "completed", wheels
            build_ids = [w["artifact_id"] for w in wheels["wheels"] if w["name"] != "peppercorn"]
            if args.discover_build:
                dynamic=inspect('dynamic-build-requirements',build_ids,'dynamic')
                assert dynamic['projects'][0]['static_requires']==static['projects'][0]['static_requires']
                if any(r not in static['requirements'] for r in dynamic['requirements']):
                    complete_roots=dynamic['requirements']+dynamic['frontend_requirements']
                    extra_proposal=resolve('complete-build-proposal',target,requirements=complete_roots)
                    complete=resolve('complete-build-resolve',target,approve(extra_proposal),requirements=complete_roots)
                    evidence['complete_build_resolution']=complete
                    extra=acquire('complete-build-download-proposal',complete['wheels'],lock=complete['resolution_lock_artifact_id'])
                    fetched=acquire('complete-build-download',complete['wheels'],approval=approve(extra),lock=complete['resolution_lock_artifact_id'])
                    evidence['complete_build_acquisition']=fetched
                    build_ids=fetched['wheel_artifact_ids']
            build_case = None
            runtime_wheels = wheels
            if args.build_only:
                build_case = prepare('source_build_without_runtime_installation',build_ids,build_only=True)
                build_observed = engine.perform(deps,'observe-source-wheel','job.observe',{'job_id':build_case['job']['job_id']},
                    lambda _:jobs.observation(build_case['job']['job_id'],task_id))
                evidence['source_build'] = build_observed['prepared_wheels']
                build_ids = evidence['source_build']['wheel_artifact_ids']
                for record in evidence['source_build']['built_wheels']:
                    ref = record['metadata_artifact_id']
                    metadata = engine.perform(deps,'read-source-metadata','artifact.read',{'artifact_id':ref},
                        lambda _,ref=ref:engine.resources.read_artifact(task_id,ref))
                    evidence['built_metadata_readback'] = metadata
                    assert ('Name: docopt' if args.source_candidate else 'Requires-Dist: peppercorn') in metadata['text'],metadata
                runtime_roots = [source_request['name']+'=='+source_request['version']]
                runtime_proposal = resolve('runtime-resolution-proposal',target,requirements=runtime_roots,built=build_ids)
                evidence['runtime_resolution_proposal'] = runtime_proposal
                runtime_resolution = resolve('approved-runtime-resolution',target,approve(runtime_proposal),requirements=runtime_roots,built=build_ids)
                evidence['runtime_resolution'] = runtime_resolution
                assert runtime_resolution['kind']=='completed',runtime_resolution
                runtime_lock = runtime_resolution['resolution_lock_artifact_id']
                runtime_wheels={'wheels':[],'wheel_artifact_ids':[]}
                if runtime_resolution['wheels']:
                    runtime_download = acquire('runtime-wheel-proposal',runtime_resolution['wheels'],lock=runtime_lock)
                    evidence['runtime_download_proposal'] = runtime_download
                    runtime_wheels = acquire('approved-runtime-wheels',runtime_resolution['wheels'],approval=approve(runtime_download),lock=runtime_lock)
                    evidence['runtime_wheel_acquisition'] = runtime_wheels
                    assert runtime_wheels['kind']=='completed',runtime_wheels
            failed_runtime=None
            if not args.source_candidate:
                failed_runtime = prepare("missing_runtime_dependency", build_ids, "failed",with_source=not args.build_only)
                assert "peppercorn" in json.dumps(failed_runtime["logs"])
            final_wheels = build_ids + runtime_wheels['wheel_artifact_ids'] if args.build_only else wheels['wheel_artifact_ids']
            ready = prepare("build_and_install_full_closure", final_wheels,with_source=not args.build_only)
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
                if args.source_candidate:
                    code=code.replace("from sample.simple import add_one\nimport peppercorn", "from docopt import docopt")
                    code=code.replace("dict(answer=add_one(41),version=importlib.metadata.version('sampleproject'),dependency_version=importlib.metadata.version('peppercorn'),uid=os.getuid(),network_blocked=blocked)", "dict(parsed=docopt('Usage: example [--flag] <value>',argv=['--flag','hello']),version=importlib.metadata.version('docopt'),uid=os.getuid(),network_blocked=blocked)")
                computed = observe(engine.perform(deps, "reuse-"+str(index), "code.execute",
                    {"environment_artifact_id":archive, "input_refs":[]},
                    lambda action, code=code:jobs.submit(task_id, 1, action, code, [], 30,
                        environment_artifact_id=archive)), "reuse_"+str(index))
                ref = computed["job"]["artifact_ids"][0]
                readback = engine.perform(deps, "read-"+str(index), "artifact.read", {"artifact_id":ref},
                    lambda _, ref=ref:engine.resources.read_artifact(task_id, ref))
                computed["readback"], computed["numbers"] = readback, json.loads(readback["text"])
                results.append(computed)
            source_case = build_case or ready
            built = evidence['source_build']['built_wheels'] if args.build_only else environment["manifest"]["built_wheels"]
            expected={"parsed":{"--flag":True,"<value>":"hello"},"version":"0.6.2","uid":65534,"network_blocked":True} if args.source_candidate else {"answer":42,"version":"4.0.0","dependency_version":"0.6","uid":65534,"network_blocked":True}
            checks = {
                "official_typed_hashed_sdist":source_result["source_packages"][0]["sha256"] == source_sha and source_result["wheels"] == [],
                "distinct_exact_approvals_consumed":len(engine.network.list(task_id)) == ((7 if 'complete_build_resolution' in evidence else 5) if args.build_only else 3) and all(g["status"] == "consumed" for g in engine.network.list(task_id)),
                "actual_target_and_complete_resolved_build_wheels":({'setuptools','pyproject-hooks','packaging'} <= {w['name'] for w in resolution['wheels']} if args.discover_build else
                    {w["name"] for w in resolution["wheels"]} == ({"setuptools","wheel"} if args.build_only else {"setuptools","wheel","peppercorn"})),
                ("missing_build_failure_preserved" if args.source_candidate else "missing_build_and_runtime_failures_preserved"):failed_build["job"]["status"] == "failed" and (args.source_candidate or failed_runtime["job"]["status"] == "failed"),
                "sdist_reused_as_readonly_acquired_artifact":source_case["job"]["source_recipe"][0]["source_kind"] == "acquired_artifact" and source_case["job"]["source_recipe"][0]["resource_id"] == source_id,
                "built_wheel_bound_to_official_source":len(built) == 1 and built[0]["source_sha256"] == source_sha and built[0]["filename"] == ("docopt-0.6.2-py2.py3-none-any.whl" if args.source_candidate else "sampleproject-4.0.0-py3-none-any.whl"),
                "native_pep517_isolation":(engine.environments.built(task_id,1,build_case['job']['action_id'])['runtime_installation'] is False
                    if args.build_only else environment["manifest"]["build_frontend"]["isolation"] == "native_pep517"),
                "two_exact_actual_readbacks":all(c["numbers"] == expected for c in results),
                "same_environment_and_image":all(c["job"]["environment"]["artifact_id"] == archive and c["container"]["Image"] == environment["image_id"] for c in results),
                "all_containers_restricted":all(c["container"]["HostConfig"]["NetworkMode"] == "none" and c["container"]["HostConfig"]["ReadonlyRootfs"] and "ALL" in c["container"]["HostConfig"]["CapDrop"] and c["container"]["Config"]["User"] == "65534:65534" for c in evidence["cases"]),
                "source_archive_unchanged":file_hash(native_path(source["locator"])) == source_sha,
                "host_packages_unchanged":host_packages() == before_host,
            }
            if args.build_only:
                checks.update(
                    one_successful_build_only_reused_for_install=not ready['job'].get('source_recipe') and
                        (args.source_candidate or (not failed_runtime['job'].get('source_recipe') and [w['resource_id'] for w in failed_runtime['job']['environment_recipe']]==evidence['source_build']['wheel_artifact_ids'])) and
                        built[0]['artifact_id'] in [w['resource_id'] for w in ready['job']['environment_recipe']],
                    final_environment_has_only_runtime_packages={p['name'] for p in environment['manifest']['installed']}==({'docopt'} if args.source_candidate else {'sampleproject','peppercorn'}),
                    built_metadata_actual_readback_and_no_environment=evidence['built_metadata_readback']['kind']=='completed' and
                        all(engine.resources.artifact(ref)['name']!='environment.zip' for ref in build_case['job']['artifact_ids']),
                    runtime_dependency_derived_from_built_metadata=evidence['runtime_resolution']['built_wheel_artifact_ids']==build_ids and
                        {w['name'] for w in evidence['runtime_resolution']['wheels']}==(set() if args.source_candidate else {'peppercorn'}) and runtime_roots==[source_request['name']+'=='+source_request['version']])
            if args.source_candidate:
                checks.update(
                    native_resolver_selected_source_without_filename=source_request=={'name':'docopt','version':'0.6.2','filename':'docopt-0.6.2.tar.gz'} and candidate['resolution_complete'] is False,
                    source_index_release_and_bytes_same_sha=candidate['source_candidate_sha256'][source_request['filename']]==source_sha=='49b3a825280bd66b3aa83585ef59c4a8c82f2c8a522dbe754a8bc8d08c85c491',
                    source_candidate_lock_separate_download_approval=next(g for g in engine.network.list(task_id) if g['consumed_action_id']==source_result['action_id'])['request']['resolution_lock_artifact_id']==source_lock,
                    native_pep517_actual_backend_log='pyproject.toml' in json.dumps(build_case['logs']) and 'Installing build dependencies' in json.dumps(build_case['logs']),
                    final_complete_local_only_resolution=evidence['runtime_resolution']['wheels']==[] and len(evidence['runtime_resolution']['built_wheel_artifact_ids'])==1)
            if args.discover_build:
                checks.update(
                    build_roots_derived_from_static_archive=static['requirements']==['setuptools>=40.8.0'] and build_roots==static['requirements']+static['frontend_requirements'],
                    actual_public_backend_hook_discovery=dynamic['inspection']=='dynamic' and dynamic['runtime_installation'] is False and
                        dynamic['projects'][0]['frontend']=={'name':'pyproject-hooks','version':'1.2.0','hook':'get_requires_for_build_wheel'},
                    inspections_actual_readback_and_no_environment=all(evidence[k]['readback']['kind']=='completed' and
                        all(engine.resources.artifact(ref)['name']!='environment.zip' for ref in evidence[k]['case']['job']['artifact_ids'])
                        for k in ['static-build-requirements','dynamic-build-requirements']))
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
