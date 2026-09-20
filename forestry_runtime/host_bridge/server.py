"""Local Windows bridge for authorized file access and Docker job control.

The bridge never executes commands on the Windows host. Code and shell actions
run only in constrained Docker containers.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlparse


ID = re.compile(r"^[a-z][a-z0-9_-]{7,95}$")
TEXT_LIMIT = 256 * 1024


def dependency_install_command(packages: list[str]) -> list[str]:
    script = (
        "import importlib.metadata as m,json,pathlib,subprocess,sys;"
        "target='/deps';"
        "subprocess.check_call([sys.executable,'-m','pip','install','--target',target,*sys.argv[1:]]);"
        "rows=sorted(({'name':d.metadata.get('Name') or d.name,'version':d.version} "
        "for d in m.distributions(path=[target])),key=lambda x:(x['name'].casefold(),x['version']));"
        "pathlib.Path(target,'installed-packages.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')"
    )
    return ["python", "-c", script, *packages]


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
        timeout=45,
    )


def safe_relative(root: Path, relative: str, *, exists: bool = False) -> Path:
    pure = PurePosixPath(str(relative or ".").replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("relative path leaves authorized root")
    target = (root / Path(*[p for p in pure.parts if p not in ("", ".")])).resolve()
    if target != root and root not in target.parents:
        raise ValueError("resolved path leaves authorized root")
    if exists and not target.exists():
        raise ValueError("path does not exist")
    return target


class Service:
    def __init__(self):
        self.key = os.environ.get("HOST_BRIDGE_KEY", "")
        if len(self.key) < 24:
            raise RuntimeError("HOST_BRIDGE_KEY must contain at least 24 characters")
        configured = os.environ.get("RUNTIME_DATA_HOST_ROOT", "")
        if not configured:
            raise RuntimeError("RUNTIME_DATA_HOST_ROOT is required")
        self.data_root = Path(configured).resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.image = os.environ.get("AGENT_JOB_IMAGE", "python:3.12-slim")
        self.cpu = os.environ.get("AGENT_JOB_CPUS", "4")
        self.memory = os.environ.get("AGENT_JOB_MEMORY", "6g")
        self.max_jobs = int(os.environ.get("AGENT_MAX_ACTIVE_JOBS", "1"))
        self.pids = os.environ.get("AGENT_JOB_PIDS", "256")

    def enforce_deadlines(self) -> int:
        """Stop managed containers whose persisted wall-clock deadline passed."""
        listed = docker(
            "ps", "--filter", "label=forestry.runtime.managed=true",
            "--format", "{{.Names}}", check=False,
        )
        stopped = 0
        for name in [line.strip() for line in listed.stdout.splitlines() if line.strip()]:
            try:
                info = json.loads(docker("inspect", name).stdout)[0]
                labels = info.get("Config", {}).get("Labels", {}) or {}
                deadline = int(labels.get("forestry.runtime.deadline", "0") or 0)
                if deadline and time.time() > deadline and info.get("State", {}).get("Running"):
                    docker("stop", "--time", "10", name, check=False)
                    stopped += 1
            except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError, ValueError):
                continue
        return stopped

    def watchdog(self, interval_seconds: int = 15) -> None:
        while True:
            try:
                self.enforce_deadlines()
            except Exception as exc:
                print(f"Job watchdog error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(interval_seconds)

    def authorize(self, header: str) -> None:
        if not hmac.compare_digest(header, "Bearer " + self.key):
            raise PermissionError("invalid bridge key")

    def verify_grant(self, body: dict, *, write: bool = False) -> Path:
        grant_id = str(body.get("grant_id") or "")
        if not re.fullmatch(r"grant_[0-9a-f]{32}", grant_id):
            raise PermissionError("invalid source grant id")
        root = Path(str(body.get("root") or "")).resolve(strict=True)
        access = str(body.get("access") or "read")
        if write and access != "write":
            raise PermissionError("source grant is read-only")
        message = f"{body.get('chat_id', '')}\n{root}\n{access}".encode("utf-8")
        expected = hmac.new(self.key.encode("utf-8"), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(body.get("grant_token") or ""), expected):
            raise PermissionError("invalid source grant token")
        database = self.data_root / "workspaces.sqlite3"
        if not database.is_file():
            raise PermissionError("source grant registry is unavailable")
        conn = sqlite3.connect(database, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT chat_id,host_path,access,active FROM grants WHERE id=?",
                (grant_id,),
            ).fetchone()
        finally:
            conn.close()
        if (
            row is None or not bool(row["active"])
            or not hmac.compare_digest(str(row["chat_id"]), str(body.get("chat_id") or ""))
            or not hmac.compare_digest(str(row["access"]), access)
            or Path(str(row["host_path"])).resolve() != root
        ):
            raise PermissionError("source grant is missing, revoked, or does not match")
        return root

    def resolve(self, body: dict) -> dict:
        target = Path(str(body.get("path") or "")).expanduser().resolve(strict=True)
        return {
            "path": str(target),
            "type": "directory" if target.is_dir() else "file",
            "size": target.stat().st_size if target.is_file() else None,
        }

    def fs_list(self, body: dict) -> dict:
        root = self.verify_grant(body)
        target = safe_relative(root, body.get("path", "."), exists=True)
        if not target.is_dir():
            raise ValueError("path is not a directory")
        page = max(1, int(body.get("page", 1)))
        size = min(200, max(1, int(body.get("page_size", 100))))
        rows = []
        for item in sorted(target.iterdir(), key=lambda p: p.name.casefold()):
            stat = item.stat()
            rows.append({
                "name": item.name,
                "path": item.relative_to(root).as_posix(),
                "type": "directory" if item.is_dir() else "file",
                "size": stat.st_size if item.is_file() else None,
                "modified": stat.st_mtime,
            })
        start = (page - 1) * size
        return {"items": rows[start:start + size], "page": page, "page_size": size, "total": len(rows), "has_more": start + size < len(rows)}

    def fs_read(self, body: dict) -> dict:
        root = self.verify_grant(body)
        target = safe_relative(root, body.get("path", ""), exists=True)
        if not target.is_file():
            raise ValueError("path is not a file")
        start_line = max(1, int(body.get("start_line", 1)))
        max_lines = min(2000, max(1, int(body.get("max_lines", 200))))
        max_chars = min(TEXT_LIMIT, max(1, int(body.get("max_chars", 32000))))
        try:
            with target.open("r", encoding="utf-8") as handle:
                lines = []
                for number, line in enumerate(handle, 1):
                    if number < start_line:
                        continue
                    if len(lines) >= max_lines:
                        break
                    lines.append(line)
                    if sum(map(len, lines)) >= max_chars:
                        break
        except UnicodeDecodeError as exc:
            return {"path": target.relative_to(root).as_posix(), "text": False, "error": f"binary or non-UTF-8 file: {exc}"}
        text = "".join(lines)[:max_chars]
        return {"path": target.relative_to(root).as_posix(), "text": True, "start_line": start_line, "content": text, "truncated": len(text) >= max_chars or len(lines) >= max_lines}

    def fs_search(self, body: dict) -> dict:
        root = self.verify_grant(body)
        target = safe_relative(root, body.get("path", "."), exists=True)
        query = str(body.get("query") or "")
        if not query:
            raise ValueError("query is required")
        pattern = str(body.get("glob") or "*")
        limit = min(200, max(1, int(body.get("max_results", 50))))
        matches = []
        paths = [target] if target.is_file() else target.rglob("*")
        for item in paths:
            if len(matches) >= limit or not item.is_file() or not fnmatch.fnmatch(item.name, pattern):
                continue
            if item.stat().st_size > 4 * 1024 * 1024:
                continue
            try:
                with item.open("r", encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, 1):
                        if query.casefold() in line.casefold():
                            matches.append({"path": item.relative_to(root).as_posix(), "line": line_no, "text": line.rstrip()[:500]})
                            if len(matches) >= limit:
                                break
            except (UnicodeDecodeError, OSError):
                continue
        return {"matches": matches, "truncated": len(matches) >= limit}

    def fs_edit(self, body: dict) -> dict:
        target = self.verify_grant(body, write=True)
        if not target.is_file():
            raise ValueError("write grant does not identify one file")
        old = str(body.get("old") or "")
        new = str(body.get("new") or "")
        replace_all = bool(body.get("replace_all", False))
        if not old:
            raise ValueError("old text is required")
        content = target.read_text(encoding="utf-8")
        count = content.count(old)
        if count == 0:
            raise ValueError("exact text was not found")
        if count > 1 and not replace_all:
            raise ValueError(f"exact text occurs {count} times")
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        target.write_text(updated, encoding="utf-8")
        return {"path": str(target), "replacements": count if replace_all else 1, "size": target.stat().st_size}

    def _workspace(self, raw: str) -> Path:
        path = Path(raw).resolve(strict=True)
        if path != self.data_root and self.data_root not in path.parents:
            raise PermissionError("workspace is outside runtime-owned data root")
        return path

    def _container(self, job_id: str) -> str:
        if not ID.fullmatch(job_id):
            raise ValueError("invalid job id")
        return "forestry-" + job_id

    def job_start(self, body: dict) -> dict:
        job_id = str(body.get("job_id") or "")
        name = self._container(job_id)
        workspace = self._workspace(str(body.get("workspace") or ""))
        existing = docker("ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
        if existing.stdout.strip() == name:
            return self.job_status({"job_id": job_id, "offset": body.get("offset", 0)}) | {"already_exists": True}
        running = docker("ps", "--filter", "label=forestry.runtime.managed=true", "--format", "{{.Names}}")
        active = [line for line in running.stdout.splitlines() if line.strip()]
        if len(active) >= self.max_jobs:
            raise ValueError(f"active job limit reached ({self.max_jobs})")

        kind = str(body.get("kind") or "python")
        timeout_seconds = min(14400, max(1, int(body.get("timeout_seconds", 14400))))
        command: list[str]
        mounts: list[tuple[Path, str, str]] = []
        if kind == "install":
            deps = safe_relative(workspace, ".runtime/deps", exists=True)
            mounts.append((deps, "/deps", "rw"))
            packages = body.get("packages") or []
            if not isinstance(packages, list) or not packages or len(packages) > 20:
                raise ValueError("packages must contain 1-20 entries")
            for package in packages:
                if not re.fullmatch(r"[A-Za-z0-9_.\-\[\],<>=!~]+", str(package)):
                    raise ValueError("invalid package requirement")
            command = dependency_install_command(list(map(str, packages)))
            network = str(body.get("network") or "bridge")
        else:
            mounts.append((workspace, "/workspace", "rw"))
            relative = str(body.get("action_path") or "")
            action = safe_relative(workspace, relative, exists=True)
            action_inside = "/workspace/" + action.relative_to(workspace).as_posix()
            if kind == "python":
                command = ["python", action_inside]
            elif kind == "shell":
                command = ["/bin/sh", action_inside]
            else:
                raise ValueError("job kind must be python, shell, or install")
            network = "none"
            for index, source in enumerate(body.get("sources") or []):
                source_root = self.verify_grant(source)
                mounts.append((source_root, f"/sources/{source['id']}", "ro"))

        args = [
            "run", "-d", "--name", name,
            "--label", "forestry.runtime.managed=true",
            "--label", f"forestry.runtime.job_id={job_id}",
            "--label", f"forestry.runtime.chat_id={str(body.get('chat_id') or '')[:100]}",
            "--label", f"forestry.runtime.deadline={int(time.time()) + timeout_seconds}",
            "--cpus", self.cpu, "--memory", self.memory,
            "--pids-limit", self.pids,
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--init",
            "--user", "10001:10001", "--tmpfs", "/tmp:rw,nosuid,size=536870912",
            "--network", network,
            "-e", "PYTHONPATH=/workspace/.runtime/deps", "-e", "HOME=/tmp",
        ]
        for host, container, mode in mounts:
            args.extend(["--mount", f"type=bind,source={host},target={container},readonly" if mode == "ro" else f"type=bind,source={host},target={container}"])
        if kind != "install":
            args.extend(["--workdir", "/workspace"])
        args.extend([self.image, *command])
        result = docker(*args)
        return {"job_id": job_id, "container_id": result.stdout.strip(), "state": "running", "offset": 0}

    def job_status(self, body: dict) -> dict:
        job_id = str(body.get("job_id") or "")
        name = self._container(job_id)
        try:
            raw = docker("inspect", name).stdout
            info = json.loads(raw)[0]
        except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError) as exc:
            raise ValueError("managed job container does not exist") from exc
        state = info["State"]
        deadline = int(info.get("Config", {}).get("Labels", {}).get("forestry.runtime.deadline", "0") or 0)
        timed_out = bool(state.get("Running") and deadline and time.time() > deadline)
        if timed_out:
            docker("stop", "--time", "10", name, check=False)
            info = json.loads(docker("inspect", name).stdout)[0]
            state = info["State"]
        logs = docker("logs", name, check=False).stdout + docker("logs", name, check=False).stderr
        offset = max(0, int(body.get("offset", 0)))
        chunk = logs[offset:offset + 65536]
        if state.get("Running"):
            normalized = "running"
        elif timed_out:
            normalized = "timed_out"
        elif state.get("ExitCode") == 0:
            normalized = "succeeded"
        else:
            normalized = "failed"
        return {
            "job_id": job_id,
            "state": normalized,
            "terminal": normalized != "running",
            "exit_code": None if state.get("Running") else state.get("ExitCode"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "output": chunk,
            "offset": offset + len(chunk),
            "has_more_output": offset + len(chunk) < len(logs),
        }

    def job_cancel(self, body: dict) -> dict:
        job_id = str(body.get("job_id") or "")
        name = self._container(job_id)
        docker("stop", "--time", "10", name, check=False)
        status = self.job_status({"job_id": job_id, "offset": body.get("offset", 0)})
        return status | {"canceled": True, "state": "canceled", "terminal": True}

    def job_cleanup(self, body: dict) -> dict:
        job_id = str(body.get("job_id") or "")
        name = self._container(job_id)
        docker("rm", "-f", name, check=False)
        return {"job_id": job_id, "removed": True}


class Handler(BaseHTTPRequestHandler):
    service: Service

    def log_message(self, format, *args):
        print(format % args, flush=True)

    def reply(self, status: int, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        try:
            self.service.authorize(self.headers.get("Authorization", ""))
            if urlparse(self.path).path != "/health":
                raise ValueError("unknown endpoint")
            self.reply(200, {"status": "ok", "data_root": str(self.service.data_root), "job_image": self.service.image})
        except PermissionError as exc:
            self.reply(401, {"error": str(exc)})
        except Exception as exc:
            self.reply(400, {"error": str(exc)})

    def do_POST(self):
        try:
            self.service.authorize(self.headers.get("Authorization", ""))
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024 * 1024:
                raise ValueError("request body too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            route = urlparse(self.path).path
            routes = {
                "/resolve": self.service.resolve,
                "/fs/list": self.service.fs_list,
                "/fs/read": self.service.fs_read,
                "/fs/search": self.service.fs_search,
                "/fs/edit": self.service.fs_edit,
                "/jobs/start": self.service.job_start,
                "/jobs/status": self.service.job_status,
                "/jobs/cancel": self.service.job_cancel,
                "/jobs/cleanup": self.service.job_cleanup,
            }
            if route not in routes:
                raise ValueError("unknown endpoint")
            self.reply(200, routes[route](body))
        except PermissionError as exc:
            self.reply(403, {"error": str(exc)})
        except subprocess.CalledProcessError as exc:
            self.reply(500, {"error": (exc.stderr or exc.stdout or str(exc))[-4000:]})
        except Exception as exc:
            self.reply(400, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    Handler.service = Service()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    threading.Thread(target=Handler.service.watchdog, name="job-deadline-watchdog", daemon=True).start()
    print(f"Forestry host bridge listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
