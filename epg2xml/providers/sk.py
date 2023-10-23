import logging
from datetime import date, datetime, timedelta
from typing import List
from xml.sax.saxutils import unescape

from epg2xml.providers import EPGProgram, EPGProvider

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())

GENRE_CODE = {
    "1": "드라마",
    "2": "영화",
    "4": "만화",
    "8": "스포츠",
    "9": "교육",
    "11": "홈쇼핑",
    "13": "예능",
    "14": "시사/다큐",
    "15": "음악",
    "16": "라이프",
    "17": "교양",
    "18": "뉴스",
}


class SK(EPGProvider):
    """EPGProvider for SK

    데이터: jsonapi
    요청수: #channels
    특이사항:
    - 최대 3일치를 한 번에
    """

    referer = "https://cyber.skbroadband.com/"
    title_regex = r"^(.*?)(\(([\d,]+)회\))?(<(.*)>)?(\((재)\))?$"
    no_endtime = False

    def get_svc_channels(self) -> None:
        url = "https://www.skbroadband.com/content/realtime/realtime_list.ajax"
        params = {"package_name": "PM50305785"}
        c_name = ""
        for x in self.request(url, params=params):
            if x["depth"] == "1":
                c_name = x["m_name"]
            elif x["depth"] == "2" and c_name and c_name not in ["프로모션"]:
                self.svc_channels.append(
                    {
                        "Name": unescape(x["m_name"]),
                        "No": str(x["ch_no"]),
                        "ServiceId": x["c_menu"],
                        "Category": c_name,
                    }
                )

    def get_programs(self, lazy_write: bool = False) -> None:
        max_ndays = 3
        if int(self.cfg["FETCH_LIMIT"]) > max_ndays:
            log.warning(
                """

***********************************************************************

%s는 당일포함 %d일치만 EPG를 제공하고 있습니다.

***********************************************************************
        """,
                self.provider_name,
                max_ndays,
            )
        url = "https://cyber.skbroadband.com/core-prod/product/btv-channel/week-frmt-list"
        params = {"idSvc": "SVCID", "stdDt": "EPGDATE", "gubun": "week"}

        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            params.update({"idSvc": _ch.svcid, "stdDt": date.today().strftime("%Y%m%d")})
            try:
                infolist = self.request(url, params=params)["result"]["chnlFrmtInfoList"]
                assert isinstance(infolist, list)
            except Exception:
                log.exception("예상치 못한 응답: %s", params)
                continue
            for nd in range(min(int(self.cfg["FETCH_LIMIT"]), max_ndays)):
                day = date.today() + timedelta(days=nd)
                try:
                    _epgs = self.__epgs_of_day(_ch.id, infolist, day)
                except Exception:
                    log.exception("프로그램 파싱 중 예외: %s, %s", _ch, day)
                else:
                    _ch.programs.extend(_epgs)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)

    def __epgs_of_day(self, channelid: str, data: list, day: datetime) -> List[EPGProgram]:
        _epgs = []
        for info in data:
            if info["eventDt"] != day.strftime("%Y%m%d"):
                continue
            _epg = EPGProgram(channelid)
            _epg.title = info["nmTitle"]
            matches = self.title_regex.match(_epg.title)
            if matches:
                _epg.title = matches.group(1) or ""
                _epg.title_sub = matches.group(5) or ""
                _epg.rebroadcast = bool(matches.group(7))
                _epg.ep_num = matches.group(3) or ""
            _epg.rating = int(info.get("cdRating") or "0")
            _epg.stime = datetime.strptime(info["dtEventStart"], "%Y%m%d%H%M%S")
            _epg.etime = datetime.strptime(info["dtEventEnd"], "%Y%m%d%H%M%S")
            if info["cdGenre"] and (info["cdGenre"] in GENRE_CODE):
                _epg.categories = [GENRE_CODE[info["cdGenre"]]]
            _epg.desc = info["nmSynop"]  # 값이 없음
            _epgs.append(_epg)
        return _epgs
