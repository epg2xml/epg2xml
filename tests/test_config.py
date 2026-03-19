import sys
import types
import unittest
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

from epg2xml.config import Config


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


if __name__ == "__main__":
    unittest.main()
