from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wave_delivery.config import (
    default_config,
    load_config,
    save_config,
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


if __name__ == "__main__":
    unittest.main()
