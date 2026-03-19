import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
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

from epg2xml.providers import EPGChannel, EPGHandler, EPGProgram, EPGProvider, SQLite


CFG = {
    "ENABLED": True,
    "FETCH_LIMIT": 2,
    "ID_FORMAT": "{ServiceId}.{Source.lower()}",
    "ADD_REBROADCAST_TO_TITLE": False,
    "ADD_EPNUM_TO_TITLE": True,
    "ADD_DESCRIPTION": True,
    "ADD_XMLTV_NS": False,
    "GET_MORE_DETAILS": False,
    "ADD_CHANNEL_ICON": True,
    "HTTP_PROXY": None,
    "MY_CHANNELS": [],
}


class FAKE(EPGProvider):
    def __init__(self, cfg):
        self.fetch_count = 0
        self.to_return = []
        super().__init__(cfg)

    def get_svc_channels(self):
        self.fetch_count += 1
        return list(self.to_return)

    def get_programs(self):
        raise NotImplementedError


class FakeHandlerProvider:
    def __init__(self, error=None):
        self.error = error
        self.was_channel_updated = False
        self.provider_name = "FAKE"
        self.svc_channels = []

    def load_svc_channels(self, channeljson=None):
        if self.error is not None:
            raise self.error

    def get_programs(self):
        if self.error is not None:
            raise self.error


class DummySession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.proxies = {}
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return DummyResponse()


class DummyResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class TestProvider(unittest.TestCase):
    def make_handler(self, *providers):
        handler = EPGHandler.__new__(EPGHandler)
        handler.providers = list(providers)
        return handler

    def test_load_svc_channels_uses_recent_cache(self):
        with patch("epg2xml.providers.requests.Session", DummySession):
            provider = FAKE(dict(CFG))
        cached_channels = [{"Name": "A", "ServiceId": "1"}]
        channeljson = {
            "FAKE": {
                "UPDATED": datetime.now().isoformat(),
                "TOTAL": len(cached_channels),
                "CHANNELS": cached_channels,
            }
        }

        provider.load_svc_channels(channeljson=channeljson)

        self.assertEqual(provider.svc_channels, cached_channels)
        self.assertEqual(provider.fetch_count, 0)
        self.assertFalse(provider.was_channel_updated)

    def test_load_svc_channels_fetches_when_cache_is_broken(self):
        with patch("epg2xml.providers.requests.Session", DummySession):
            provider = FAKE(dict(CFG))
        provider.to_return = [{"Name": "B", "ServiceId": "2"}]
        broken_channeljson = {
            "FAKE": {
                "UPDATED": datetime.now().isoformat(),
                "TOTAL": 999,
                "CHANNELS": [],
            }
        }

        provider.load_svc_channels(channeljson=broken_channeljson)

        self.assertEqual(provider.svc_channels, provider.to_return)
        self.assertEqual(provider.fetch_count, 1)
        self.assertTrue(provider.was_channel_updated)

    def test_load_svc_channels_fetches_when_cache_is_outdated(self):
        with patch("epg2xml.providers.requests.Session", DummySession):
            provider = FAKE(dict(CFG))
        provider.to_return = [{"Name": "C", "ServiceId": "3"}]
        stale_channeljson = {
            "FAKE": {
                "UPDATED": (datetime.now() - timedelta(days=5)).isoformat(),
                "TOTAL": 1,
                "CHANNELS": [{"Name": "OLD", "ServiceId": "0"}],
            }
        }

        provider.load_svc_channels(channeljson=stale_channeljson)

        self.assertEqual(provider.svc_channels, provider.to_return)
        self.assertEqual(provider.fetch_count, 1)
        self.assertTrue(provider.was_channel_updated)

    def test_request_uses_default_timeout_and_status_check(self):
        with patch("epg2xml.providers.requests.Session", DummySession):
            provider = FAKE(dict(CFG))

        response = provider.request("https://example.com")

        self.assertEqual(response, {"ok": True})
        self.assertEqual(provider.sess.calls[0]["timeout"], provider.timeout)
        self.assertEqual(provider.sess.calls[0]["url"], "https://example.com")

    def test_load_channels_parallel_propagates_worker_exceptions(self):
        handler = self.make_handler(FakeHandlerProvider(RuntimeError("boom")))

        with self.assertRaises(RuntimeError):
            handler.load_channels("unused.json", parallel=True)

    def test_get_programs_parallel_propagates_worker_exceptions(self):
        handler = self.make_handler(FakeHandlerProvider(RuntimeError("boom")))

        with self.assertRaises(RuntimeError):
            handler.get_programs(parallel=True)

    def test_sqlite_round_trip_preserves_channel_and_program_order(self):
        channel = EPGChannel("kt.id", "KT", "svc1", "Channel A")
        channel.no = "101"
        programs = [
            EPGProgram("kt.id", stime=datetime(2026, 1, 1, 10, 0), etime=datetime(2026, 1, 1, 11, 0), title="B"),
            EPGProgram("kt.id", stime=datetime(2026, 1, 1, 9, 0), etime=datetime(2026, 1, 1, 10, 0), title="A"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            dbfile = Path(tmpdir) / "epg.db"
            with SQLite(dbfile, "w") as db:
                db.insert_channels([channel])
                db.insert_programs(programs)

            with SQLite(dbfile, "r") as db:
                loaded_channels = db.select_channels("KT")
                loaded_programs = db.select_programs("kt.id")

        self.assertEqual(len(loaded_channels), 1)
        self.assertEqual(loaded_channels[0].id, "kt.id")
        self.assertEqual([program.title for program in loaded_programs], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
