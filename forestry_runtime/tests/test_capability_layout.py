import unittest

from runtime.capabilities.runtime import RuntimeTools, _REGISTRY
from runtime.capabilities.domain_runtime import RemoteSensingTools
from runtime.store.executions import ExecutionRecords


class CapabilityLayoutTests(unittest.TestCase):
    def test_parameter_contracts_live_with_their_capabilities(self):
        expected_modules = {
            "fs_list": "runtime.capabilities.fs.tool",
            "fs_read": "runtime.capabilities.fs.tool",
            "fs_search": "runtime.capabilities.fs.tool",
            "fs_write": "runtime.capabilities.fs.tool",
            "fs_edit": "runtime.capabilities.fs.tool",
            "code_run": "runtime.capabilities.code_run.tool",
            "dependency_install": "runtime.capabilities.dependency_install.tool",
            "job_status": "runtime.capabilities.job_status.tool",
            "job_cancel": "runtime.capabilities.job_cancel.tool",
            "tool_result_read": "runtime.capabilities.artifacts.tool",
            "artifacts_inspect": "runtime.capabilities.artifacts.tool",
            "artifacts_preview": "runtime.capabilities.artifacts.tool",
            "knowledge_search": "runtime.capabilities.knowledge_search.tool",
            "knowledge_read": "runtime.capabilities.knowledge_search.tool",
        }
        self.assertEqual(set(expected_modules), {spec.name for spec in _REGISTRY})
        for name, module in expected_modules.items():
            with self.subTest(name=name):
                self.assertEqual(_REGISTRY.require(name).params.__module__, module)

    def test_runtime_toolbox_is_composed_by_capability_loader(self):
        self.assertEqual(RuntimeTools.__module__, "runtime.capabilities.runtime")

    def test_filesystem_capability_owns_filesystem_methods(self):
        for method in ("fs_list", "fs_read", "fs_search", "fs_write", "fs_edit"):
            with self.subTest(method=method):
                self.assertEqual(
                    getattr(RuntimeTools, method).__module__,
                    "runtime.capabilities.fs.service",
                )

    def test_artifact_capability_owns_artifact_methods(self):
        for method in (
            "artifacts_inspect", "artifacts_preview", "tool_result_read",
            "store_tool_result",
        ):
            with self.subTest(method=method):
                self.assertEqual(
                    getattr(RuntimeTools, method).__module__,
                    "runtime.capabilities.artifacts.service",
                )

    def test_knowledge_capability_owns_knowledge_methods(self):
        for method in ("knowledge_search", "knowledge_read"):
            with self.subTest(method=method):
                self.assertEqual(
                    getattr(RuntimeTools, method).__module__,
                    "runtime.capabilities.knowledge_search.service",
                )

    def test_execution_capabilities_and_store_own_job_submission(self):
        self.assertEqual(
            RuntimeTools.code_run.__module__,
            "runtime.capabilities.code_run.service",
        )
        self.assertEqual(
            RuntimeTools.dependency_install.__module__,
            "runtime.capabilities.dependency_install.service",
        )
        self.assertEqual(ExecutionRecords.__module__, "runtime.store.executions")

    def test_job_capabilities_own_job_observation_and_cancel(self):
        self.assertEqual(
            RuntimeTools.job_status.__module__,
            "runtime.capabilities.job_status.service",
        )
        self.assertEqual(
            RuntimeTools.job_cancel.__module__,
            "runtime.capabilities.job_cancel.service",
        )

    def test_remote_sensing_methods_are_split_by_domain(self):
        expected = {
            "inspect_raster": "runtime.capabilities.raster.service",
            "calculate_ndvi": "runtime.capabilities.raster.service",
            "inspect_uav_source": "runtime.capabilities.uav_audit.service",
            "inspect_uav_products": "runtime.capabilities.uav_audit.service",
            "build_canopy_height_model": (
                "runtime.capabilities.forest_structure.service"
            ),
            "summarize_forest_structure": (
                "runtime.capabilities.forest_structure.service"
            ),
            "simulate_prosail": "runtime.capabilities.prosail.service",
            "invert_prosail": "runtime.capabilities.prosail.service",
        }
        for name, module in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(RemoteSensingTools, name).__module__, module)


if __name__ == "__main__":
    unittest.main()
