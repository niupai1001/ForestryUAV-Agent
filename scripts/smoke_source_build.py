"""Real offline source build, repair and reuse through the existing tool/Jobs.

Fixture archives are constructed on the host, never unpacked or executed there.
No model is invoked: this is integration evidence, not Agent/UI acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import logging
import stat
import struct
import sys
import tarfile
import time
import zipfile
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
BUILD_WHEELS = [
    {"name":"setuptools", "version":"80.9.0", "filename":"setuptools-80.9.0-py3-none-any.whl"},
    {"name":"wheel", "version":"0.45.1", "filename":"wheel-0.45.1-py3-none-any.whl"},
]
NATIVE_PROJECT = {
    "pyproject.toml": '[build-system]\nrequires=["setuptools==80.9.0", "wheel==0.45.1"]\nbuild-backend="setuptools.build_meta"\n',
    "setup.py": 'from setuptools import setup, Extension\nsetup(name="forestry-build-fixture",version="1.0",ext_modules=[Extension("forestry_build_fixture",["answer.c"])])\n',
    "answer.c": '''#include <Python.h>
static PyObject *answer(PyObject *self, PyObject *args) { return PyLong_FromLong(42); }
static PyMethodDef methods[] = {{"answer", answer, METH_NOARGS, "Fixture value"}, {NULL, NULL, 0, NULL}};
static struct PyModuleDef definition = {PyModuleDef_HEAD_INIT, "forestry_build_fixture", NULL, -1, methods};
PyMODINIT_FUNC PyInit_forestry_build_fixture(void) { return PyModule_Create(&definition); }
''',
}
PROJECT = {
    "pyproject.toml": '[build-system]\nrequires=["setuptools==80.9.0"]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n',
    "fixture_backend.py": 'from setuptools import build_meta\ndef get_requires_for_build_wheel(config_settings=None):\n    return ["wheel==0.45.1"]\nbuild_wheel=build_meta.build_wheel\nprepare_metadata_for_build_wheel=build_meta.prepare_metadata_for_build_wheel\n',
    "setup.py": 'from setuptools import setup\nsetup(name="forestry-build-fixture",version="1.0",py_modules=["forestry_build_fixture"])\n',
    "forestry_build_fixture.py": 'def answer():\n    return 42\n',
}


def host_packages():
    packages = sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())
    return hashlib.sha256(json.dumps(packages).encode()).hexdigest()


def write_tar(path, members):
    with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type, info.linkname = tarfile.SYMTYPE, "../../outside"
                archive.addfile(info)
            else:
                data = content.encode() if isinstance(content, str) else content
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/app.wsl.example.yaml")
    parser.add_argument("--source", choices=["pypi", "tuna"], default="tuna")
    args = parser.parse_args()
    trial = str(uuid4())
    directory = ROOT/"workspace"/"source-build-trials"/trial
    directory.mkdir(parents=True)
    config = load_config(args.config).model_copy(update={"workspace_root":directory/"runtime"})
    evidence = {"trial_id":trial, "model_used":False, "autonomous_model_source_build":False,
        "scientific_accuracy":"not_assessed", "general_stage_acceptance":"incomplete", "all_passed":False, "cases":[]}
    output = ROOT/"workspace"/"evidence"/("source-build-real-"+trial+".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    before_host = host_packages()
    source = directory/"fixture.tar.gz"
    write_tar(source, [("fixture/"+name, content) for name, content in PROJECT.items()])
    invalid = {
        "traversal.tar.gz":[("../escaped.py", "x")],
        "absolute.tar.gz":[("/escaped.py", "x")],
        "backslash.tar.gz":[("fixture\\escaped.py", "x")],
        "link.tar.gz":[("fixture/link", None)],
        "duplicate.tar.gz":[("fixture/file", "x"), ("fixture/./file", "y")],
        "ambiguous.tar.gz":[("one/setup.py", "x"), ("two/setup.py", "x")],
    }
    paths = [source]
    native_source = directory/"native.tar.gz"
    write_tar(native_source, [("fixture/"+name, content) for name, content in NATIVE_PROJECT.items()])
    paths.append(native_source)
    zip_source = directory/"fixture.zip"
    with zipfile.ZipFile(zip_source, "w") as archive:
        for name, content in PROJECT.items():
            archive.writestr("fixture/"+name, content)
    paths.append(zip_source)
    for name, members in invalid.items():
        path = directory/name
        write_tar(path, members)
        paths.append(path)
    corrupt = directory/"corrupt.tar.gz"
    data = bytearray(source.read_bytes())
    data[-8] ^= 1
    corrupt.write_bytes(data)
    paths.append(corrupt)
    duplicate_zip = directory/"duplicate.zip"
    with zipfile.ZipFile(duplicate_zip, "w") as archive:
        archive.writestr("fixture/file", "x")
        archive.writestr("fixture/./file", "y")
    paths.append(duplicate_zip)
    excessive_zip = directory/"excessive.zip"
    with zipfile.ZipFile(excessive_zip, "w") as archive:
        for index in range(30001):
            archive.writestr(f"fixture/d{index}/", "")
    paths.append(excessive_zip)
    oversized_zip = directory/"oversized.zip"
    with zipfile.ZipFile(oversized_zip, "w") as archive:
        archive.writestr("fixture/file", "x")
    data = bytearray(oversized_zip.read_bytes())
    central = data.index(b'PK\x01\x02')
    struct.pack_into('<I', data, 22, 512*1024*1024+1)
    struct.pack_into('<I', data, central+24, 512*1024*1024+1)
    oversized_zip.write_bytes(data)
    paths.append(oversized_zip)
    nul_zip = directory/"nul.zip"
    with zipfile.ZipFile(nul_zip, "w") as archive:
        archive.writestr("fixture/zeroXevil.py", "x")
    nul_zip.write_bytes(nul_zip.read_bytes().replace(b"zeroXevil", b"zero\x00evil"))
    paths.append(nul_zip)
    link_zip = directory/"link.zip"
    with zipfile.ZipFile(link_zip, "w") as archive:
        info = zipfile.ZipInfo("fixture/link")
        info.create_system, info.external_attr = 3, (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../../outside")
    paths.append(link_zip)
    hashes = {path.name:file_hash(path) for path in paths}
    app = create_app(config, enable_agent=False)
    engine, jobs = app.state.engine, app.state.engine.jobs
    owned = []
    with TestClient(app, base_url="http://127.0.0.1", headers={"origin":"http://127.0.0.1"}) as client:
        client.get("/session").raise_for_status()
        refs = {}
        for path in paths:
            reply = client.post("/resources/register", json={"request_id":str(uuid4()), "path":str(path), "classification":"private", "read_only":True})
            reply.raise_for_status()
            refs[path.name] = reply.json()["resource_id"]
        reply = client.post("/tasks", json={"request_id":str(uuid4()), "objective":"受限源码构建、失败修复、两次复用及归档故障检查；仅软件夹具",
            "input_refs":list(refs.values()), "constraints":{"allowed_capabilities":["environment.acquire", "environment.prepare", "code.execute", "job.observe", "artifact.read"]}})
        reply.raise_for_status()
        task_id = reply.json()["task_id"]
        evidence["task_id"] = task_id
        deps = Deps(engine, task_id, "source-build-smoke-"+trial, 1)
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

        def prepare(name, wheel_ids, scenario, expected="succeeded"):
            ref = refs[name]
            reply = engine.perform(deps, scenario, "environment.prepare", {"input_refs":[ref], "source_refs":[ref], "wheel_artifact_ids":wheel_ids},
                lambda action:engine.environments.prepare(task_id, 1, action, [], source_refs=[ref], wheel_artifact_ids=wheel_ids))
            return observe(reply, scenario, expected)

        try:
            diagnostics = """import json, shutil, subprocess, sys
