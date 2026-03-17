import logging
import re
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple

from epg2xml.providers import EPGProgram, EPGProvider

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class MBC(EPGProvider):
    """EPGProvider for MBC

    데이터: jsonapi
    요청수: #channels * #days
    특이사항:
    - 채널별 API endpoint(TV/Radio/MBCPlus)가 다르다.
    """

    referer = "https://schedule.imbc.com/"
    channel_url = "https://control.imbc.com/Schedule/ONAIRWITHNVOD"
    schedule_base_url = "https://control.imbc.com/Schedule"
    PTN_DIGITS = re.compile(r"\d+")
    REQUEST_SPEC = {
        "MBC": ("TV", "ALL", "tv"),
        "FM": ("Radio", "FM", "radio"),
        "FM4U": ("Radio", "FM4U", "radio"),
        "ALLTHAT": ("Radio", "ALLTHAT", "radio"),
        "MBCNET": ("MBCPlus", "MBCNET", "mbcplus"),
    }

    def get_svc_channels(self) -> List[dict]:
        data = self.request(self.channel_url)
        if not isinstance(data, list):
            raise ValueError(f"Unexpected channel payload type: {type(data).__name__}")

        svc_channels = []
        for ch in data:
            svc_channels.append(
                {
                    "Name": ch["TypeTitle"],
                    "ServiceId": ch["ScheduleCode"],
                    "Category": ch["Type"],
                }
            )
        return svc_channels

    def __get_parser(self, key: str) -> Callable[[str, dict, str], EPGProgram]:
        if key == "tv":
            return self.__epg_of_tv
        if key == "radio":
            return self.__epg_of_radio
        if key == "mbcplus":
            return self.__epg_of_mbcplus
        raise ValueError(f"Unsupported parser key: {key}")

    def __request_spec(self, schedule_code: str, day: date) -> Tuple[str, dict, Callable[[str, dict, str], EPGProgram]]:
        code = str(schedule_code)
        if code in self.REQUEST_SPEC:
            path, stype, parser_key = self.REQUEST_SPEC[code]
        elif code.startswith("P_"):
            path, stype, parser_key = "MBCPlus", code, "mbcplus"
        else:
            raise ValueError(f"Unsupported schedule code: {schedule_code}")
        endpoint = f"{self.schedule_base_url}/{path}"
        params = {"sDate": day.strftime("%Y%m%d"), "sType": stype}
        return endpoint, params, self.__get_parser(parser_key)

    def __epg_of_day(self, channelid: str, schedule_code: str, day: date) -> List[EPGProgram]:
        endpoint, params, parser = self.__request_spec(schedule_code, day)
        data = self.request(endpoint, params=params)
        if not isinstance(data, list):
            raise ValueError(
                f"Unexpected schedule payload type: {type(data).__name__} " f"(endpoint={endpoint}, params={params})"
            )

        _epgs = []
        for item in data:
            _epg = parser(channelid, item, params["sDate"])
            if not _epg.stime:
                raise ValueError("Invalid StartTime in schedule item")
            if not _epg.etime:
                raise ValueError("Invalid EndTime in schedule item")
            if _epg.etime <= _epg.stime:
                _epg.etime += timedelta(days=1)
            _epgs.append(_epg)
        return _epgs

    def get_programs(self) -> None:
        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            for nd in range(int(self.cfg["FETCH_LIMIT"])):
                day = date.today() + timedelta(days=nd)
                try:
                    _epgs = self.__epg_of_day(_ch.id, _ch.svcid, day)
                except Exception:
                    log.exception("프로그램 파싱 중 예외: %s, %s", _ch, day)
                else:
                    _ch.programs.extend(_epgs)

    def __epg_of_tv(self, channelid: str, item: dict, _sdate: str) -> EPGProgram:
        _epg = self.__base_epg(channelid, item, "Title")
        _epg.rating = self.__parse_rating(str(item.get("AgeRange") or "").strip())
        _epg.stime = self.__parse_dt(item["ScheduleDay"], item["StartTime"])
        _epg.etime = self.__parse_dt(item["ScheduleDay"], item["EndTime"])
        return _epg

    def __epg_of_radio(self, channelid: str, item: dict, _sdate: str) -> EPGProgram:
        _epg = self.__base_epg(channelid, item, "Title")
        _epg.stime = self.__parse_dt(item["BroadDate"], item["StartTime"])
        _epg.etime = self.__parse_dt(item["BroadDate"], item["EndTime"])
        return _epg

    def __epg_of_mbcplus(self, channelid: str, item: dict, sdate: str) -> EPGProgram:
        _epg = self.__base_epg(channelid, item, "ProgramTitle")
        _epg.rating = self.__parse_rating(str(item.get("TargetAge") or "").strip())
        _epg.stime = self.__parse_dt(sdate, item["StartTime"])
        _epg.etime = self.__parse_dt(sdate, item["EndTime"])
        return _epg

    def __base_epg(self, channelid: str, item: dict, title_key: str) -> EPGProgram:
        _epg = EPGProgram(channelid)
        _epg.title = str(item[title_key]).strip()
        if not _epg.title:
            raise ValueError("Empty title in schedule item")
        title_sub = str(item.get("SubTitle") or "").strip() or None
        if title_sub == _epg.title:
            title_sub = None
        _epg.title_sub = title_sub
        _epg.poster_url = str(item.get("Photo") or "").strip() or None
        return _epg

    def __parse_dt(self, schedule_day: str, hhmm: str) -> Optional[datetime]:
        if schedule_day is None or hhmm is None:
            return None
        day_text = str(schedule_day).strip()
        time_text = str(hhmm).strip()
        if not day_text or not time_text:
            return None

        if not time_text.isdigit() or len(time_text) != 4:
            return None
        hour = int(time_text[:2])
        minute = int(time_text[2:])

        if minute > 59:
            return None

        try:
            base = datetime.strptime(day_text, "%Y%m%d")
        except Exception:
            try:
                base = datetime.strptime(day_text, "%Y-%m-%d")
            except Exception:
                return None
        return base + timedelta(hours=hour, minutes=minute)

    def __parse_rating(self, value: Optional[str]) -> int:
        if not value:
            return 0
        if m := self.PTN_DIGITS.search(str(value)):
            return int(m.group(0))
        return 0
