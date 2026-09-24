"""Local Windows bridge for authorized file access and Docker job control.

The bridge never executes commands on the Windows host. Code and shell actions
run only in constrained Docker containers.
"""
from __future__ import annotations

import argparse
import datetime
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
import sys
import threading
import time
from urllib.parse import urlparse

# Works both when started as a script (`python host_bridge/server.py`, which puts
# this directory on sys.path) and when imported as `host_bridge.server`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.paths import is_within


ID = re.compile(r"^[a-z][a-z0-9_-]{7,95}$")
TEXT_LIMIT = 256 * 1024
ENVIRONMENT_MARKER = "RUNTIME_ENVIRONMENT_JSON:"


class BridgeError(Exception):
    """Failure that carries a machine-readable code for the Runtime to act on."""

    def __init__(self, message: str, *, code: str, retryable: bool = False, **extra):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.extra = extra


def environment_probe_command(modules: list[str]) -> list[str]:
    """Import each module inside the job image and report version + origin.

    Runs in the same image and with the same ``PYTHONPATH`` as a real job, so a
    successful import here is genuine evidence that the module is importable when
    the job runs.  The distribution mapping comes from ``importlib.metadata`` so
    the Runtime can tell which package a module belongs to without guessing.
    """
    script = (
        "import importlib,importlib.metadata as m,json,sys;"
        "mods=sys.argv[1:];"
        "pkgmap={};"
        "dist=[]\n"
        "try:\n"
        "    pkgmap=m.packages_distributions()\n"
        "except Exception:\n"
        "    pkgmap={}\n"
        "versions={}\n"
        "for d in m.distributions():\n"
        "    name=d.metadata.get('Name') or d.name\n"
        "    versions[name.casefold()]=d.version\n"
        "out=[]\n"
        "for name in mods:\n"
        "    entry={'module':name,'importable':False,'error':None,'version':None,"
        "'distributions':sorted(pkgmap.get(name,[])),'origin':None}\n"
        "    try:\n"
        "        mod=importlib.import_module(name)\n"
        "        entry['importable']=True\n"
        "        entry['origin']=getattr(mod,'__file__',None)\n"
        "        v=getattr(mod,'__version__',None)\n"
        "        if not isinstance(v,str):\n"
        "            for d in entry['distributions'] or [name]:\n"
        "                if d.casefold() in versions:\n"
        "                    v=versions[d.casefold()];break\n"
        "        entry['version']=v if isinstance(v,str) else None\n"
        "    except BaseException as exc:\n"
        "        entry['error']=type(exc).__name__+': '+str(exc)[:300]\n"
        "    out.append(entry)\n"
        "sys.stdout.write('\\n'+%r+json.dumps(out,ensure_ascii=False)+'\\n')"
        % ENVIRONMENT_MARKER
    )
    return ["python", "-c", script, *modules]


