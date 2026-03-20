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

from epg2xml.__main__ import main
from epg2xml.config import ConfigHelpRequested, ConfigLoadError, ConfigUpgradeRequired


class MainEntryPointTests(unittest.TestCase):
    @patch("epg2xml.__main__.run", side_effect=ConfigHelpRequested())
    def test_main_returns_zero_when_help_requested(self, _mock_run):
        self.assertEqual(main(), 0)

    @patch("epg2xml.__main__.run", side_effect=ConfigUpgradeRequired("epg2xml.json"))
    def test_main_returns_zero_when_config_is_created(self, _mock_run):
        self.assertEqual(main(), 0)

    @patch("epg2xml.__main__.run", side_effect=ConfigLoadError("epg2xml.json"))
    def test_main_returns_one_on_config_error(self, _mock_run):
        self.assertEqual(main(), 1)


if __name__ == "__main__":
    unittest.main()
