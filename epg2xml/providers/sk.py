import logging
from datetime import datetime, timedelta, date
from xml.sax.saxutils import unescape

from epg2xml.providers import EPGProvider, EPGProgram

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class SK(EPGProvider):
    """EPGProvider for SK

    데이터: jsonapi
    요청수: #channels * #days
    특이사항:
    - 2일치만 제공
    """

    referer = None
    title_regex = r"^(.*?)(\(([\d,]+)회\))?(<(.*)>)?(\((재)\))?$"
    no_endtime = False

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
        max_ndays = 2
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
        url = "https://cyber.skbroadband.com/core-prod/product/btv-channel/day-frmt-list"
        params = {"idSvc": "SVCID", "stdDt": "EPGDATE", "gubun": "day"}
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

        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            for nd in range(min(int(self.cfg["FETCH_LIMIT"]), max_ndays)):
                day = date.today() + timedelta(days=nd)
                params.update({"idSvc": _ch.svcid, "stdDt": day.strftime("%Y%m%d")})
                try:
                    for res in self.request(url, params=params).get("result", []):
                        _prog = EPGProgram(_ch.id)
                        _prog.title = res["nmTitle"]
                        matches = self.title_regex.match(_prog.title)
                        if matches:
                            _prog.title = matches.group(1) or ""
                            _prog.title_sub = matches.group(5) or ""
                            _prog.rebroadcast = bool(matches.group(7))
                            _prog.ep_num = matches.group(3) or ""
                        _prog.rating = int(res.get("cdRating", "0"))
                        _prog.stime = datetime.strptime(res["dtEventStart"], "%Y%m%d%H%M%S")
                        _prog.etime = datetime.strptime(res["dtEventEnd"], "%Y%m%d%H%M%S")
                        if res["cdGenre"] and (res["cdGenre"] in genre_code):
                            _prog.category = genre_code[res["cdGenre"]]
                        _prog.desc = res["nmSynop"]  # 값이 없음
                        _ch.programs.append(_prog)
                except Exception:
                    log.exception("파싱 에러: %s", _ch)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