def dependency_install_command(
    packages: list[str], system_packages: list[str] | None = None,
) -> list[str]:
    """Install Python requirements, and optionally operating-system packages first.

    A Python wheel can need a shared library the wheel cannot supply -- ``rasterio``
    needs ``libexpat.so.1``. pip then reports the install as a success while the
    import fails, which reads to a caller as "the dependency is not installed" and
    invites reinstalling it indefinitely. Adding the operating-system package in the
    same job is what makes the finished image usable by the code jobs that later run
    from it.

    Both steps live in one job so a failure leaves one unambiguous record, and so the
    container committed to an image is the one that had both.
    """
    script = "\n".join([
        "import importlib.metadata as m, json, pathlib, subprocess, sys",
        "target = '/deps'",
        "system_packages = json.loads(sys.argv[1])",
        "packages = json.loads(sys.argv[2])",
        "if system_packages:",
        "    subprocess.check_call(['apt-get', 'update', '-qq'])",
        "    subprocess.check_call(['apt-get', 'install', '-y', '-qq', "
        "'--no-install-recommends', *system_packages])",
        "if packages:",
        "    subprocess.check_call([sys.executable, '-m', 'pip', 'install', "
        "'--target', target, *packages])",
        "rows = sorted(({'name': d.metadata.get('Name') or d.name, 'version': d.version}",
        "               for d in m.distributions(path=[target])),",
        "              key=lambda x: (x['name'].casefold(), x['version']))",
        "pathlib.Path(target, 'installed-packages.json').write_text(",
        "    json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')",
        "print('SYSTEM_PACKAGES=' + json.dumps(sorted(system_packages)))",
    ])
    return [
        "python", "-c", script,
        json.dumps(list(system_packages or [])), json.dumps(list(packages)),
    ]


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
    if not is_within(target, root):
        raise ValueError("resolved path leaves authorized root")
    if exists and not target.exists():
        raise FileNotFoundError("path does not exist")
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
        # Install jobs may add system packages. Those land in the container's own
        # filesystem, and every job container is removed when it exits, so a library
        # installed by one job is invisible to the next unless the container is
        # committed to an image first. The image each workspace has reached is
        # recorded beside the dependency directory.
        self.image_record_name = os.environ.get("AGENT_JOB_IMAGE_RECORD", "job-image")
        # Admission and slot acquisition must be atomic: two concurrent starts
        # both observing a free slot would otherwise both launch a container.
        self.job_lock = threading.RLock()
        # Containers already committed to an image, so a polled status call does not
        # commit the same finished install over and over.
        self._committed: dict[str, str] = {}

    def _image_record(self, workspace: Path) -> Path:
        """Where a workspace records the image its install jobs have produced."""
        return workspace / ".runtime" / self.image_record_name

    def _workspace_image(self, workspace: Path) -> str | None:
        """The image this workspace has reached, or None for the configured base.

        Read from the record rather than assumed: a workspace whose installs have
        added nothing must keep using the base image, and one that added a system
        library must not silently fall back to an image without it.
        """
        try:
            recorded = self._image_record(workspace).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return recorded or None

    def _commit_job_image(self, workspace: Path, container: str) -> dict:
        """Commit a finished install container so later jobs can see what it added.

        Committing even when the install failed is deliberate: an install that
        fails on one requirement may still have installed the system packages that
        preceded it, and those are the difference between "the dependency set is
        unavailable" and "the dependency set loads but is broken". The commit is
        always based on the configured image, never on a previous commit, so installs
        do not accumulate layers across a session.
        """
        digest = hashlib.sha256(
            (self.image + "|" + str(workspace) + "|" + str(time.time())).encode()
        ).hexdigest()[:12]
        tag = f"forestry-job:{digest}"
        created = docker("commit", container, tag, check=False)
        if created.returncode != 0:
            return {
                "committed": False,
                "error": (created.stderr or "docker commit failed").strip()[:300],
            }
        record = self._image_record(workspace)
        try:
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(tag + "\n", encoding="utf-8")
        except OSError as exc:
            return {"committed": True, "image": tag, "recorded": False, "error": str(exc)[:200]}
        return {"committed": True, "image": tag, "recorded": True}

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

    def prune_finished(self, keep_seconds: int = 3600) -> int:
        """Remove exited managed containers that are no longer needed.

        Settled containers accumulate one per job and are never reclaimed. They do not
        occupy an execution slot -- only *running* containers do -- but the pile makes
        operator inspection and slot accounting progressively harder to read, and a
        lingering container name is exactly what ``job_start`` checks before launching.
        Recent ones are kept briefly so a late ``job_status`` still finds its output.
        """
        listed = docker(
            "ps", "-a", "--filter", "label=forestry.runtime.managed=true",
            "--filter", "status=exited", "--format", "{{.Names}}", check=False,
        )
        removed = 0
        for name in [line.strip() for line in listed.stdout.splitlines() if line.strip()]:
            try:
                info = json.loads(docker("inspect", name).stdout)[0]
            except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError):
                continue
            finished = str(info.get("State", {}).get("FinishedAt") or "")
            try:
                stamp = finished.replace("Z", "+00:00")
                moment = datetime.datetime.fromisoformat(stamp).timestamp()
            except (ValueError, TypeError):
                continue
            if moment and time.time() - moment >= keep_seconds:
                docker("rm", "-f", name, check=False)
                removed += 1
        return removed

    def watchdog(self, interval_seconds: int = 15) -> None:
        maintenance_every = max(1, int(300 / max(1, interval_seconds)))
        cycles = 0
        while True:
            try:
                self.enforce_deadlines()
                cycles += 1
                if cycles % maintenance_every == 0:
                    self.prune_finished()
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
        if not is_within(path, self.data_root):
            raise PermissionError("workspace is outside runtime-owned data root")
        return path

    def _container(self, job_id: str) -> str:
        if not ID.fullmatch(job_id):
            raise ValueError("invalid job id")
        return "forestry-" + job_id

    def _active_jobs(self) -> list[str]:
        running = docker(
            "ps", "--filter", "label=forestry.runtime.managed=true",
            "--format", "{{.Names}}",
        )
        return [line.strip() for line in running.stdout.splitlines() if line.strip()]

    def _acquire_slot(self) -> None:
        """Reserve one of the fixed execution slots, atomically.

        Callers must hold ``job_lock``.  A full slot pool is a retryable
        condition, not an unknown submission outcome, so it gets its own code.
        """
        active = self._active_jobs()
        if len(active) >= self.max_jobs:
            raise BridgeError(
                f"the {self.max_jobs} execution slot(s) are occupied by "
                f"{', '.join(active[:4])}",
                code="resource_busy",
                retryable=True,
                active_jobs=active[:8],
                max_jobs=self.max_jobs,
            )

    def _container_args(
        self, job_id: str, mounts: list[tuple[Path, str, str]], network: str,
        chat_id: str, timeout_seconds: int, workdir: bool = True,
        kind: str = "python", workspace: Path | None = None,
    ) -> list[str]:
        """Docker flags for one job container.

        The isolation is applied to every job *except* the install job, and the
        exception is deliberate rather than incidental:

        * a package manager needs a writable root filesystem and the file-ownership
          capabilities (``CAP_CHOWN``, ``CAP_DAC_OVERRIDE``) to maintain its state.
          With ``--cap-drop ALL`` in place apt does not fail with a permission error;
          it reports ``E: Unable to locate package``, because it could not build its
          package lists. Measured directly: the same command succeeds on a writable
          rootfs with docker's default capabilities and fails with ``--cap-drop ALL``;
        * the user runs this Runtime on their own machine and already holds these
          permissions, so the install job is not being granted anything the operator
          does not have;
        * code the agent wrote still runs read-only, unprivileged and
          capability-stripped, so this does not widen what a ``code_run`` may do.
        """
        args = [
            "--label", "forestry.runtime.managed=true",
            "--label", f"forestry.runtime.job_id={job_id}",
            "--label", f"forestry.runtime.chat_id={str(chat_id or '')[:100]}",
            "--label", f"forestry.runtime.deadline={int(time.time()) + timeout_seconds}",
            "--label", f"forestry.runtime.kind={kind}",
            "--label", f"forestry.runtime.workspace={workspace if workspace is not None else ''}",
            "--cpus", self.cpu, "--memory", self.memory,
            "--pids-limit", self.pids,
            "--security-opt", "no-new-privileges", "--init",
        ]
        if kind == "install":
            # Writable root filesystem and uid 0, so a system package can be added
            # and then committed into an image the code jobs run from.
            args.extend([
                "--user", "0:0",
                "--tmpfs", "/tmp:rw,nosuid,size=536870912",
            ])
        else:
            args.extend([
                "--read-only", "--cap-drop", "ALL",
                "--user", "10001:10001",
                "--tmpfs", "/tmp:rw,nosuid,size=536870912",
            ])
        args.extend([
            "--network", network,
            "-e", "PYTHONPATH=/workspace/.runtime/deps", "-e", "HOME=/tmp",
        ])
        for host, container, mode in mounts:
            args.extend([
                "--mount",
                f"type=bind,source={host},target={container},readonly"
                if mode == "ro" else f"type=bind,source={host},target={container}",
            ])
        if workdir:
            args.extend(["--workdir", "/workspace"])
        return args

    def job_start(self, body: dict) -> dict:
        job_id = str(body.get("job_id") or "")
        name = self._container(job_id)
        workspace = self._workspace(str(body.get("workspace") or ""))
        existing = docker("ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
        if existing.stdout.strip() == name:
            return self.job_status({"job_id": job_id, "offset": body.get("offset", 0)}) | {"already_exists": True}

        kind = str(body.get("kind") or "python")
        timeout_seconds = min(14400, max(1, int(body.get("timeout_seconds", 14400))))
        command: list[str]
        mounts: list[tuple[Path, str, str]] = []
        if kind == "install":
            deps = safe_relative(workspace, ".runtime/deps", exists=True)
            mounts.append((deps, "/deps", "rw"))
            packages = body.get("packages") or []
            system_packages = body.get("system_packages") or []
            if not isinstance(packages, list) or len(packages) > 20:
                raise ValueError("packages must be a list of at most 20 entries")
            if not isinstance(system_packages, list) or len(system_packages) > 20:
                raise ValueError("system_packages must be a list of at most 20 entries")
            if not packages and not system_packages:
                # A job that installs nothing would still burn a slot and a container,
                # and would report success without changing the environment.
                raise ValueError("an install job needs at least one Python or system package")
            for package in packages:
                if not re.fullmatch(r"[A-Za-z0-9_.\-\[\],<>=!~]+", str(package)):
                    raise ValueError("invalid package requirement")
            for package in system_packages:
                # Debian package names only: this is passed to apt as one argument
                # each, never through a shell, so the risk is a bad name rather than
                # an injection -- but a bad name that apt reads as an option would
                # still change what runs, so the character set is enforced.
                if not re.fullmatch(r"[a-z0-9][a-z0-9+.\-]{0,63}", str(package)):
                    raise ValueError("invalid system package name")
            command = dependency_install_command(
                list(map(str, packages)), list(map(str, system_packages)),
            )
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

        with self.job_lock:
            self._acquire_slot()
            args = [
                "run", "-d", "--name", name,
                *self._container_args(
                    job_id, mounts, network, str(body.get("chat_id") or ""),
                    timeout_seconds, workdir=kind != "install", kind=kind,
                    workspace=workspace,
                ),
                (self.image if kind == "install" else (self._workspace_image(workspace) or self.image)),
                *command,
            ]
            result = docker(*args)
        return {"job_id": job_id, "container_id": result.stdout.strip(), "state": "running", "offset": 0}

    def job_run(self, body: dict) -> dict:
        """Run a short, bounded, self-contained check inside the job image.

        This never occupies an execution slot: the probe lasts seconds, shares
        neither the workspace nor the action files, and is used to answer
        "what is actually available in this environment" with real evidence
        instead of an inference from an empty workspace.
        """
        modules = body.get("modules") or []
        if not isinstance(modules, list) or len(modules) > 40:
            raise ValueError("modules must be a list of at most 40 entries")
        for module in modules:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", str(module)):
                raise ValueError("invalid module name")
        workspace = self._workspace(str(body.get("workspace") or ""))
        deps = safe_relative(workspace, ".runtime/deps", exists=True)
        timeout_seconds = min(120, max(5, int(body.get("timeout_seconds", 120))))
        job_id = "env_" + hashlib.sha256(
            (str(workspace) + "|" + ",".join(map(str, modules)) + "|" + str(time.time())).encode()
        ).hexdigest()[:24]
        mounts: list[tuple[Path, str, str]] = [(deps, "/deps", "ro")]
        args = [
            "run", "--rm",
            *self._container_args(
                job_id, mounts, "none", str(body.get("chat_id") or ""),
                timeout_seconds, workdir=False,
            ),
            self.image, *environment_probe_command(list(map(str, modules))),
        ]
        try:
            completed = subprocess.run(
                ["docker", *args], text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=timeout_seconds + 20,
            )
        except subprocess.TimeoutExpired as exc:
            raise BridgeError(
                "environment check exceeded its time budget",
                code="environment_check_timeout", retryable=True, timeout_seconds=timeout_seconds,
            ) from exc
        combined = (completed.stdout or "") + (completed.stderr or "")
        report: list[dict] = []
        marker_index = combined.rfind(ENVIRONMENT_MARKER)
        if marker_index >= 0:
            tail = combined[marker_index + len(ENVIRONMENT_MARKER):].strip()
            try:
                decoded = json.loads(tail.splitlines()[0])
                if isinstance(decoded, list):
                    report = decoded
            except (ValueError, IndexError):
                report = []
        if not report:
            raise BridgeError(
                "environment check produced no parsable report: " + combined[-1500:],
                code="environment_check_unparsable", retryable=False,
            )
        return {
            "image": self.image,
            "dependency_mount": "/deps",
            "pythonpath": "/workspace/.runtime/deps",
            "exit_code": completed.returncode,
            "modules": report,
        }

    def job_status(self, body: dict) -> dict:
        job_id = str(body.get("job_id") or "")
        name = self._container(job_id)
        try:
            raw = docker("inspect", name).stdout
            info = json.loads(raw)[0]
        except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError) as exc:
            raise ValueError("managed job container does not exist") from exc
        state = info["State"]
        labels = (info.get("Config", {}).get("Labels", {}) or {})
        deadline = int(labels.get("forestry.runtime.deadline", "0") or 0)
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
        committed: dict = {}
        if normalized != "running" and labels.get("forestry.runtime.kind") == "install":
            # An install job is the only one that can add something to the image
            # rather than to a mount, and the container is discarded on exit, so
            # whatever it added has to be committed before that happens. The status
            # call is polled repeatedly, so committing is keyed on the container id:
            # the same finished container is committed once.
            recorded_workspace = str(labels.get("forestry.runtime.workspace") or "")
            container_id = str(info.get("Id") or "")[:64]
            if recorded_workspace and self._committed.get(container_id) != container_id:
                try:
                    workspace = self._workspace(recorded_workspace)
                except Exception:
                    workspace = None
                if workspace is not None:
                    committed = self._commit_job_image(workspace, name)
                    if committed.get("committed"):
                        self._committed[container_id] = container_id
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
            **({"image": committed} if committed else {}),
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
                "/jobs/run": self.service.job_run,
            }
            if route not in routes:
                raise ValueError("unknown endpoint")
            self.reply(200, routes[route](body))
        except PermissionError as exc:
            self.reply(403, {"error": str(exc), "code": "permission_denied", "retryable": False})
        except FileNotFoundError as exc:
            self.reply(404, {"error": str(exc), "code": "path_not_found", "retryable": False})
        except BridgeError as exc:
            self.reply(200 if exc.retryable else 400, {
                "error": str(exc), "code": exc.code, "retryable": exc.retryable,
                **exc.extra,
            })
        except subprocess.CalledProcessError as exc:
            self.reply(500, {
                "error": (exc.stderr or exc.stdout or str(exc))[-4000:],
                "code": "docker_command_failed", "retryable": False,
            })
        except Exception as exc:
            self.reply(400, {"error": str(exc), "code": "invalid_request", "retryable": False})


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
