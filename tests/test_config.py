import sys
import tempfile
import types
import unittest
import os
from pathlib import Path
from unittest.mock import patch


bs4 = types.ModuleType("bs4")


class DummyBeautifulSoup:
    def __init__(self, *args, **kwargs):
        pass


class DummyFeatureNotFound(Exception):
    pass


bs4.BeautifulSoup = DummyBeautifulSoup
bs4.FeatureNotFound = DummyFeatureNotFound
sys.modules.setdefault("bs4", bs4)

from epg2xml.config import Config, ConfigUpgradeRequired
from epg2xml.providers.all import PROVIDERS
from epg2xml.utils import load_json, strip_json_comments


class TestConfig(unittest.TestCase):
    def test_config_creates_distinct_instances(self):
        with patch.object(Config, "parse_args", return_value={"cmd": "run"}), patch.object(
            Config, "get_settings", return_value={}
        ):
            first = Config()
            second = Config()

        self.assertIsNot(first, second)

    def test_default_config_is_a_deep_copy(self):
        with patch.object(Config, "parse_args", return_value={"cmd": "run"}), patch.object(
            Config, "get_settings", return_value={}
        ):
            config = Config()

        default_config = config.default_config
        default_config["KT"]["MY_CHANNELS"].append({"ServiceId": "100"})
        default_config["GLOBAL"]["HTTP_PROXY"] = "http://proxy"

        self.assertEqual(Config.base_config["KT"]["MY_CHANNELS"], [])
        self.assertIsNone(Config.base_config["GLOBAL"]["HTTP_PROXY"])

    def test_base_config_contains_all_registry_providers(self):
        for provider in PROVIDERS:
            self.assertIn(provider.name.upper(), Config.base_config)
            self.assertEqual(Config.base_config[provider.name.upper()], {"MY_CHANNELS": []})

    def test_load_creates_missing_config_and_raises_upgrade_required(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "epg2xml.json"
            with patch.object(Config, "parse_args", return_value={"cmd": "run"}), patch.object(
                Config, "get_settings", return_value={"config": str(config_path)}
            ):
                config = Config()

            with self.assertRaises(ConfigUpgradeRequired):
                config.load()

            self.assertTrue(config_path.exists())

    def test_strip_json_comments_removes_line_and_block_comments(self):
        source = '{\n  "a": 1, // comment\n  /* block */ "b": 2\n}'

        stripped = strip_json_comments(source)

        self.assertEqual(stripped, '{\n  "a": 1, \n   "b": 2\n}')

    def test_strip_json_comments_preserves_comment_tokens_inside_strings(self):
        source = '{ "url": "http://a//b", "text": "/* keep */" }'

        self.assertEqual(strip_json_comments(source), source)

    def test_load_json_accepts_comments(self):
        source = '{\n  "a": 1, // comment\n  "url": "http://a//b",\n  /* block */ "b": 2\n}'

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(source, encoding="utf-8")

            self.assertEqual(load_json(config_path), {"a": 1, "url": "http://a//b", "b": 2})

    def test_get_settings_prefers_args_over_env_and_default(self):
        with patch.object(
            Config,
            "parse_args",
            return_value={"cmd": "run", "loglevel": "ERROR", "parallel": False},
        ), patch.dict(os.environ, {"EPG2XML_LOGLEVEL": "DEBUG"}, clear=False):
            config = Config()

        self.assertEqual(config.settings["loglevel"], "ERROR")

    def test_get_settings_coerces_parallel_env_value(self):
        args = {"cmd": "run"}
        args.update({name: None for name in Config.base_settings})
        with patch.object(Config, "parse_args", return_value=args), patch.dict(
            os.environ,
            {"EPG2XML_PARALLEL": "true"},
            clear=False,
        ):
            config = Config()

        self.assertTrue(config.settings["parallel"])


if __name__ == "__main__":
    unittest.main()
