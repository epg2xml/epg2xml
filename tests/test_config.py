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


if __name__ == "__main__":
    unittest.main()
