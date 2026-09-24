"""A failing call must not be executable twice with identical arguments.

Two defects were found by running the capability baseline, and both let a model
burn its whole turn budget repeating a call whose outcome could not change:

* an install that fails *inside* the job image was never remembered, so the model
  re-submitted the same `dependency_install` a dozen times -- twelve model calls,
  no new evidence, and a slot that ended in `infra_error` instead of a graded
  result;
* a submission that starts a container and then fails (a compile error, a missing
  system library) counted as "new work" because the fingerprint ignored the failure
  history, so only the argument list had to stay identical to be re-run forever.

The Runtime is the component that can tell "the model learned something" from "the
model repeated itself", so the rule lives here rather than in the prompt.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic_ai import RunContext

from runtime.agent import AgentDependencies, _execute_tool

try:  # the usage type moved between pydantic_ai releases
    from pydantic_ai.usage import RunUsage
except ImportError:  # pragma: no cover - older layout
    from pydantic_ai import Usage as RunUsage


def _context(deps):
    return RunContext(deps=deps, model=None, usage=RunUsage())


class _StubToolbox:
    """Minimal toolbox: records calls, returns one scripted outcome per call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name, arguments, progress=None):
        self.calls.append((name, dict(arguments)))
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        return dict(outcome)


def _dependencies(toolbox):
    return AgentDependencies(
        toolbox=toolbox,
        cancelled=lambda: False,
        pause_requested=lambda: False,
    )


FAILED_INSTALL = {
    "ok": False,
    "outcome_ok": False,
    "error": "Dependency installation did not complete successfully.",
    "failure": {
        "stage": "execution",
        "code": "install_failed",
        "operation_started": True,
        "side_effects": "partial_install",
        "missing_from_manifest": ["gdal"],
    },
}


