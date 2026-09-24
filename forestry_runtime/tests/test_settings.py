"""Tunable values: where they come from, and that the panel reports it.

These settings used to be read with `os.getenv` at thirty call sites, so "what is the
model-call limit and why" had no answer in the product -- only a grep, followed by
working out whether the number came from the container environment, the deployment
`.env`, or a default literal. The limit that ended a real Run was one of them.

So the tests here are mostly about *provenance* and *ordering*, not about storing a
number: a value written from the panel must outrank the environment, a rejected value
must leave the stored settings untouched, and clearing must genuinely return a value
to the deployment rather than pinning a copy of it.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from runtime.settings import (
    BY_KEY,
    GROUPS,
    SCOPE_PER_RUN,
    SCOPE_STARTUP,
    SETTINGS,
    Setting,
    SettingError,
    panel,
    resolve,
)
from runtime.storage import Store


class RegistryTests(unittest.TestCase):
    def test_every_setting_declares_what_the_panel_needs(self):
        for setting in SETTINGS:
            with self.subTest(key=setting.key):
                self.assertIn(setting.kind, {"int", "float", "bool", "text"})
                self.assertTrue(setting.group)
                self.assertTrue(setting.help, "the panel shows help text, so it is required")
                self.assertIn(setting.scope, {SCOPE_PER_RUN, SCOPE_STARTUP})
                self.assertIn(setting.group, GROUPS)

    def test_keys_are_unique(self):
        self.assertEqual(len(SETTINGS), len(BY_KEY))

    def test_the_model_call_limit_is_tunable_and_live(self):
        """The value that ended a real Run must be reachable from the panel."""
        setting = BY_KEY["AGENT_MAX_ROUNDS"]
        self.assertEqual(setting.kind, "int")
        self.assertEqual(setting.scope, SCOPE_PER_RUN)

    def test_credentials_and_deployment_paths_are_not_tunable(self):
        """A settings panel is a convenience surface, not a place for secrets."""
        for key in ("RUNTIME_API_KEY", "HOST_BRIDGE_KEY", "HOST_BRIDGE_URL",
                    "OLLAMA_URL", "RUNTIME_DATA_HOST_ROOT", "DATA_ROOT",
                    "KNOWLEDGE_ROOTS"):
            with self.subTest(key=key):
                self.assertNotIn(key, BY_KEY)


class CoercionTests(unittest.TestCase):
    def test_int_rejects_text_and_out_of_range(self):
        setting = BY_KEY["AGENT_MAX_ROUNDS"]
        self.assertEqual(setting.coerce("120"), "120")
        with self.assertRaises(SettingError):
            setting.coerce("many")
        with self.assertRaises(SettingError):
            setting.coerce("0")
        with self.assertRaises(SettingError):
            setting.coerce("999999")

    def test_bool_accepts_the_spellings_an_operator_might_type(self):
        setting = BY_KEY["DOMAIN_GUIDES_ENABLED"]
        for truthy in ("true", "TRUE", "1", "yes", "on"):
            self.assertEqual(setting.coerce(truthy), "true")
        for falsy in ("false", "0", "no", "off"):
            self.assertEqual(setting.coerce(falsy), "false")
        with self.assertRaises(SettingError):
            setting.coerce("maybe")

    def test_float_range_is_enforced(self):
        setting = BY_KEY["OLLAMA_TEMPERATURE"]
        self.assertEqual(float(setting.coerce("0.7")), 0.7)
        with self.assertRaises(SettingError):
            setting.coerce("3")

    def test_every_setting_has_a_usable_default(self):
        """A default the registry itself rejects is a latent startup failure."""
        for setting in SETTINGS:
            with self.subTest(key=setting.key):
                setting.coerce(setting.default)


class ResolutionTests(unittest.TestCase):
    def test_order_is_override_then_environment_then_default(self):
        key = "AGENT_MAX_ROUNDS"
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(key, None)
            self.assertEqual(resolve(key, {}).source, "default")
            self.assertEqual(resolve(key, {}).value, "32")

            os.environ[key] = "77"
            self.assertEqual(resolve(key, {}).source, "env")
            self.assertEqual(resolve(key, {}).value, "77")

            # The panel outranks the deployment, which is the whole point of it.
            self.assertEqual(resolve(key, {key: "120"}).source, "override")
            self.assertEqual(resolve(key, {key: "120"}).value, "120")

    def test_a_stale_override_that_no_longer_validates_falls_through(self):
        """Tightening a range must not brick the Runtime on a stored value."""
        key = "AGENT_MAX_ROUNDS"
        resolution = resolve(key, {key: "0"})
        self.assertNotEqual(resolution.source, "override")
        key_row = BY_KEY[key]
        self.assertEqual(resolution.value, key_row.coerce(key_row.default))

    def test_startup_scoped_values_say_they_need_a_restart(self):
        row = next(r for r in panel({})["groups"] for r in r["settings"]
                   if r["key"] == "AGENT_MAX_ROUNDS")
        self.assertEqual(row["applies"], "next_run")

    def test_panel_groups_every_setting_exactly_once(self):
        payload = panel({})
        seen = [row["key"] for group in payload["groups"] for row in group["settings"]]
        self.assertEqual(sorted(seen), sorted(BY_KEY))
        self.assertEqual(len(seen), len(set(seen)))

    def test_unknown_key_is_refused(self):
        with self.assertRaises(SettingError):
            resolve("NOT_A_SETTING", {})


class StoreTests(unittest.TestCase):
    def test_overrides_round_trip_and_clear(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            self.assertEqual(store.setting_overrides(), {})
            store.set_setting("AGENT_MAX_ROUNDS", "120", updated_by="ui")
            self.assertEqual(store.setting_overrides()["AGENT_MAX_ROUNDS"], "120")
            store.set_setting("AGENT_MAX_ROUNDS", "150", updated_by="ui")
            self.assertEqual(store.setting_overrides()["AGENT_MAX_ROUNDS"], "150")
            store.clear_setting("AGENT_MAX_ROUNDS")
            self.assertEqual(store.setting_overrides(), {})

    def test_settings_survive_reopening_the_store(self):
        with tempfile.TemporaryDirectory() as root:
            Store(root).set_setting("AGENT_MAX_ROUNDS", "120")
            self.assertEqual(Store(root).setting_overrides()["AGENT_MAX_ROUNDS"], "120")


class AgentUsesTheRegistryTests(unittest.TestCase):
    def test_the_loop_reads_the_limit_through_the_registry(self):
        """The number the panel shows must be the number the loop enforces."""
        from runtime.agent import _setting_value

        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            self.assertEqual(_setting_value(store, "AGENT_MAX_ROUNDS"), 32)
            store.set_setting("AGENT_MAX_ROUNDS", "120")
            self.assertEqual(_setting_value(store, "AGENT_MAX_ROUNDS"), 120)

    def test_a_store_without_settings_accessors_still_resolves(self):
        """Tests that drive the loop directly pass no store, or a bare stub."""
        from runtime.agent import _setting_value

        self.assertEqual(_setting_value(None, "AGENT_MAX_ROUNDS"), 32)

        class Bare:
            pass

        self.assertEqual(_setting_value(Bare(), "AGENT_MAX_ROUNDS"), 32)

    def test_a_failing_settings_read_cannot_take_down_a_run(self):
        from runtime.agent import _setting_value

        class Broken:
            def setting_overrides(self):
                raise RuntimeError("database is locked")

        self.assertEqual(_setting_value(Broken(), "AGENT_MAX_ROUNDS"), 32)


if __name__ == "__main__":
    unittest.main()
