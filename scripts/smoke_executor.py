"""Exercise the real restricted executor; no model and no fixture Docker backend."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from forestry_agent.agent import Deps
from forestry_agent.app import create_app
from forestry_agent.config import load_config
from forestry_agent.jobs import TERMINAL, Docker, Jobs

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    evidence = ROOT / "workspace" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    trial = str(uuid4())
    source = config.workspace_root / "fixtures" / (trial + ".csv")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"height_m\n8.25\n12.50\n16.75\n")
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    app = create_app(config, enable_agent=False)
    outcomes = []
    docker = Docker(config)
    with TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}) as client:
        client.get("/session").raise_for_status()
        grant = client.post("/resources/register", json={"request_id": str(uuid4()), "path": str(source),
            "classification": "private", "read_only": True})
        grant.raise_for_status()
        ref = grant.json()["resource_id"]

        def submit(code, seconds=60):
            response = client.post("/tasks", json={"request_id": str(uuid4()), "objective": "真实受限执行器测试",
                "input_refs": [ref], "constraints": {"allowed_capabilities": ["code.execute", "job.observe"]}})
            assert response.status_code == 202, response.text
            response.raise_for_status()
            task_id = response.json()["task_id"]
            deps = Deps(app.state.engine, task_id, "executor-smoke-" + trial, 1)
            result = app.state.engine.perform(deps, str(uuid4()), "code.execute", {"code": code, "input_refs": [ref]},
                lambda action: app.state.engine.jobs.submit(task_id, 1, action, code, [ref], seconds))
            assert result["kind"] == "completed", result
            return task_id, result["job"]["job_id"]

        code = '''import csv, json, os, socket, sys, time
from pathlib import Path
source = Path('/inputs/RESOURCE')
rows = list(csv.DictReader(source.open()))
values = [float(row['height_m']) for row in rows]
try:
    source.write_text('forbidden mutation')
    source_readonly = False
except OSError:
    source_readonly = True
try:
    Path('/etc/forestry-test').write_text('forbidden mutation')
    root_protected = False
except OSError:
    root_protected = True
try:
    with socket.create_connection(('1.1.1.1', 443), timeout=2):
        network_blocked = False
except OSError:
    network_blocked = True
result = dict(tree_count=len(values), total_height_m=sum(values), mean_height_m=sum(values)/len(values),
    uid=os.getuid(), source_readonly=source_readonly, root_protected=root_protected,
    network_blocked=network_blocked, host_files_hidden=not Path('/mnt/c').exists(),
    host_secret_absent='FORESTRY_EXECUTOR_SYNTHETIC_SECRET' not in os.environ, python=sys.version)
print(json.dumps(result), flush=True)
time.sleep(5)
Path('/output/result.json').write_text(json.dumps(result), encoding='utf-8')
'''.replace("RESOURCE", ref)
        task_id, job_id = submit(code)
        manager = app.state.engine.jobs
        manager.tick()
        started = manager.read(job_id)
        container = docker.inspect(started["container_name"])
        assert started["status"] == "running" and container["State"]["Running"], started
        # Fresh application objects/database connection observe the same real container.
        reopened = create_app(config, enable_agent=False)
        reopened.state.engine.reconcile_startup()
        manager = Jobs(config, reopened.state.store, reopened.state.resources)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            manager.tick()
            job = manager.read(job_id)
            if job["status"] in TERMINAL:
                break
            time.sleep(0.5)
        files = [reopened.state.resources.artifact(a, task_id) for a in job["artifact_ids"]]
        numbers = next((json.loads(Path(a["locator"]).read_text()) for a in files if a["name"] == "result.json"), {})
        host = container["HostConfig"]
        checks = {"succeeded": job["status"] == "succeeded", "same_container_after_reopen": job["container_id"] == container["Id"],
            "exact_numbers": all(numbers.get(k) == v for k, v in {"tree_count": 3, "total_height_m": 37.5, "mean_height_m": 12.5}.items()),
            "source_readonly": numbers.get("source_readonly") is True, "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == original,
            "uid_unprivileged": numbers.get("uid") == 65534, "root_protected": numbers.get("root_protected") is True,
            "network_blocked": numbers.get("network_blocked") is True, "host_files_hidden": numbers.get("host_files_hidden") is True,
            "host_secret_absent": numbers.get("host_secret_absent") is True, "network_none": host["NetworkMode"] == "none",
            "read_only_root": host["ReadonlyRootfs"], "capabilities_dropped": "ALL" in host["CapDrop"],
            "memory_frozen": host["Memory"] == config.max_code_memory_bytes and host["MemorySwap"] == config.max_code_memory_bytes,
            "cpu_frozen": host["NanoCpus"] == config.max_code_cpus * 1000000000,
            "pid_limit": host["PidsLimit"] == 128, "real_logs": '"tree_count": 3' in manager.logs(job_id, task_id)["text"],
            "image_identity": container["Image"] == job["image_id"]}
        outcomes.append({"scenario": "restricted_compute_and_manager_reopen", "task_id": task_id, "job": job,
            "container": container, "numbers": numbers, "checks": checks, "passed": all(checks.values())})
        print(json.dumps({"scenario": outcomes[-1]["scenario"], "task_id": task_id, "checks": checks}), flush=True)

        for scenario, seconds in [("cancel", 60), ("timeout", 1)]:
            task_id, job_id = submit("import time\nprint('started', flush=True)\ntime.sleep(60)\n", seconds)
            manager.tick()
            before = manager.read(job_id)
            if scenario == "cancel":
                response = client.post(f"/tasks/{task_id}/cancel", json={"request_id": str(uuid4()),
                    "expected_state_version": app.state.store.task(task_id)["state_version"]})
                response.raise_for_status()
            else:
                time.sleep(1.2)
            manager.tick()
            after = manager.read(job_id)
            container = docker.inspect(after["container_name"])
            checks = {"real_running_before": before["status"] == "running", "terminal": after["status"] == ("cancelled" if scenario == "cancel" else "timed_out"),
                "same_container": container["Id"] == before["container_id"], "actually_stopped": not container["State"]["Running"],
                "no_artifacts": after["artifact_ids"] == []}
            outcomes.append({"scenario": scenario, "task_id": task_id, "before": before, "after": after,
                "container": container, "checks": checks, "passed": all(checks.values())})
            print(json.dumps({"scenario": scenario, "task_id": task_id, "checks": checks}), flush=True)
    path = evidence / ("executor-real-" + trial + ".json")
    path.write_text(json.dumps({"trial": trial, "model_used": False, "real_process_restart": False,
        "scientific_accuracy": "not_assessed", "outcomes": outcomes}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(path), flush=True)
    raise SystemExit(0 if all(x["passed"] for x in outcomes) else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        path = ROOT / "workspace" / "evidence" / ("executor-smoke-failed-" + str(uuid4()) + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"exception": type(error).__name__, "message": str(error), "passed": False},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(path), flush=True)
        raise
