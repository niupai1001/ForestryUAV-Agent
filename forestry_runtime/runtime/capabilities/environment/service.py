"""Execution-environment facts: what is installed, importable, and verified.

The Runtime must not infer "dependencies are needed" from an empty workspace,
and it must not let code start against a dependency set whose installation has
not reached a confirmed terminal success.  Both questions are answered from
evidence produced inside the job image itself:

* ``dependency_install`` waits for the install container to terminate, then
  records the packages that actually landed in the dependency directory;
* ``environment_check`` imports modules inside the same image with the same
  ``PYTHONPATH`` and records which ones are genuinely importable;
* ``code_run`` consults those records before submitting, and returns an explicit
  ``blocked_by`` instead of launching code that cannot import its inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import time

from ...kernel.protocol import SubmissionRefused, ToolPreconditionError
from ...storage import AssetError
from ...workspace import BridgeRequestError
from shared.job_diagnostics import (
    describe_resource_failure,
    filesystem_evidence,
    parse_marker,
    parse_resource_failure,
)
from shared.job_diagnostics import FAILURE_MARKER, RESOURCE_MARKER
from shared.outcome import missing_shared_library

VERIFICATION_FILE = "environment.json"
INSTALL_FAILURES_FILE = "install-failures.json"
VERIFICATION_VERSION = "environment-v1"

# How many times the same package set may be submitted after it has already been
# observed to come back missing before the Runtime answers from its own record.
# A first retry is legitimate (a transient network error, a longer timeout); past
# that the argument list has not changed, so the outcome cannot either.
DEFAULT_INSTALL_ATTEMPT_LIMIT = 2


def install_attempt_limit() -> int:
    """Read the retry ceiling when it is needed, not when the module is imported."""
    try:
        return max(1, int(os.getenv("INSTALL_ATTEMPT_LIMIT", "") or DEFAULT_INSTALL_ATTEMPT_LIMIT))
    except ValueError:
        return DEFAULT_INSTALL_ATTEMPT_LIMIT


_IMPORT_PATTERN = re.compile(
    r"^[ \t]*(?:from[ \t]+([A-Za-z_][A-Za-z0-9_.]*)[ \t]+import|import[ \t]+([A-Za-z_][A-Za-z0-9_.]*))",
    re.MULTILINE,
)

# Modules the job image provides itself or that ship with the interpreter.  Only
# used to avoid blocking on names the Runtime cannot observe; everything else is
# verified inside the container.
_STDLIB_HINT = frozenset("""
abc argparse array asyncio base64 binascii bisect builtins bz2 calendar cmath
collections colorsys concurrent configparser contextlib copy csv ctypes
dataclasses datetime decimal difflib dis email enum errno faulthandler fnmatch
fractions ftplib functools gc getpass glob gzip hashlib heapq hmac html http
imaplib importlib inspect io ipaddress itertools json keyword linecache locale
logging lzma mailbox math mimetypes mmap multiprocessing netrc numbers operator
os pathlib pickle pkgutil platform plistlib poplib posixpath pprint profile
pstats pty queue quopri random re readline reprlib resource secrets select
shelve shlex shutil signal site smtplib socket socketserver sqlite3 ssl stat
statistics string struct subprocess sys sysconfig tarfile tempfile textwrap
threading time timeit tkinter token tokenize traceback tracemalloc types
typing unicodedata unittest urllib uuid venv warnings wave weakref webbrowser
xml xmlrpc zipfile zipimport zlib zoneinfo
""".split())


class InstallNotVerified(SubmissionRefused):
    """Installation did not reach a verified success; dependent code must wait."""

    def __init__(self, reason: str, *, data: dict, failure: dict):
        details = {
            key: value for key, value in failure.items()
            if key not in {"operation_started", "side_effects"}
        }
        super().__init__(reason, data=data, **details)
        self.failure_details["operation_started"] = True
        self.failure_details["side_effects"] = "partial_install"


def _module_root(module: str) -> str:
    return str(module).split(".", 1)[0]


def normalise_requirements(packages) -> list[str]:
    """Map requirement strings to the distribution names they install.

    ``scikit-learn>=1.4`` and ``scikit_learn`` name the same distribution, so the
    verification key must not depend on the version specifier or the separator.
    """
    names: list[str] = []
    for raw in packages or []:
        text = str(raw).strip()
        if not text:
            continue
        name = re.split(r"[<>=!~\[; ]", text, maxsplit=1)[0]
        name = name.replace("_", "-").strip().casefold()
        if name and name not in names:
            names.append(name)
    return sorted(names)


def requirement_fingerprint(packages) -> str:
    payload = json.dumps(normalise_requirements(packages), sort_keys=True)
    return "env_" + hashlib.sha256(
        f"{VERIFICATION_VERSION}:{payload}".encode("utf-8")
    ).hexdigest()[:32]


#: The final line an install job prints, reporting the system packages it installed.
_SYSTEM_PACKAGES_MARKER = "SYSTEM_PACKAGES="


def _reported_system_packages(output: str) -> set[str]:
    """The system packages an install job reported installing.

    Read from the job's own output rather than from the workspace: a system library
    lands in the image and is not visible in the dependency directory, so the job's
    terminal report is the only evidence this call can actually obtain.
    """
    for line in reversed(str(output or "").splitlines()):
        stripped = line.strip()
        index = stripped.find(_SYSTEM_PACKAGES_MARKER)
        if index < 0:
            continue
        try:
            parsed = json.loads(stripped[index + len(_SYSTEM_PACKAGES_MARKER):])
        except ValueError:
            continue
        if isinstance(parsed, list):
            return {str(item) for item in parsed}
    return set()


def install_resource_evidence(observation: dict) -> dict | None:
    """What the install container ran out of, and where, or ``None``.

    Two sources are read and neither is guessed at: the job script's own structured
    report (which knows the tmpdir, the pip cache and the container's capacity) and
    the log text (which is where pip puts ``Errno 28`` when it is pip rather than
    the script that noticed). A log that says nothing about a resource produces
    ``None`` so the caller cannot turn silence into a diagnosis.
    """
    output = str(observation.get("output") or "")
    reported = observation.get("resource")
    if not isinstance(reported, dict):
        reported = {}
    failure_marker = parse_marker(output, FAILURE_MARKER) or {}
    success_marker = parse_marker(output, RESOURCE_MARKER) or {}
    detected = parse_resource_failure(output)
    # The bridge already merged what it found; `detected` recomputes from the
    # complete log, which is what the runtime holds even when the bridge was older.
    merged: dict = {
        key: value for key, value in (reported or {}).items()
        if key not in {"filesystems", "container_space"}
    }
    for source in (failure_marker, detected or {}):
        for key, value in source.items():
            if value is not None:
                merged[key] = value
    if not merged:
        return None
    space = (
        reported.get("container_space")
        or failure_marker.get("space")
        or success_marker.get("space")
        or {}
    )
    merged["container_space"] = space
    merged["filesystems"] = filesystem_evidence(space)
    merged["exhausted_path"] = merged.get("exhausted_path") or merged.get("filename")
    if not merged.get("kind"):
        merged["kind"] = "resource_limit"
    merged["detail"] = describe_resource_failure(merged)
    return merged


class EnvironmentCapability:
    """Shared environment bookkeeping for install, execution, and checks."""
    # -------------------------------------------------------------- bookkeeping

    def _environment_path(self) -> Path:
        return self.workspace / ".runtime" / "deps" / VERIFICATION_FILE

    def _environment_records(self) -> dict:
        try:
            data = json.loads(self._environment_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _image_modules(self) -> list[str]:
        """Importable modules in the job image, as last proven by an import check.

        Read from verified evidence only: this never starts a container, and it
        never turns an inference ("the image probably ships numpy") into a claim.
        """
        names: set[str] = set()
        for entry in self._environment_records().values():
            if not isinstance(entry, dict):
                continue
            names.update(str(name) for name in entry.get("modules") or [] if name)
        return sorted(names)

    def _write_environment_records(self, records: dict) -> None:
        path = self._environment_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------- failed-install bookkeeping

    def _install_failures_path(self) -> Path:
        return self.workspace / ".runtime" / "deps" / INSTALL_FAILURES_FILE

    def _install_failures(self) -> dict:
        try:
            data = json.loads(self._install_failures_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _install_attempts(self, key: str) -> dict:
        entry = self._install_failures().get(key)
        return entry if isinstance(entry, dict) else {}

    def _clear_install_failure(self, key: str) -> None:
        records = self._install_failures()
        if records.pop(key, None) is None:
            return
        path = self._install_failures_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record_install_failure(self, key: str, failure: dict, requirements: list[str]) -> dict:
        """Remember that this package set did not land, and how it failed."""
        records = self._install_failures()
        entry = dict(records.get(key) or {})
        attempts = int(entry.get("attempts") or 0) + 1
        entry.update({
            "requirements": requirements,
            "attempts": attempts,
            "last_code": failure.get("code"),
            "last_exit_code": failure.get("exit_code"),
            "missing_from_manifest": failure.get("missing_from_manifest") or [],
            "at": time.time(),
        })
        records[key] = entry
        path = self._install_failures_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return entry

    def _installed_packages(self) -> list[dict]:
        manifest = self.workspace / ".runtime" / "deps" / "installed-packages.json"
        if not manifest.is_file():
            return []
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError:
            return []
        if not isinstance(data, list):
            return []
        rows = [
            {"name": str(item.get("name") or ""), "version": item.get("version")}
            for item in data if isinstance(item, dict)
        ]
        return [row for row in rows if row["name"]]

    def installed_distributions(self) -> set[str]:
        return {row["name"].replace("_", "-").casefold() for row in self._installed_packages()}

    def installed_top_levels(self) -> set[str]:
        """Top-level import names physically present in the dependency directory."""
        roots: set[str] = set()
        deps = self.workspace / ".runtime" / "deps"
        if not deps.is_dir():
            return roots
        for entry in deps.iterdir():
            name = entry.name
            if entry.is_dir():
                if name.endswith((".dist-info", ".egg-info", "__pycache__")):
                    continue
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    roots.add(name)
            elif name.endswith(".py") and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", name):
                roots.add(name[:-3])
        py_version = deps / f"python{os.sys.version_info.major}.{os.sys.version_info.minor}"
        return roots

    # ------------------------------------------------------------------ probing

    def _probe_modules(self, modules: list[str]) -> dict:
        modules = [name for name in dict.fromkeys(str(item).strip() for item in modules) if name]
        if not modules:
            return {
                "image": None, "modules": [], "checked_at": time.time(),
                "note": "No modules were requested; nothing was import-checked.",
            }
        try:
            report = self.workspaces.bridge.job("run", {
                "workspace": str(self.workspaces.host_workspace(self.store)),
                "chat_id": self.chat_id,
                "modules": modules,
                "timeout_seconds": 120,
            })
        except BridgeRequestError as exc:
            if exc.code == "resource_busy":
                return {
                    "image": None, "modules": [], "checked_at": time.time(),
                    "resource_busy": True,
                    "error": str(exc),
                    "retryable": True,
                }
            raise
        rows = []
        for item in report.get("modules") or []:
            if not isinstance(item, dict):
                continue
            rows.append({
                "module": item.get("module"),
                "importable": bool(item.get("importable")),
                "version": item.get("version"),
                "distributions": item.get("distributions") or [],
                "error": item.get("error"),
                "origin": item.get("origin"),
            })
        return {
            "image": report.get("image"),
            "base_image": report.get("base_image"),
            "image_source": report.get("image_source"),
            "dependency_mount": report.get("dependency_mount"),
            "pythonpath": report.get("pythonpath"),
            "scratch_mount": report.get("scratch_mount"),
            "scratch_host_path": report.get("scratch_host_path"),
            "container": report.get("container") or {},
            "space": report.get("space") or [],
            "limits": report.get("limits") or {},
            "exit_code": report.get("exit_code"),
            "modules": rows,
            "checked_at": time.time(),
            "installed_packages": self._installed_packages(),
        }

    # ------------------------------------------------------------------- tools

    def _classify_modules(self, rows: list[dict]) -> list[dict]:
        """Why each probed module is or is not usable, as an observation.

        The distinction this exists to make: ``ModuleNotFoundError`` is raised both
        for a module that was never installed and for one that is sitting in the
        dependency directory but cannot load. Those two states call for opposite
        actions -- install it, versus stop installing -- and a bare message cannot
        tell them apart. Observed in a real Run: ``rasterio`` and ``numpy``
        installed successfully and were still reported unimportable, and the model
        retried six different package sets because nothing said "installed, but the
        missing piece is a system library inside the image".

        Facts only. Nothing here tells the model what to do; ``cause_scope`` states
        where the missing piece lives, and the decision stays with the model.
        """
        present = self.installed_top_levels()
        classified: list[dict] = []
        for row in rows:
            name = str(row.get("module") or "").strip()
            if not name:
                continue
            entry: dict = {
                "module": name,
                "importable": bool(row.get("importable")),
                "version": row.get("version"),
                "loader_error": row.get("error"),
                "present_in_deps": name in present,
            }
            if entry["importable"]:
                entry["state"] = "importable"
                entry["cause_scope"] = None
                entry["remedy_reachable"] = None
                entry["reason"] = f"{name} imports in the job image"
            elif entry["present_in_deps"]:
                # On disk yet unloadable: a missing shared library or a broken
                # install. Installing the distribution again cannot add a system
                # library, so the remedy is not reachable from inside the container.
                library = missing_shared_library(str(row.get("error") or ""))
                entry.update({
                    "state": "present_not_importable",
                    "missing_system_library": library,
                    "cause_scope": "image" if library else "deps",
                    "remedy_reachable": not library,
                    "reason": (
                        f"{name} is installed in the dependency directory but fails to import"
                        + (f": missing system library {library}, which lives in the job image"
                           if library else "")
                    ),
                })
            else:
                entry.update({
                    "state": "absent",
                    "cause_scope": "deps",
                    "remedy_reachable": True,
                    "reason": f"{name} is not installed in the dependency directory",
                })
            classified.append(entry)
        return classified

    def environment_check(self, modules=None, record_requirements=None):
        """Report real package versions and importability inside the job image."""
        requested = [str(item) for item in (modules or [])]
        probe = self._probe_modules(requested)
        if probe.get("resource_busy"):
            return {
                "environment": "busy",
                "resource_busy": True,
                "retryable": True,
                "error": probe.get("error"),
                "guidance": (
                    "An execution slot is occupied. The check did not run and nothing "
                    "changed. Retry once the running job finishes."
                ),
            }
        result: dict = {
            "image": probe.get("image"),
            "dependency_directory": "/deps",
            "pythonpath": probe.get("pythonpath"),
            "installed_packages": probe.get("installed_packages") or [],
            "installed_package_count": len(probe.get("installed_packages") or []),
            "modules": probe.get("modules") or [],
            "checked_at": probe.get("checked_at"),
        }
        # The environment a code job will actually use, stated as measured facts:
        # the image that job runs (which is not the configured base image once an
        # install has committed one), the dependency directory, and the capacity of
        # the writable locations. "Which image, which packages, how much scratch"
        # is one question, and answering it from inference is what made a full
        # scratch disk look like an unbuildable requirement.
        if probe.get("image"):
            scratch = next(
                (row for row in probe.get("space") or []
                 if row.get("path") == probe.get("scratch_mount")),
                None,
            )
            result["execution_environment"] = {
                "job_image": probe.get("image"),
                "base_image": probe.get("base_image"),
                "image_source": probe.get("image_source"),
                "dependency_directory": "/deps",
                "scratch_mount": probe.get("scratch_mount"),
                "scratch_host_path": probe.get("scratch_host_path"),
                "scratch_capacity": scratch,
                "filesystems": probe.get("space") or [],
                "limits": probe.get("limits") or {},
                "python": (probe.get("container") or {}).get("python"),
                "platform": (probe.get("container") or {}).get("platform"),
                "note": (
                    "Capacity is measured inside the container a job runs in; a host "
                    "figure for the same filesystem would be a different number."
                ),
            }
        if requested:
            result["importable"] = [
                item["module"] for item in result["modules"] if item.get("importable")
            ]
            result["not_importable"] = [
                {"module": item["module"], "error": item.get("error")}
                for item in result["modules"] if not item.get("importable")
            ]
            # The same observation, classified: a state the caller can act on,
            # instead of a boolean that conflates two opposite problems.
            facts = self._classify_modules(result["modules"])
            result["module_facts"] = facts
            result["module_states"] = {row["module"]: row["state"] for row in facts}
            blocked = [row for row in facts if row["state"] == "present_not_importable"]
            if blocked:
                result["environment_note"] = (
                    "Installed but not importable: "
                    + ", ".join(
                        row["module"]
                        + (f" (missing {row['missing_system_library']} in the job image)"
                           if row.get("missing_system_library") else "")
                        for row in blocked
                    )
                    + ". The dependency directory already holds these; installing them "
                      "again does not change the job image."
                )
        if record_requirements:
            key = requirement_fingerprint(record_requirements)
            records = self._environment_records()
            entry = dict(records.get(key) or {})
            probed = set(entry.get("modules") or [])
            for item in result["modules"]:
                if item.get("importable"):
                    probed.add(str(item.get("module")))
            entry.update({
                "requirements": normalise_requirements(record_requirements),
                "modules": sorted(probed),
                "image": result.get("image"),
                "verified_at": time.time(),
            })
            records[key] = entry
            self._write_environment_records(records)
            result["verification_key"] = key
            result["verified_requirements"] = entry["requirements"]
        return result

    def _submit_install(self, packages, timeout_seconds: int) -> dict:
        """Submit the durable install job.  Overridden by the install capability.

        Kept separate from `environment_install` so the verification layer never
        has to call back into the capability that delegates to it.
        """
        raise ToolPreconditionError(
            "Dependency installation is not wired into this environment."
        )

    def environment_install(self, packages, timeout_seconds=1800, system_packages=None):
        """Install dependencies and prove they landed before returning success.

        The job is submitted through the normal durable-job path, then this call
        waits for its terminal state.  A partial install is reported as a failure
        with the packages that are still missing, never as a success.

        ``system_packages`` names operating-system packages, which are installed
        before the Python requirements in the same job. A wheel can need a shared
        library the wheel cannot carry, and pip reports that install as a success
        while the import fails -- so the two kinds are installable together and
        verified together.
        """
        system_packages = list(system_packages or [])
        requirements = normalise_requirements(packages)
        if not requirements and not system_packages:
            raise ToolPreconditionError(
                "At least one Python package or one system package is required."
            )
        key = requirement_fingerprint(packages)
        prior = self._install_attempts(key)
        limit = install_attempt_limit()
        if int(prior.get("attempts") or 0) >= limit:
            # A plain refusal, not `InstallNotVerified`: nothing was started this
            # time, and the difference matters to the caller's retry accounting.
            raise SubmissionRefused(
                "This exact package set has already failed to install "
                f"{prior['attempts']} times in this workspace; requesting it again "
                "cannot change the outcome. Nothing was started.",
                data={
                    "prior_attempts": prior["attempts"],
                    "prior_failure": prior,
                    "installed_top_levels": sorted(self.installed_top_levels()),
                    "image_modules": self._image_modules(),
                },
                failure={
                    "stage": "preconditions", "code": "repeated_install_failure",
                    "operation_started": False, "side_effects": "none",
                    "attempts": prior["attempts"],
                    "requested_requirements": requirements,
                    "last_code": prior.get("last_code"),
                    "missing_from_manifest": prior.get("missing_from_manifest") or [],
                    "installed_top_levels": sorted(self.installed_top_levels()),
                    "image_modules": self._image_modules(),
                    "retryable": False,
                    "guidance": (
                        "Use a module that the image already provides (see image_modules), "
                        "produce the deliverable with a different method, or state plainly "
                        "that the requirement cannot be met in this environment."
                    ),
                },
                code="repeated_install_failure",
                attempts=prior["attempts"],
                requested_requirements=requirements,
                last_code=prior.get("last_code"),
                missing_from_manifest=prior.get("missing_from_manifest") or [],
                installed_top_levels=sorted(self.installed_top_levels()),
                image_modules=self._image_modules(),
                retryable=False,
                guidance=(
                    "Use a module that the image already provides (see image_modules), "
                    "produce the deliverable with a different method, or state plainly "
                    "that the requirement cannot be met in this environment."
                ),
            )
        pending = self._pending_install()
        if pending:
            return self._blocked_by_install(pending, requirements)
        # The record is only created for a submission that actually happens, so a
        # refused attempt never leaves an unsettled job behind to block the next
        # legitimate request.
        started = self._submit_install(
            list(packages), timeout_seconds, system_packages=system_packages,
        )
        job_id = (started.get("data") or {}).get("job_id") or started.get("job_id")
        if not job_id:
            return started
        if started.get("terminal"):
            # Reconciled submission that is already settled: no waiting needed.
            return self._finish_install(job_id, packages, requirements, started, system_packages)
        # Wait in model-visible increments rather than one long block: the Run
        # stays resumable and the model can report honest progress.  The first
        # increment is short so a job that is still running is observed quickly
        # instead of occupying the model turn for the whole install budget.
        increments = self._install_wait_schedule()
        observation: dict = {}
        waited = 0
        for increment in increments:
            remaining = int(timeout_seconds) - waited
            if remaining <= 0:
                break
            chunk = max(1, min(increment, remaining))
            observation = self.job_wait(job_id, timeout_seconds=chunk)
            waited += chunk
            if observation.get("terminal") or self._run_interrupted():
                break
        return self._finish_install(job_id, packages, requirements, observation, system_packages)

    @staticmethod
    def _install_wait_schedule() -> list[int]:
        configured = os.getenv("INSTALL_WAIT_SECONDS", "90,300,900")
        schedule: list[int] = []
        for item in str(configured).split(","):
            item = item.strip()
            if not item:
                continue
            try:
                value = int(item)
            except ValueError:
                continue
            if value > 0:
                schedule.append(value)
        return schedule or [90]

    def _finish_install(
        self, job_id: str, packages, requirements: list[str], observation: dict,
        system_packages: list[str] | None = None,
    ) -> dict:
        system_packages = list(system_packages or [])
        state = str(observation.get("state") or "")
        manifest = self._installed_packages()
        installed = {row["name"].replace("_", "-").casefold() for row in manifest}
        missing = [name for name in requirements if name not in installed]
        # The system packages are proven by the job's own final report, because the
        # library lands in the image rather than in the dependency directory that
        # holds the Python manifest. A job that exited zero without reporting them
        # did not complete both steps, so it must not be recorded as verified.
        reported_system = _reported_system_packages(str(observation.get("output") or ""))
        system_missing = [name for name in system_packages if name not in reported_system]
        key = requirement_fingerprint(packages)
        records = self._environment_records()
        if state == "succeeded" and not missing and not system_missing:
            records[key] = {
                "requirements": requirements,
                "system_packages": system_packages,
                "modules": sorted(records.get(key, {}).get("modules") or []),
                "image": observation.get("image"),
                "installed_at": time.time(),
                "job_id": job_id,
                "state": state,
                "packages_present": sorted(installed),
            }
            self._write_environment_records(records)
            self._clear_install_failure(key)
        verification = {
            "state": state,
            "terminal": bool(observation.get("terminal")),
            "exit_code": observation.get("exit_code"),
            "requirements": requirements,
            "system_packages": system_packages,
            "missing_system_packages": system_missing,
            "missing_from_manifest": missing,
            "installed_packages": manifest[:500],
            "verification_key": key,
            "succeeded": state == "succeeded" and not missing,
        }
        if verification["succeeded"]:
            return {
                "job_id": job_id,
                "job_type": "install",
                "state": state,
                "terminal": True,
                "exit_code": observation.get("exit_code"),
                "verification": verification,
                "output": (observation.get("output") or "")[-4000:],
                "log_chars": observation.get("log_chars"),
                "guidance": (
                    "Installation reached a successful terminal state and the packages are "
                    "present in the dependency directory. Call environment_check for the "
                    "modules you will import before running dependent code."
                ),
            }
        if not observation.get("terminal"):
            failure = {
                "stage": "execution", "code": "install_still_running",
                "operation_started": True, "side_effects": "partial_install",
                "message": "The dependency installation has not reached a terminal state.",
                "retryable": True,
                "suggested_tool": "job_wait",
                "suggested_arguments": {"job_id": job_id},
            }
        elif state == "timed_out":
            failure = {
                "stage": "execution", "code": "install_timed_out",
                "operation_started": True, "side_effects": "partial_install",
                "missing_from_manifest": missing,
            }
        else:
            failure = {
                "stage": "execution", "code": "install_failed",
                "operation_started": True, "side_effects": "partial_install",
                "exit_code": observation.get("exit_code"),
                "missing_from_manifest": missing,
            }
        # A capacity failure is a property of the container, not of the package, and
        # the repair is different: the 512 MiB scratch disk inside the install
        # container was once exhausted while the host had tens of gigabytes free, and
        # the failure was reported as an unexplained "install_failed". The evidence
        # here names the path that ran out and the capacity it ran out of.
        resource = install_resource_evidence(observation)
        if resource:
            failure["code"] = (
                "install_failed_no_space"
                if resource.get("kind") == "no_space_left_on_device"
                else f"install_failed_{resource.get('kind')}"
            )
            failure["resource"] = resource
            failure["message"] = describe_resource_failure(resource)
            failure["guidance"] = (
                "This is a storage limit in the job container, not an unbuildable "
                "requirement. Report the exhausted path and its capacity; do not "
                "request the same package set again without changing where the job "
                "writes."
            )
        # Raising (rather than returning a failure dict) keeps `outcome_ok` false:
        # the tool dispatcher wraps the return value in `{"ok": True, "data": ...}`,
        # which would otherwise report a blocked installation as a success.
        attempt = self._record_install_failure(key, failure, requirements)
        failure = {
            **failure,
            "attempts": attempt["attempts"],
            "attempt_limit": install_attempt_limit(),
            "installed_top_levels": sorted(self.installed_top_levels()),
            "image_modules": self._image_modules(),
        }
        raise InstallNotVerified(
            "Dependency installation did not complete successfully; "
            "dependent code must not start yet.",
            data={
                "job_id": job_id, "job_type": "install", "state": state,
                "terminal": bool(observation.get("terminal")),
                "verification": verification,
                "output": (observation.get("output") or "")[-4000:],
            },
            failure=failure,
        )

    # --------------------------------------------------------------- admission

    def _pending_install(self) -> dict | None:
        """Return the running install job, if the dependency set is unsettled."""
        try:
            rows = self.records.unsettled(("install",))
        except Exception:
            return None
        return rows[0] if rows else None

    def _blocked_by_install(self, pending: dict, requirements: list[str]) -> dict:
        raise SubmissionRefused(
            "A dependency installation is still running in this environment; "
            "execution is blocked until it reaches a terminal state.",
            data={
                "pending_job_id": pending.get("id"),
                "pending_state": pending.get("state"),
                "requested_requirements": requirements,
            },
            code="blocked_by",
            blocked_by="dependency_install",
            pending_job_id=pending.get("id"),
            pending_state=pending.get("state"),
            requested_requirements=requirements,
            retryable=True,
            suggested_tool="job_wait",
            suggested_arguments={"job_id": pending.get("id")},
        )

    def execution_preflight(self, code: str) -> dict | None:
        """Return a ``blocked_by`` payload when code must not start yet.

        Imports the code needs but neither the verified record nor the installed
        dependency directory can satisfy are reported before a container is
        started, so a slot is never spent on a guaranteed ImportError.
        """
        pending = self._pending_install()
        if pending:
            return self._blocked_by_install(pending, [])
        if os.getenv("CODE_RUN_PREFLIGHT", "true").lower() != "true":
            return None
        required = self._imported_module_roots(code)
        if not required:
            return None
        unknown = sorted(
            name for name in required
            if _module_root(name) not in _STDLIB_HINT
        )
        if not unknown:
            return None
        available = self.installed_top_levels()
        records = self._environment_records()
        verified_roots: set[str] = set()
        for entry in records.values():
            verified_roots.update(_module_root(str(name)) for name in entry.get("modules") or [])
        missing = [
            name for name in unknown
            if _module_root(name) not in available and _module_root(name) not in verified_roots
        ]
        if not missing:
            return None
        return {
            "ok": False,
            "outcome_ok": False,
            "error": (
                "Execution was not started: the code imports modules that are not "
                "present in the dependency directory and have not been import-checked."
            ),
            "failure": {
                "stage": "preconditions",
                "code": "blocked_by",
                "blocked_by": "environment_check",
                "operation_started": False,
                "side_effects": "none",
                "unverified_modules": missing[:20],
                "installed_top_levels": sorted(available)[:50],
                "retryable": True,
                "suggested_tool": "environment_check",
                "suggested_arguments": {"modules": missing[:20]},
                "guidance": (
                    "Call environment_check with these modules to prove importability in "
                    "the job image, installing them with dependency_install first if the "
                    "check reports they are missing."
                ),
            },
        }

    @staticmethod
    def _imported_module_roots(code: str | None) -> list[str]:
        roots: list[str] = []
        for match in _IMPORT_PATTERN.finditer(code or ""):
            name = match.group(1) or match.group(2)
            if not name:
                continue
            root = _module_root(name)
            if root and root not in roots:
                roots.append(root)
        return roots

    # ------------------------------------------------------------------ usage

    def require_verified(self, packages) -> tuple[bool, dict]:
        """Answer whether code may rely on *packages* in this environment."""
        requirements = normalise_requirements(packages)
        if not requirements:
            return True, {"requirements": [], "state": "not_required"}
        records = self._environment_records()
        installed = self.installed_distributions()
        record = records.get(requirement_fingerprint(packages)) or {}
        if record.get("installed_at") or record.get("verified_at"):
            return True, {
                "requirements": requirements,
                "state": "verified",
                "job_id": record.get("job_id"),
                "modules": record.get("modules") or [],
            }
        missing = [name for name in requirements if name not in installed]
        return False, {
            "requirements": requirements,
            "state": "unverified",
            "missing_from_manifest": missing,
            "blocked_by": "dependency_install" if missing else "environment_check",
        }