class RepeatedFailedCallTests(unittest.TestCase):
    def _run(self, args: dict, outcomes) -> dict:
        toolbox = _StubToolbox(outcomes)
        deps = _dependencies(toolbox)
        context = _context(deps)
        output = asyncio.run(_execute_tool(context, "dependency_install", args, False))
        return {"output": output, "deps": deps, "toolbox": toolbox}

    def test_install_failure_is_remembered_and_not_repeated(self):
        """A failure that started an operation is still a recorded failure."""
        args = {"packages": ["gdal"], "timeout_seconds": 300}
        first = self._run(args, [FAILED_INSTALL])
        self.assertFalse(first["output"]["outcome_ok"])
        self.assertEqual(len(first["toolbox"].calls), 1)
        self.assertTrue(first["deps"].failed_calls)

        second = self._run(args, [FAILED_INSTALL])
        # Same call, fresh Run: the guard is per-Run, so this one executes again.
        self.assertEqual(len(second["toolbox"].calls), 1)

        # Within one Run the second identical call never reaches the environment.
        toolbox = _StubToolbox([FAILED_INSTALL, FAILED_INSTALL])
        deps = _dependencies(toolbox)
        context = _context(deps)
        asyncio.run(_execute_tool(context, "dependency_install", args, False))
        blocked = asyncio.run(_execute_tool(context, "dependency_install", args, False))
        self.assertEqual(len(toolbox.calls), 1)
        self.assertEqual(blocked["failure"]["code"], "duplicate_failed_call")
        self.assertEqual(
            blocked["failure"]["previous_failure"]["code"], "install_failed"
        )

    def test_three_identical_failures_pause_the_run(self):
        toolbox = _StubToolbox([FAILED_INSTALL])
        deps = _dependencies(toolbox)
        context = _context(deps)
        args = {"packages": ["gdal"], "timeout_seconds": 300}
        asyncio.run(_execute_tool(context, "dependency_install", args, False))
        for _ in range(3):
            asyncio.run(_execute_tool(context, "dependency_install", args, False))
        self.assertIsNotNone(deps.pause_reason)
        self.assertEqual(len(toolbox.calls), 1)

    def test_changed_arguments_are_new_work(self):
        """Extending the package list is genuinely different, so it may run."""
        toolbox = _StubToolbox([FAILED_INSTALL, FAILED_INSTALL])
        deps = _dependencies(toolbox)
        context = _context(deps)
        asyncio.run(_execute_tool(
            context, "dependency_install", {"packages": ["gdal"]}, False
        ))
        asyncio.run(_execute_tool(
            context, "dependency_install", {"packages": ["gdal", "rasterio"]}, False
        ))
        self.assertEqual(len(toolbox.calls), 2)
        self.assertIsNone(deps.pause_reason)

    def test_same_missing_prerequisite_across_tools_and_options_pauses(self):
        missing = {
            "ok": False, "error": "path missing",
            "failure": {
                "stage": "preconditions", "code": "source_path_not_found",
                "requested_path": "1605 白桦", "source_id": "grant_" + "a" * 32,
                "checked_scope": {"source_id": "grant_" + "a" * 32,
                                  "path": "1605 白桦"},
                "operation_started": False, "side_effects": "none",
            },
        }
        toolbox = _StubToolbox([missing, missing, missing])
        deps = _dependencies(toolbox)
        context = _context(deps)
        for name, args in [
            ("fs_list", {"path": "1605 白桦", "source_id": "grant_" + "a" * 32}),
            ("fs_search", {"path": "1605 白桦", "source_id": "grant_" + "a" * 32,
                           "query": "IMG"}),
            ("fs_list", {"path": "1605 白桦", "source_id": "grant_" + "a" * 32,
                         "page_size": 50}),
        ]:
            asyncio.run(_execute_tool(context, name, args, False))
        self.assertEqual(len(toolbox.calls), 3)
        self.assertEqual(next(iter(deps.prerequisite_failures.values()))["count"], 3)
        self.assertEqual(deps.pause_blocker, "prerequisite_stalled")

    def test_new_observation_reopens_missing_prerequisite(self):
        missing = {
            "ok": False, "error": "path missing",
            "failure": {"stage": "preconditions", "code": "source_path_not_found",
                        "requested_path": "1605 白桦", "source_id": "grant_" + "a" * 32},
        }
        observed = {"ok": True, "data": {"items": [{"name": "1605白桦",
                                                "path": "1605白桦"}]}}
        toolbox = _StubToolbox([missing, observed, missing])
        deps = _dependencies(toolbox)
        context = _context(deps)
        grant = "grant_" + "a" * 32
        asyncio.run(_execute_tool(context, "fs_list", {"path": "1605 白桦", "source_id": grant}, False))
        asyncio.run(_execute_tool(context, "fs_list", {"path": ".", "source_id": grant}, False))
        asyncio.run(_execute_tool(context, "fs_search", {"path": "1605 白桦", "source_id": grant,
                                                         "query": "IMG"}, False))
        self.assertEqual(len(toolbox.calls), 3)
        self.assertEqual(next(iter(deps.prerequisite_failures.values()))["count"], 1)
        self.assertIsNone(deps.pause_reason)

    def test_unrelated_directory_observation_does_not_reset_missing_target(self):
        grant = "grant_" + "a" * 32
        missing = {"ok": False, "failure": {
            "stage": "preconditions", "code": "source_path_not_found",
            "requested_path": "1605 白桦", "source_id": grant,
        }}
        toolbox = _StubToolbox([
            missing,
            {"ok": True, "data": {"items": [{"name": "other.txt"}]}},
            missing, missing,
        ])
        deps = _dependencies(toolbox)
        context = _context(deps)
        asyncio.run(_execute_tool(context, "fs_list", {"source_id": grant,
                                                      "path": "1605 白桦"}, False))
        asyncio.run(_execute_tool(context, "fs_list", {"source_id": grant,
                                                      "path": "unrelated"}, False))
        asyncio.run(_execute_tool(context, "fs_search", {"source_id": grant,
                                                        "path": "1605 白桦", "query": "IMG"}, False))
        asyncio.run(_execute_tool(context, "fs_list", {"source_id": grant,
                                                      "path": "1605 白桦", "page": 2}, False))
        self.assertEqual(deps.evidence_generation, 0)
        self.assertEqual(deps.pause_blocker, "prerequisite_stalled")


