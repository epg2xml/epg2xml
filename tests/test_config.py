import sys
import tempfile
import types
import unittest
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


if __name__ == "__main__":
    unittest.main()
