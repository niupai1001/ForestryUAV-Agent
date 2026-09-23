"""An unimportable module must say *why*, because two opposite states share a message.

Observed in a real Run against this Runtime. The model installed ``numpy`` and
``rasterio``; pip reported success and the distributions landed in the dependency
directory. ``environment_check`` then reported both as unimportable:

    rasterio  importable=False  ModuleNotFoundError: No module named 'rasterio'

That is the same message a module that was never installed produces. The two
states call for opposite actions -- install it, versus stop installing -- and with
one message for both the model retried six different package sets until its
32-call budget was spent. The missing piece was a system library (``libexpat.so.1``)
inside the job image, which no install inside the container can supply.

These tests pin the distinction and the fact that it is *reported*, not acted on:
the Runtime supplies the observation and leaves the decision to the model.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic_ai import RunContext

from runtime.agent import AgentDependencies, _execute_tool
from runtime.capabilities.environment.service import EnvironmentCapability

try:  # the usage type moved between pydantic_ai releases
    from pydantic_ai.usage import RunUsage
except ImportError:  # pragma: no cover - older layout
    from pydantic_ai import Usage as RunUsage


LOADER_ERROR = (
    "ImportError: libexpat.so.1: cannot open shared object file: "
    "No such file or directory"
)


def _capability(workspace: Path) -> EnvironmentCapability:
    """A capability whose only real state is the dependency directory."""
    capability = object.__new__(EnvironmentCapability)
    capability.workspace = workspace
    return capability


def _deps_tree(root: Path, installed: list[str]) -> Path:
    """Create the dependency directory as the installer leaves it on disk."""
    deps = root / ".runtime" / "deps"
    deps.mkdir(parents=True, exist_ok=True)
    for name in installed:
        (deps / name).mkdir(exist_ok=True)
        (deps / f"{name}-1.0.0.dist-info").mkdir(exist_ok=True)
    return deps


def _probe_row(module: str, *, importable: bool, error: str | None = None) -> dict:
    return {
        "module": module, "importable": importable, "version": "1.5.1" if importable else None,
        "distributions": [], "error": error, "origin": None,
    }


class ModuleStateTests(unittest.TestCase):
    def test_absent_module_is_reported_as_installable(self):
        with tempfile.TemporaryDirectory() as raw:
            capability = _capability(Path(raw))
            _deps_tree(Path(raw), [])
            facts = capability._classify_modules([
                _probe_row("rasterio", importable=False,
                           error="ModuleNotFoundError: No module named 'rasterio'"),
            ])
            self.assertEqual(facts[0]["state"], "absent")
            self.assertEqual(facts[0]["cause_scope"], "deps")
            self.assertTrue(facts[0]["remedy_reachable"])

    def test_installed_but_unimportable_is_not_reported_as_absent(self):
        """The regression: a module on disk that cannot load is not a missing module."""
        with tempfile.TemporaryDirectory() as raw:
            capability = _capability(Path(raw))
            _deps_tree(Path(raw), ["rasterio", "numpy"])
            facts = capability._classify_modules([
                _probe_row("rasterio", importable=False,
                           error="ModuleNotFoundError: No module named 'rasterio'"),
                _probe_row("numpy", importable=False,
                           error="ModuleNotFoundError: No module named 'numpy'"),
            ])
            for fact in facts:
                self.assertEqual(fact["state"], "present_not_importable")
                self.assertTrue(fact["present_in_deps"])
                self.assertNotEqual(fact["state"], "absent")

    def test_missing_system_library_is_named_and_marked_as_image_scope(self):
        with tempfile.TemporaryDirectory() as raw:
            capability = _capability(Path(raw))
            _deps_tree(Path(raw), ["rasterio"])
            facts = capability._classify_modules([
                _probe_row("rasterio", importable=False, error=LOADER_ERROR),
            ])
            fact = facts[0]
            self.assertEqual(fact["state"], "present_not_importable")
            self.assertEqual(fact["missing_system_library"], "libexpat.so.1")
            self.assertEqual(fact["cause_scope"], "image")
            # The observation says the remedy is out of reach inside the container.
            self.assertFalse(fact["remedy_reachable"])

    def test_installed_but_broken_without_a_loader_error_stays_a_deps_problem(self):
        """A broken install is retryable; a missing image library is not."""
        with tempfile.TemporaryDirectory() as raw:
            capability = _capability(Path(raw))
            _deps_tree(Path(raw), ["rasterio"])
            facts = capability._classify_modules([
                _probe_row("rasterio", importable=False,
                           error="ImportError: cannot import name '_base' from partially "
                                 "initialized module 'rasterio'"),
            ])
            self.assertEqual(facts[0]["state"], "present_not_importable")
            self.assertIsNone(facts[0]["missing_system_library"])
            self.assertEqual(facts[0]["cause_scope"], "deps")
            self.assertTrue(facts[0]["remedy_reachable"])

    def test_importable_module_reports_no_remedy_need(self):
        with tempfile.TemporaryDirectory() as raw:
            capability = _capability(Path(raw))
            _deps_tree(Path(raw), ["numpy"])
            facts = capability._classify_modules([_probe_row("numpy", importable=True)])
            self.assertEqual(facts[0]["state"], "importable")
            self.assertIsNone(facts[0]["cause_scope"])
            self.assertIsNone(facts[0]["remedy_reachable"])

    def test_unprobed_module_yields_no_state_claim(self):
        """A module name with no probe row must not be reported as absent."""
        with tempfile.TemporaryDirectory() as raw:
            capability = _capability(Path(raw))
            _deps_tree(Path(raw), [])
            facts = capability._classify_modules([_probe_row("", importable=False)])
            self.assertEqual(facts, [])


class EnvironmentCheckTests(unittest.TestCase):
    def _check(self, workspace: Path, modules: list[str], probe: dict) -> dict:
        capability = _capability(workspace)
        with patch.object(EnvironmentCapability, "_probe_modules", return_value=probe):
            return capability.environment_check(modules=modules)

    def _probe(self, rows: list[dict]) -> dict:
        return {
            "image": "python:3.12-slim", "dependency_mount": "/deps",
            "pythonpath": "/workspace/.runtime/deps", "exit_code": 0,
            "modules": rows, "checked_at": 0.0, "installed_packages": [],
        }

    def test_check_reports_states_alongside_the_existing_boolean(self):
        with tempfile.TemporaryDirectory() as raw:
            _deps_tree(Path(raw), ["rasterio"])
            result = self._check(Path(raw), ["rasterio", "numpy"], self._probe([
                _probe_row("rasterio", importable=False, error=LOADER_ERROR),
                _probe_row("numpy", importable=False,
                           error="ModuleNotFoundError: No module named 'numpy'"),
            ]))
            # The pre-existing contract is unchanged:
            self.assertEqual(result["importable"], [])
            self.assertEqual(
                [item["module"] for item in result["not_importable"]], ["rasterio", "numpy"]
            )
            # And the two are now distinguished:
            self.assertEqual(
                result["module_states"],
                {"rasterio": "present_not_importable", "numpy": "absent"},
            )

    def test_check_summarises_an_unreachable_remedy_without_instructing(self):
        with tempfile.TemporaryDirectory() as raw:
            _deps_tree(Path(raw), ["rasterio"])
            result = self._check(Path(raw), ["rasterio"], self._probe([
                _probe_row("rasterio", importable=False, error=LOADER_ERROR),
            ]))
            note = result.get("environment_note", "")
            self.assertIn("libexpat.so.1", note)
            self.assertIn("job image", note)
            # It states the fact; it does not forbid an action.
            for imperative in ("do not", "must not", "stop"):
                self.assertNotIn(imperative, note.casefold())

    def test_busy_environment_still_refuses_to_claim_a_state(self):
        """A check that never ran must not report modules as absent."""
        capability = _capability(Path("/nonexistent"))
        with patch.object(EnvironmentCapability, "_probe_modules", return_value={
            "resource_busy": True, "error": "slot occupied", "modules": [],
        }):
            result = capability.environment_check(modules=["rasterio"])
        self.assertTrue(result["resource_busy"])
        self.assertNotIn("module_states", result)
        self.assertNotIn("module_facts", result)


class ModelStillDecidesTests(unittest.TestCase):
    """The facts reach the model; the model is not told what to conclude."""

    def test_environment_check_facts_reach_the_model_through_the_tool_loop(self):
        captured: list[dict] = []

        class _Toolbox:
            def execute(self, name, arguments, progress=None):
                captured.append({"name": name, "arguments": dict(arguments)})
                return {
                    "ok": True,
                    "data": {
                        "image": "python:3.12-slim",
                        "modules": [_probe_row("rasterio", importable=False, error=LOADER_ERROR)],
                        "module_facts": [{
                            "module": "rasterio", "importable": False,
                            "state": "present_not_importable", "present_in_deps": True,
                            "missing_system_library": "libexpat.so.1",
                            "cause_scope": "image", "remedy_reachable": False,
                        }],
                        "module_states": {"rasterio": "present_not_importable"},
                    },
                }

        deps = AgentDependencies(
            toolbox=_Toolbox(), cancelled=lambda: False, pause_requested=lambda: False,
        )
        context = RunContext(deps=deps, model=None, usage=RunUsage())
        outcome = asyncio.run(_execute_tool(context, "environment_check",
                                            {"modules": ["rasterio"]}, False))
        self.assertEqual(captured[0]["name"], "environment_check")
        data = outcome["data"]
        self.assertEqual(data["module_states"]["rasterio"], "present_not_importable")
        # Nothing in the tool result tells the model to stop or to continue.
        serialised = str(data).casefold()
        for imperative in ("do not", "must not", "you should", "stop trying"):
            self.assertNotIn(imperative, serialised)


if __name__ == "__main__":
    unittest.main()
