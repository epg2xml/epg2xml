import sys
import types
import unittest


bs4 = types.ModuleType("bs4")


class DummyBeautifulSoup:
    def __init__(self, *args, **kwargs):
        pass


class DummyFeatureNotFound(Exception):
    pass


bs4.BeautifulSoup = DummyBeautifulSoup
bs4.FeatureNotFound = DummyFeatureNotFound
sys.modules.setdefault("bs4", bs4)

from epg2xml.providers import DuplicateChannelIdError, EPGChannel, EPGHandler


class FakeProvider:
    def __init__(self, provider_name, req_channels):
        self.provider_name = provider_name
        self.req_channels = req_channels

    def load_req_channels(self):
        return None


class TestDuplicateChannelIds(unittest.TestCase):
    def make_handler(self, *providers):
        handler = EPGHandler.__new__(EPGHandler)
        handler.providers = list(providers)
        return handler

    def test_load_req_channels_raises_on_duplicate_channel_ids(self):
        kt = FakeProvider("KT", [EPGChannel("shared.id", "KT", "svc1", "KBS 1")])
        lg = FakeProvider("LG", [EPGChannel("shared.id", "LG", "svc2", "KBS 1")])
        handler = self.make_handler(kt, lg)

        with self.assertRaises(DuplicateChannelIdError) as ctx:
            handler.load_req_channels()

        self.assertIn("shared.id", str(ctx.exception))
        self.assertIn("'shared.id': 2", str(ctx.exception))

    def test_load_req_channels_allows_unique_channel_ids(self):
        kt = FakeProvider("KT", [EPGChannel("kt.id", "KT", "svc1", "KBS 1")])
        lg = FakeProvider("LG", [EPGChannel("lg.id", "LG", "svc2", "KBS 1")])
        handler = self.make_handler(kt, lg)

        handler.load_req_channels()


if __name__ == "__main__":
    unittest.main()
