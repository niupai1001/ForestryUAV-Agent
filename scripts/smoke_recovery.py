"""Crash only a service spawned by this trial, reconnect its real container, resume.

No existing development service is stopped. No Docker fixture or host code runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psutil
import yaml

from forestry_agent.client import collect_events
from forestry_agent.config import load_config
from forestry_agent.jobs import Docker

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


class TrialService:
    def __init__(self, config_path, directory):
        self.config_path, self.directory = config_path.resolve(), directory
        self.process = None
        self.stream = None
        self.identities = []

    def start(self):
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("Previous owned process is still alive; do not restart")
        log = self.directory / f"service-{len(self.identities) + 1}.log"
        self.stream = log.open("wb")
        self.process = subprocess.Popen([sys.executable, "-m", "forestry_agent.cli", "serve", "--config", str(self.config_path)],
            cwd=ROOT, stdout=self.stream, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        launched = time.time()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Owned service exited: " + log.read_text(errors="replace")[-4000:])
            match = re.search(r"Started server process \[(\d+)\]", log.read_text(errors="replace"))
            if match:
                process = psutil.Process(int(match.group(1)))
                command = process.cmdline()
                if "forestry_agent.cli" not in command or "serve" not in command or "--config" not in command:
                    raise RuntimeError("Server identity did not match spawned CLI")
                actual = Path(command[command.index("--config") + 1]).resolve()
                tree = {self.process.pid, *(p.pid for p in psutil.Process(self.process.pid).children(recursive=True))}
                if actual != self.config_path or process.pid not in tree or process.create_time() < launched - 2:
                    raise RuntimeError("Server is not a new process owned by this trial")
                identity = {"launcher_pid": self.process.pid, "server_pid": process.pid,
                    "command": command, "created": process.create_time(), "log": str(log)}
                self.identities.append(identity)
                return identity
            time.sleep(0.1)
        raise RuntimeError("Observation expired; owned process remains live and was not restarted")

    def stop(self):
        if not self.process:
            return
        if self.process.poll() is None and self.identities:
            identity = self.identities[-1]
            try:
                process = psutil.Process(identity["server_pid"])
                if process.create_time() != identity["created"] or process.cmdline() != identity["command"]:
                    raise RuntimeError("Refusing to stop a replacement process")
                process.kill()
            except psutil.NoSuchProcess:
                pass
        elif self.process.poll() is None:
            self.process.kill()  # Exact owned Popen handle, before a server was identified.
        self.process.wait(timeout=15)
        self.stream.close()


def wait_health(client, service):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if service.process.poll() is not None:
            raise RuntimeError("Owned service terminated during health observation")
        try:
            response = client.get("/health", timeout=2)
            if response.status_code == 200:
                client.get("/session", timeout=2).raise_for_status()
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError("Health observation expired without restarting the live process")


def trial(model, template):
    identifier = str(uuid4())
    directory = ROOT / "workspace" / "recovery-trials" / identifier
    directory.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    raw = yaml.safe_load(Path(template).read_text(encoding="utf-8"))
    raw.update(port=port, workspace_root=str(directory / "runtime"), default_model=model)
    path = directory / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    config = load_config(path)
    docker = Docker(config)
    service = TrialService(path, directory)
    evidence = {"trial_id": identifier, "model": model, "directory": str(directory), "all_passed": False,
        "real_process_restart": False, "scientific_accuracy": "not_assessed", "general_stage_acceptance": "incomplete"}
    output = ROOT / "workspace" / "evidence" / (f"recovery-real-{model}-{identifier}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    task_id = None
    # The log endpoint performs two bounded Docker calls (inspect and logs).
    # A five-second HTTP read budget could discard a successfully finished trial.
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=httpx.Timeout(70, connect=3), trust_env=False,
            headers={"origin": f"http://127.0.0.1:{port}"}) as client:
        try:
            evidence["phase"] = "initial_service"
            evidence["first_service"] = service.start()
            wait_health(client, service)
            source = directory / "heights.csv"
            source.write_bytes(b"height_m\n8.25\n12.50\n16.75\n")
            original = hashlib.sha256(source.read_bytes()).hexdigest()
            grant = client.post("/resources/register", json={"request_id": str(uuid4()), "path": str(source),
                "classification": "private", "read_only": True})
            grant.raise_for_status()
            ref = grant.json()["resource_id"]
            code = f'''import csv, json, time
from pathlib import Path
print("RESTART_READY", flush=True)
time.sleep(60)
values = [float(row['height_m']) for row in csv.DictReader(Path('/inputs/{ref}').open())]
result = dict(tree_count=len(values), total_height_m=sum(values), mean_height_m=sum(values)/len(values))
Path('/output/recovery-result.json').write_text(json.dumps(result), encoding='utf-8')
print("COMPLETED_ONCE", flush=True)
'''
            response = client.post("/tasks", json={"request_id": str(uuid4()), "model_profile": model,
                "harness_mode": "H1", "input_refs": [ref], "constraints": {"allowed_capabilities": [
                    "code.execute", "job.observe", "artifact.read", "artifact.inspect", "fs.write_workspace", "action.inspect"]},
                "objective": "真实长作业恢复测试。仅提交一次下面的Python代码，用code_execute(seconds=120,input_refs=[已授权CSV资源ID])，"
                    "不得另写代码或重复提交，使用wait_job等待真实完成。过程中服务可能重启，恢复后须继续观察同一job_id。"
                    "实际read_artifact读回recovery-result.json，再保存中文recovery-report.md记录真实作业和结果及软件夹具局限，"
                    "实际读回报告并final_result引用两个真实产物ID，等待用户验收。\n\n" + code})
            response.raise_for_status()
            task_id = response.json()["task_id"]
            evidence["task_id"] = task_id
            evidence["phase"] = "waiting_for_container"
            print(json.dumps({"model": model, "task_id": task_id, "phase": "created"}), flush=True)
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                events = collect_events(client, task_id)
                queued = [e for e in events if e["type"] == "job.queued"]
                if queued:
                    assert len(queued) == 1, "More than one job before interruption"
                    job_id = queued[0]["payload"]["job_id"]
                    job = client.get(f"/tasks/{task_id}/jobs/{job_id}").json()
                    if job["status"] == "running":
                        logs = client.get(f"/tasks/{task_id}/jobs/{job_id}/logs").json()
                        if "RESTART_READY" in logs.get("text", ""):
                            break
                state = client.get(f"/tasks/{task_id}").json()
                if state["status"] in {"paused", "failed", "cancelled", "waiting_user"}:
                    raise RuntimeError("Model did not enter the real long job: " + json.dumps(state["blockers"]))
                time.sleep(0.5)
            else:
                raise RuntimeError("Long job observation expired; no new process was started")
            evidence.update(before_state=client.get(f"/tasks/{task_id}").json(), before_events=events, before_job=job,
                before_container=docker.inspect(job["container_name"]), before_logs=logs)
            service.stop()  # Kill only the process identity created and verified above.
            evidence["phase"] = "service_stopped"
            evidence["crashed_launcher_exit_code"] = service.process.returncode
            evidence["container_after_crash"] = docker.inspect(job["container_name"])
            assert evidence["container_after_crash"]["State"]["Running"], "Container must outlive the crashed service"
            evidence["second_service"] = service.start()
            wait_health(client, service)
            evidence["real_process_restart"] = True
            evidence["phase"] = "service_restarted"
            paused = client.get(f"/tasks/{task_id}").json()
            evidence["after_restart_state"] = paused
            assert paused["status"] == "paused", paused
            assert any(b["code"] == "SERVICE_RESTARTED" for b in paused["blockers"]), paused
            resume = client.post(f"/tasks/{task_id}/resume", json={"request_id": str(uuid4()),
                "expected_state_version": paused["state_version"]})
            resume.raise_for_status()
            evidence["resume_receipt"] = resume.json()
            evidence["phase"] = "resumed"
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                state = client.get(f"/tasks/{task_id}").json()
                if state["status"] in {"waiting_user", "paused", "failed", "cancelled", "completed"}:
                    break
                time.sleep(0.5)
            events = collect_events(client, task_id)
            evidence["phase"] = "verifying_outputs"
            artifacts = client.get(f"/tasks/{task_id}/artifacts").json()
            job_after = client.get(f"/tasks/{task_id}/jobs/{job_id}").json()
            logs_after = client.get(f"/tasks/{task_id}/jobs/{job_id}/logs").json()
            numeric = next((a for a in artifacts if a["name"] == "recovery-result.json"), None)
            report = next((a for a in artifacts if a["name"] == "recovery-report.md"), None)
            numbers = client.get(numeric["preview_url"]).json() if numeric else None
            confirmed_reads = {e["payload"]["result"].get("action_id") for e in events
                if e["type"] == "action.observed" and e["payload"]["tool"] == "artifact.read"
                and e["payload"]["result"].get("kind") == "completed"}
            read_artifacts = {e["payload"]["arguments"]["artifact_id"] for e in events
                if e["type"] == "action.started" and e["payload"]["tool"] == "artifact.read"
                and e["payload"]["action_id"] in confirmed_reads}
            checks = {"new_service_identity": (evidence["first_service"]["server_pid"], evidence["first_service"]["created"]) !=
                    (evidence["second_service"]["server_pid"], evidence["second_service"]["created"]),
                "container_survived": evidence["container_after_crash"]["Id"] == evidence["before_container"]["Id"],
                "same_container_completed": job_after["container_id"] == job["container_id"] and job_after["status"] == "succeeded",
                "one_job": sum(e["type"] == "job.queued" for e in events) == 1,
                "one_code_action": sum(e["type"] == "action.started" and e["payload"]["tool"] == "code.execute" for e in events) == 1,
                "one_completion": logs_after["text"].count("COMPLETED_ONCE") == 1,
                "waiting_user": state["status"] == "waiting_user", "revision_preserved": state["spec_revision"] == 1,
                "exact_numbers": numbers == {"tree_count": 3, "total_height_m": 37.5, "mean_height_m": 12.5},
                "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == original,
                "report_exists": report is not None,
                "result_readback": numeric is not None and numeric["artifact_id"] in read_artifacts,
                "report_readback": report is not None and report["artifact_id"] in read_artifacts,
                "task_budget_retained": state["model_requests"] >= evidence["before_state"]["model_requests"]}
            evidence.update(state=state, events=events, artifacts=artifacts, after_job=job_after, after_logs=logs_after,
                numbers=numbers, checks=checks, all_passed=all(checks.values()))
            evidence["phase"] = "verified"
            print(json.dumps({"model": model, "task_id": task_id, "checks": checks}), flush=True)
        except Exception as error:
            logger.exception("Real recovery trial failed")
            evidence["error"] = {"type": type(error).__name__, "message": str(error)}
            if task_id and service.process and service.process.poll() is None:
                try:
                    evidence.update(state=client.get(f"/tasks/{task_id}").json(), events=collect_events(client, task_id))
                except httpx.HTTPError:
                    pass
            print(json.dumps({"model": model, "task_id": task_id, "error": evidence["error"]}), flush=True)
        finally:
            try:
                service.stop()
            except (subprocess.TimeoutExpired, RuntimeError, OSError) as error:
                logger.exception("Owned service cleanup could not be confirmed")
                evidence["cleanup_error"] = {"type": type(error).__name__, "message": str(error)}
                evidence["all_passed"] = False
            output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(str(output), flush=True)
    return evidence["all_passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=["small", "large"], default=["small", "large"])
    parser.add_argument("--config", default="config/app.wsl.example.yaml")
    args = parser.parse_args()
    outcomes = [trial(model, args.config) for model in args.models]
    raise SystemExit(0 if all(outcomes) else 1)
