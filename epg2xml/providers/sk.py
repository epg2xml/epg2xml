import logging
from datetime import datetime, timedelta, date
from xml.sax.saxutils import unescape

from epg2xml.providers import EPGProvider, EPGProgram

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


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
    genre_code = {
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

    def get_svc_channels(self):
        url = "https://www.skbroadband.com/content/realtime/realtime_list.ajax"
        params = {"package_name": "PM50305785"}
        c_name = ""
        for x in self.request(url, params=params):
            if x["depth"] == "1":
                c_name = x["m_name"]
            elif x["depth"] == "2" and c_name and c_name not in ["프로모션"]:
                self.svc_channel_list.append(
                    {
                        "Name": unescape(x["m_name"]),
                        "No": str(x["ch_no"]),
                        "ServiceId": x["c_menu"],
                        "Category": c_name,
                    }
                )

    def get_programs(self, lazy_write=False):
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
                daystr = (date.today() + timedelta(days=nd)).strftime("%Y%m%d")
                for info in filter(lambda x: x["eventDt"] == daystr, infolist):
                    _prog = self._new_program(_ch.id, info)
                    if _prog:
                        _ch.programs.append(_prog)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)

    def _new_program(self, channelid: str, info: dict) -> EPGProgram:
        try:
            _prog = EPGProgram(channelid)
            _prog.title = info["nmTitle"]
            matches = self.title_regex.match(_prog.title)
            if matches:
                _prog.title = matches.group(1) or ""
                _prog.title_sub = matches.group(5) or ""
                _prog.rebroadcast = bool(matches.group(7))
                _prog.ep_num = matches.group(3) or ""
            _prog.rating = int(info.get("cdRating") or "0")
            _prog.stime = datetime.strptime(info["dtEventStart"], "%Y%m%d%H%M%S")
            _prog.etime = datetime.strptime(info["dtEventEnd"], "%Y%m%d%H%M%S")
            if info["cdGenre"] and (info["cdGenre"] in self.genre_code):
                _prog.category = self.genre_code[info["cdGenre"]]
            _prog.desc = info["nmSynop"]  # 값이 없음
            return _prog
        except Exception:
            log.exception("프로그램 파싱 에러: %s", info)
            return None
