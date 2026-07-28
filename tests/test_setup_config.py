from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from wave_delivery.cli import main
from wave_delivery.config import (
    default_config,
    get_value,
    load_config,
    save_config,
    set_value,
    validate_config,
)
from wave_delivery.errors import ValidationError


class ConfigValidationTest(unittest.TestCase):
    def test_default_config_validates(self) -> None:
        validate_config(default_config())

    def test_default_config_is_a_fresh_copy(self) -> None:
        first = default_config()
        first["merge"]["surface"] = "local"
        self.assertEqual(default_config()["merge"]["surface"], "pr")

    def test_rejects_unknown_merge_surface(self) -> None:
        config = default_config()
        config["merge"]["surface"] = "carrier-pigeon"
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_bad_risk_rule(self) -> None:
        config = default_config()
        config["riskRules"] = [{"pattern": "src/**", "risk": "extreme"}]
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_non_local_task_provider(self) -> None:
        config = default_config()
        config["taskProvider"]["type"] = "jira"
        with self.assertRaises(ValidationError):
            validate_config(config)

    def test_rejects_malformed_open_question(self) -> None:
        config = default_config()
        config["openQuestions"] = [{"question": "no path key"}]
        with self.assertRaises(ValidationError):
            validate_config(config)


class ConfigStorageTest(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            config = default_config()
            config["verification"]["commands"] = ["pytest -q"]
            save_config(wdd, config)
            self.assertEqual(load_config(wdd), config)

    def test_load_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValidationError):
                load_config(Path(tmp) / ".wdd")

    def test_load_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            wdd.mkdir()
            (wdd / "config.json").write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_config(wdd)


class ConfigPathAccessTest(unittest.TestCase):
    def test_get_value_walks_dotted_path(self) -> None:
        self.assertEqual(get_value(default_config(), "merge.surface"), "pr")

    def test_get_value_unknown_path_raises(self) -> None:
        with self.assertRaises(ValidationError):
            get_value(default_config(), "merge.velocity")

    def test_set_value_returns_validated_copy(self) -> None:
        config = default_config()
        updated = set_value(config, "merge.surface", "local")
        self.assertEqual(updated["merge"]["surface"], "local")
        self.assertEqual(config["merge"]["surface"], "pr")

    def test_set_value_rejects_invalid_value(self) -> None:
        with self.assertRaises(ValidationError):
            set_value(default_config(), "merge.surface", "carrier-pigeon")

    def test_set_value_resolves_matching_open_question(self) -> None:
        config = default_config()
        config["openQuestions"] = [
            {"path": "merge.surface", "question": "pr or local?", "options": ["pr", "local"]}
        ]
        updated = set_value(config, "merge.surface", "local")
        self.assertEqual(updated["openQuestions"], [])


class ConfigCliTest(unittest.TestCase):
    def _run(self, tmp: str, *argv: str) -> tuple[int, str]:
        state = str(Path(tmp) / ".wdd" / "state.json")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--state", state, *argv])
        return code, stdout.getvalue()

    def test_config_set_then_get_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            save_config(wdd, default_config())
            code, _ = self._run(tmp, "config", "set", "verification.commands", '["pytest -q"]')
            self.assertEqual(code, 0)
            code, out = self._run(tmp, "config", "get", "verification.commands")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), ["pytest -q"])

    def test_config_set_bare_string_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wdd = Path(tmp) / ".wdd"
            save_config(wdd, default_config())
            code, _ = self._run(tmp, "config", "set", "merge.surface", "local")
            self.assertEqual(code, 0)
            self.assertEqual(load_config(wdd)["merge"]["surface"], "local")


if __name__ == "__main__":
    unittest.main()