class EnvironmentChangeTests(unittest.TestCase):
    """A retry after the environment changed is new work, not a repeat.

    Observed in the capability baseline on `capability.supervised`: the Agent's
    `code_run` was refused for missing sklearn, it installed sklearn successfully,
    and then the *identical* `code_run` was blocked as a duplicate failed call. The
    environment had changed in precisely the way that call needed, so the guard rail
    was suppressing the one retry that was certain to work.
    """

    BLOCKED = {
        "ok": False,
        "outcome_ok": False,
        "error": "Execution was not started: the code imports modules that are not "
                 "present in the dependency directory and have not been import-checked.",
        "failure": {"stage": "preconditions", "code": "blocked_by",
                    "blocked_by": "environment_check", "operation_started": False,
                    "side_effects": "none"},
    }
    INSTALLED = {"ok": True, "outcome_ok": True, "data": {
        "job_id": "job_x", "state": "succeeded",
        "verification": {"succeeded": True, "installed_packages": [
            {"name": "scikit-learn", "version": "1.5"},
        ]},
    }}

    def test_a_successful_install_reopens_the_call(self):
        toolbox = _StubToolbox([self.BLOCKED, self.INSTALLED, dict(self.BLOCKED) | {
            "ok": True, "outcome_ok": True, "data": {"job_id": "job_y"},
        }])
        deps = _dependencies(toolbox)
        context = _context(deps)
        call = {"language": "python", "code": "import sklearn"}

        first = asyncio.run(_execute_tool(context, "code_run", call, False))
        self.assertFalse(first["outcome_ok"])
        generation_before = deps.environment_generation

        installed = asyncio.run(_execute_tool(
            context, "dependency_install", {"packages": ["scikit-learn"]}, False
        ))
        self.assertTrue(installed["outcome_ok"])
        self.assertGreater(deps.environment_generation, generation_before)

        retried = asyncio.run(_execute_tool(context, "code_run", call, False))
        self.assertEqual(len(toolbox.calls), 3, "the retry must reach the environment")
        self.assertTrue(retried["outcome_ok"])

    def test_a_failed_install_does_not_reopen_the_call(self):
        toolbox = _StubToolbox([self.BLOCKED, dict(FAILED_INSTALL)])
        deps = _dependencies(toolbox)
        context = _context(deps)
        call = {"language": "python", "code": "import sklearn"}

        asyncio.run(_execute_tool(context, "code_run", call, False))
        asyncio.run(_execute_tool(
            context, "dependency_install", {"packages": ["scikit-learn"]}, False
        ))
        self.assertEqual(deps.environment_generation, 0)

        blocked = asyncio.run(_execute_tool(context, "code_run", call, False))
        self.assertEqual(len(toolbox.calls), 2)
        self.assertEqual(blocked["failure"]["code"], "duplicate_failed_call")

    def test_an_import_check_reopens_the_call(self):
        """Proving a module importable is evidence, even without installing anything."""
        toolbox = _StubToolbox([
            self.BLOCKED,
            {"ok": True, "outcome_ok": True, "data": {"modules": [{"module": "sklearn", "importable": True}], "importable": ["sklearn"]}},
            {"ok": True, "outcome_ok": True, "data": {"job_id": "job_z"}},
        ])
        deps = _dependencies(toolbox)
        context = _context(deps)
        call = {"language": "python", "code": "import sklearn"}

        asyncio.run(_execute_tool(context, "code_run", call, False))
        asyncio.run(_execute_tool(
            context, "environment_check", {"modules": ["sklearn"]}, False
        ))
        asyncio.run(_execute_tool(context, "code_run", call, False))
        self.assertEqual(len(toolbox.calls), 3)

    def test_repeating_identical_import_evidence_does_not_reopen_failed_code(self):
        check = {"ok": True, "data": {"importable": ["sklearn"]}}
        toolbox = _StubToolbox([self.BLOCKED, check, self.BLOCKED, check])
        deps = _dependencies(toolbox)
        context = _context(deps)
        code = {"language": "python", "code": "import sklearn"}
        asyncio.run(_execute_tool(context, "code_run", code, False))
        asyncio.run(_execute_tool(context, "environment_check", {"modules": ["sklearn"]}, False))
        asyncio.run(_execute_tool(context, "code_run", code, False))
        asyncio.run(_execute_tool(context, "environment_check", {"modules": ["sklearn"]}, False))
        blocked = asyncio.run(_execute_tool(context, "code_run", code, False))
        self.assertEqual(len(toolbox.calls), 4)
        self.assertEqual(blocked["failure"]["code"], "duplicate_failed_call")