from pathlib import Path
import pip
compiler=shutil.which('gcc')
result=subprocess.run([compiler,'--version'],capture_output=True,text=True,check=False) if compiler else None
Path('/output/build-target.json').write_text(json.dumps(dict(python=sys.version,pip=pip.__version__,gcc=compiler,gcc_version=result.stdout if result else None)),encoding='utf-8')
"""
            probe = observe(engine.perform(deps, "diagnostics", "code.execute", {"input_refs":[]},
                lambda action:jobs.submit(task_id, 1, action, diagnostics, [], 30)), "compiler_diagnostics")
            evidence["target"] = json.loads(engine.resources.read_artifact(task_id, probe["job"]["artifact_ids"][0])["text"])
            failed = prepare(source.name, [], "missing_build_dependencies", "failed")
            assert "setuptools" in json.dumps(failed["logs"]) and not failed["job"]["artifact_ids"]
            for name in [*invalid, corrupt.name, duplicate_zip.name, excessive_zip.name, oversized_zip.name, nul_zip.name, link_zip.name]:
                prepare(name, [], "reject_"+name, "failed")

            def acquire(call, approval=None):
                return engine.perform(deps, call, "environment.acquire", {"wheels":BUILD_WHEELS, "source":args.source, "approval_id":approval},
                    lambda action:engine.acquisition.acquire(deps, action, BUILD_WHEELS, args.source, approval))
            proposal = acquire("build-dependency-proposal")
            evidence["proposal"] = proposal
            assert proposal["kind"] == "approval_required", proposal
            grant = engine.network.list(task_id)[-1]
            client.post(f"/tasks/{task_id}/approvals/{grant['approval_id']}", json={"request_id":str(uuid4()),
                "expected_spec_revision":1, "request_sha256":grant["request_sha256"], "verdict":"approve"}).raise_for_status()
            acquired = acquire("approved-build-dependencies", grant["approval_id"])
            evidence["acquisition"] = acquired
            assert acquired["kind"] == "completed", acquired
            setuptools = [w["artifact_id"] for w in acquired["wheels"] if w["name"] == "setuptools"]
            dynamic_failure = prepare(source.name, setuptools, "missing_dynamic_build_dependency", "failed")
            assert "wheel==0.45.1" in json.dumps(dynamic_failure["logs"])
            native = prepare(native_source.name, acquired["wheel_artifact_ids"], "native_compiler_condition",
                "succeeded" if evidence["target"]["gcc"] else "failed")
            if not evidence["target"]["gcc"]:
                assert "gcc" in json.dumps(native["logs"])
            zipped = prepare(zip_source.name, acquired["wheel_artifact_ids"], "build_zip_source_and_install")
            ready = prepare(source.name, acquired["wheel_artifact_ids"], "build_tar_source_and_install")
            observed = engine.perform(deps, "observe-build", "job.observe", {"job_id":ready["job"]["job_id"]},
                lambda _:jobs.observation(ready["job"]["job_id"], task_id))
            archive = observed["prepared_environment"]["artifact_id"]
            environment = engine.environments.resolve(task_id, 1, archive)
            evidence["environment"] = environment
            results = []
            for index in range(2):
                code = f'''import importlib.metadata,json,os,socket
from pathlib import Path
import forestry_build_fixture as fixture
try:
    socket.create_connection(('1.1.1.1',443),timeout=1)
    blocked=False
except OSError:
    blocked=True
Path('/output/answer-{index}.json').write_text(json.dumps(dict(answer=fixture.answer(),version=importlib.metadata.version('forestry-build-fixture'),uid=os.getuid(),network_blocked=blocked)),encoding='utf-8')
'''
                computed = observe(engine.perform(deps, "reuse-"+str(index), "code.execute", {"environment_artifact_id":archive, "input_refs":[]},
                    lambda action, code=code:jobs.submit(task_id, 1, action, code, [], 30, environment_artifact_id=archive)), "reuse_"+str(index))
                readback = engine.perform(deps, "read-"+str(index), "artifact.read", {"artifact_id":computed["job"]["artifact_ids"][0]},
                    lambda _, case=computed:engine.resources.read_artifact(task_id, case["job"]["artifact_ids"][0]))
                computed["readback"], computed["numbers"] = readback, json.loads(readback["text"])
                results.append(computed)
            built = environment["manifest"]["built_wheels"]
            checks = {
                "missing_dependency_failure_preserved":failed["job"]["status"] == "failed",
                "missing_dynamic_dependency_failure_preserved":dynamic_failure["job"]["status"] == "failed",
                "compiler_condition_is_explicit":native["job"]["status"] == ("succeeded" if evidence["target"]["gcc"] else "failed"),
                "zip_source_build_succeeded":zipped["job"]["status"] == "succeeded",
                "archive_faults_rejected_before_pip":all(c["job"]["status"] == "failed" and "Processing " not in json.dumps(c["logs"]) for c in evidence["cases"] if c["scenario"].startswith("reject_")),
                "approved_hashed_build_dependencies":len(acquired["wheels"]) == 2 and all(g["status"] == "consumed" for g in engine.network.list(task_id)),
                "built_wheel_and_source_provenance":len(built) == 1 and built[0]["source_sha256"] == hashes[source.name] and environment["manifest"]["sources"] == ready["job"]["source_recipe"],
                "native_pep517_isolation":environment["manifest"]["build_frontend"]["isolation"] == "native_pep517",
                "two_exact_real_readbacks":all(c["numbers"] == {"answer":42, "version":"1.0", "uid":65534, "network_blocked":True} for c in results),
                "same_environment_and_image":all(c["job"]["environment"]["artifact_id"] == archive and c["container"]["Image"] == environment["image_id"] for c in results),
                "no_private_inputs_for_diagnostics":probe["job"]["mounts"] == [],
                "all_containers_restricted":all(c["container"]["HostConfig"]["NetworkMode"] == "none" and c["container"]["HostConfig"]["ReadonlyRootfs"] and "ALL" in c["container"]["HostConfig"]["CapDrop"] and c["container"]["Config"]["User"] == "65534:65534" for c in evidence["cases"]),
                "readonly_source_archives_unchanged":all(file_hash(path) == hashes[path.name] for path in paths),
                "host_packages_unchanged":host_packages() == before_host,
            }
            evidence.update(checks=checks, all_passed=all(checks.values()))
        except Exception as error:
            logger.exception("Real source trial failed; original evidence retained")
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
            evidence["all_passed"] &= all(case["confirmed_terminal"] for case in cleanup)
            evidence["state"], evidence["events"] = engine.store.describe_task(task_id), engine.store.events(task_id, limit=1000)
            output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(output, flush=True)
    return 0 if evidence["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
