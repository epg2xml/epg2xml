import io
import sys
import tempfile
import types
import unittest
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
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
from epg2xml.providers.spotv import SPOTV
from epg2xml.utils import time_to_td

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
        del channeljson
        if self.error is not None:
            raise self.error

    def get_programs(self):
        if self.error is not None:
            raise self.error


class DummySession:
    def __init__(self, **kwargs):
        self.kwargs: dict[str, Any] = kwargs
        self.proxies: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return DummyResponse()


class DummyResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class FakeXmlProvider:
    def write_channels(self, writer=None):
        writer.write('  <channel id="fake"></channel>\n')

    def write_programs(self, writer=None):
        writer.write('  <programme channel="fake"></programme>\n')


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
        session = DummySession()
        with patch("epg2xml.providers.requests.Session", return_value=session):
            provider = FAKE(dict(CFG))

        response = provider.request("https://example.com")

        self.assertEqual(response, {"ok": True})
        self.assertEqual(session.calls[0]["timeout"], provider.timeout)
        self.assertEqual(session.calls[0]["url"], "https://example.com")

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

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        self.assertFalse(any(issubclass(w.category, DeprecationWarning) for w in caught))

    def test_to_xml_writes_to_given_stream(self):
        handler = self.make_handler(FakeXmlProvider())
        buffer = io.StringIO()

        handler.to_xml(writer=buffer)

        xml = buffer.getvalue()
        self.assertIn('<?xml version="1.0" encoding="UTF-8"?>', xml)
        self.assertIn('<channel id="fake"></channel>', xml)
        self.assertIn('<programme channel="fake"></programme>', xml)
        self.assertTrue(xml.rstrip().endswith("</tv>"))

    def test_program_to_xml_sanitizes_metadata_without_mutating_credits(self):
        program = EPGProgram(
            "kt.id",
            stime=datetime(2026, 1, 1, 9, 0),
            etime=datetime(2026, 1, 1, 10, 0),
            title="  Program Title  ",
            title_sub="  Subtitle  ",
            categories=[" 뉴스 ", "", "  "],
            keywords=[" 키워드 ", None, ""],
            cast=[{"name": " Alice ", "title": "actor", "role": "lead"}],
            crew=[{"name": " Bob ", "title": "director"}],
            rating="15",
        )
        buffer = io.StringIO()

        program.to_xml(CFG, writer=buffer)

        xml = buffer.getvalue()
        self.assertEqual(program.title, "Program Title")
        self.assertEqual(program.title_sub, "Subtitle")
        self.assertEqual(program.categories, ["뉴스"])
        self.assertEqual(program.keywords, ["키워드"])
        self.assertEqual(program.cast, [{"name": "Alice", "title": "actor", "role": "lead"}])
        self.assertEqual(program.crew, [{"name": "Bob", "title": "director"}])
        self.assertEqual(program.rating, 15)
        self.assertIn("<actor role=\"lead\">Alice</actor>", xml)
        self.assertIn("<director>Bob</director>", xml)

    def test_channel_to_xml_sanitizes_text_fields(self):
        channel = EPGChannel(" kt.id ", " KT ", " svc1 ", " Channel A ")
        channel.no = " 101 "
        channel.icon = " https://example.com/icon.png "
        buffer = io.StringIO()

        channel.to_xml(writer=buffer)

        self.assertEqual(channel.id, "kt.id")
        self.assertEqual(channel.src, "KT")
        self.assertEqual(channel.svcid, "svc1")
        self.assertEqual(channel.name, "Channel A")
        self.assertEqual(channel.no, "101")
        self.assertEqual(channel.icon, "https://example.com/icon.png")

    def test_time_to_td_handles_overflow_hours(self):
        parsed = time_to_td("24:30")

        self.assertEqual(parsed, timedelta(hours=24, minutes=30))

    def test_time_to_td_supports_kbs_time_format(self):
        parsed = time_to_td("25000099")

        self.assertEqual(parsed, timedelta(hours=25))

    def test_spotv_deduplicates_boundary_programs_without_mutating_source(self):
        with patch("epg2xml.providers.requests.Session", DummySession):
            provider = SPOTV(dict(CFG))

        day1 = [
            {
                "date": "2026-01-01",
                "channelId": "ch1",
                "startTime": "2026-01-01 23:00",
                "endTime": "2026-01-02 01:00",
                "title": "Late Match",
                "type": 100,
            }
        ]
        day2 = [
            {
                "date": "2026-01-02",
                "channelId": "ch1",
                "startTime": "2026-01-01 23:00",
                "endTime": "2026-01-02 01:00",
                "title": "Late Match",
                "type": 100,
            }
        ]
        provider.req_channels = [EPGChannel("spotv.id", "SPOTV", "ch1", "SPOTV 1")]

        with patch.object(provider, "request", side_effect=[day1, day2]):
            provider.get_programs()

        self.assertEqual(len(provider.req_channels[0].programs), 1)
        self.assertEqual(provider.req_channels[0].programs[0].title, "Late Match")
        self.assertEqual(day1[0]["date"], "2026-01-01")
        self.assertEqual(day2[0]["date"], "2026-01-02")


if __name__ == "__main__":
    unittest.main()