class RepeatedInstallSubmissionTests(unittest.TestCase):
    """The install capability refuses a package set it has already seen fail."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        (self.workspace / ".runtime" / "deps").mkdir(parents=True)
        self.env = patch.dict(os.environ, {
            "RUNTIME_DATA_HOST_ROOT": str(self.root),
            "INSTALL_WAIT_SECONDS": "1",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def _capability(self):
        """A capability wired to a real workspace and a scripted job outcome.

        Only the attributes `environment_install` actually consults are installed:
        the durable-job submission and the wait are both replaced by stubs, so the
        test exercises the capability's own bookkeeping rather than a container.
        """
        from runtime.capabilities.dependency_install.service import (
            DependencyInstallCapability,
        )
        from runtime.capabilities.environment.service import EnvironmentCapability

        capability = DependencyInstallCapability()
        capability.workspace = self.workspace
        capability.chat_id = "chat-test"
        capability.cancelled = lambda: False
        capability.pause_requested = lambda: False
        capability.records = None
        capability.__class__.environment_install = (
            EnvironmentCapability.environment_install
        )
        capability._submit_install = lambda packages, timeout_seconds, system_packages=None: {  # type: ignore[method-assign]
            "ok": True,
            "data": {"job_id": "job_test000000000000000001", "state": "running",
                     "terminal": False},
        }
        capability.job_wait = lambda job_id, timeout_seconds=0: {  # type: ignore[method-assign]
            "state": "failed", "terminal": True, "exit_code": 1, "output": "boom",
        }
        return capability

    def test_same_package_set_is_refused_after_the_limit(self):
        """One retry is allowed; the third identical request is answered locally.

        The retry exists because a first failure can be transient.  Past that the
        argument list has not changed, so a container is never started again -- and
        the refusal points at what the image *does* provide, which is the answer the
        agent needs and cannot get from another identical `install_failed`.
        """
        from runtime.kernel.protocol import SubmissionRefused

        capability = self._capability()
        with self.assertRaises(SubmissionRefused) as first:
            capability.environment_install(["gdal"], timeout_seconds=5)
        self.assertEqual(first.exception.data["verification"]["missing_from_manifest"], ["gdal"])
        self.assertEqual(first.exception.failure_details["code"], "install_failed")
        self.assertEqual(first.exception.failure_details["attempts"], 1)

        with self.assertRaises(SubmissionRefused) as retry:
            capability.environment_install(["gdal"], timeout_seconds=300)
        self.assertEqual(retry.exception.failure_details["code"], "install_failed")

        with self.assertRaises(SubmissionRefused) as stopped:
            capability.environment_install(["gdal"], timeout_seconds=300)
        details = stopped.exception.failure_details
        self.assertEqual(details["code"], "repeated_install_failure")
        self.assertFalse(details["operation_started"])
        self.assertEqual(details["requested_requirements"], ["gdal"])
        self.assertEqual(details["last_code"], "install_failed")
        self.assertEqual(details["attempts"], 2)
        self.assertIn("image_modules", details)
        self.assertIn("guidance", details)

    def test_a_different_package_set_is_still_allowed(self):
        from runtime.kernel.protocol import SubmissionRefused

        capability = self._capability()
        for _ in range(3):
            try:
                capability.environment_install(["gdal"], timeout_seconds=5)
            except SubmissionRefused:
                pass
        with self.assertRaises(SubmissionRefused) as attempt:
            capability.environment_install(["scikit-learn"], timeout_seconds=5)
        self.assertEqual(attempt.exception.failure_details["code"], "install_failed")

    def test_a_successful_install_clears_the_failure_record(self):
        from runtime.kernel.protocol import SubmissionRefused

        capability = self._capability()
        with self.assertRaises(SubmissionRefused):
            capability.environment_install(["gdal"], timeout_seconds=5)
        manifest = self.workspace / ".runtime" / "deps" / "installed-packages.json"
        manifest.write_text('[{"name":"gdal","version":"3.9.0"}]', encoding="utf-8")
        capability.job_wait = lambda job_id, timeout_seconds=0: {  # type: ignore[method-assign]
            "state": "succeeded", "terminal": True, "exit_code": 0, "output": "ok",
        }
        result = capability.environment_install(["gdal"], timeout_seconds=5)
        self.assertTrue(result["verification"]["succeeded"])
        failures = self.workspace / ".runtime" / "deps" / "install-failures.json"
        self.assertNotIn("gdal", failures.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
