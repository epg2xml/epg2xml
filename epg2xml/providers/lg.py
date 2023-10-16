import logging
from datetime import date, datetime, timedelta
from typing import List

from epg2xml.providers import EPGProgram, EPGProvider

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class LG(EPGProvider):
    """EPGProvider for LG

    데이터: jsonapi
    요청수: #channels * #days
    특이사항:
    - 5일치만 제공
    - 프로그램 시작 시각만 제공
    참고:
    - 사이트 리뉴얼 이후 프로그램 카테고리가 아직 명확히 정해지지 않은 듯 하다.
    """

    referer = "https://www.lguplus.com/iptv/channel-guide"
    title_regex = r"\s?(?:\[.*?\])?(.*?)(?:\[(.*)\])?\s?(?:\(([\d,]+)회\))?\s?(<재>)?$"
    no_endtime = True

    gcode = {"0": 0, "1": 7, "2": 12, "3": 15, "4": 19}
    pcate = {
        "00": "영화",
        "02": "만화",
        "03": "드라마",
        "05": "스포츠",
        "06": "교육",
        "07": None,  # 어린이/교육
        "08": "연예/오락",
        "09": "공연/음악",
        "10": None,  # 게임
        "11": "다큐",
        "12": "뉴스/정보",
        "13": "라이프",
        "15": None,  # 홈쇼핑
        "16": None,  # 경제/부동산
        "31": "기타",
    }

    def get_svc_channels(self):
        url = "https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/tv-schedule-list"
        data = self.request(url)
        cate = {x["urcBrdCntrTvChnlGnreCd"]: x["urcBrdCntrTvChnlGnreNm"] for x in data["brdGnreDtoList"]}
        for ch in self.request(url)["brdCntrTvChnlIDtoList"]:
            self.svc_channel_list.append(
                {
                    "Name": ch["urcBrdCntrTvChnlDscr"],
                    "No": ch["urcBrdCntrTvChnlNo"],
                    "ServiceId": ch["urcBrdCntrTvChnlId"],
                    "Category": cate[ch["urcBrdCntrTvChnlGnreCd"]],
                }
            )

    def get_programs(self, lazy_write=False):
        max_ndays = 5
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
        url = "https://www.lguplus.com/uhdc/fo/prdv/chnlgid/v1/tv-schedule-list"
        params = {"urcBrdCntrTvChnlId": "SVCID", "brdCntrTvChnlBrdDt": "EPGDATE"}
        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            for nd in range(min(int(self.cfg["FETCH_LIMIT"]), max_ndays)):
                day = date.today() + timedelta(days=nd)
                params.update({"urcBrdCntrTvChnlId": _ch.svcid, "brdCntrTvChnlBrdDt": day.strftime("%Y%m%d")})
                data = self.request(url, params=params) or {}
                data = data.get("brdCntTvSchIDtoList", [])
                if not data:
                    log.warning("EPG 정보가 없거나 없는 채널입니다: %s %s", _ch, day)
                    break  # 오늘 없으면 내일도 없는 채널로 간주
                try:
                    _epgs = self.__epgs_of_day(_ch.id, data)
                except Exception:
                    log.exception("프로그램 파싱 중 예외: %s, %s", _ch, day)
                else:
                    _ch.programs.extend(_epgs)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)

    def __epgs_of_day(self, channelid: str, data: list) -> List[EPGProgram]:
        _epgs = []
        for p in data:
            _epg = EPGProgram(channelid)
            _epg.title = p["brdPgmTitNm"]
            _epg.desc = p["brdPgmDscr"]
            _epg.stime = datetime.strptime(p["brdCntrTvChnlBrdDt"] + p["epgStrtTme"], "%Y%m%d%H:%M:%S")
            _epg.rating = self.gcode.get(p["brdWtchAgeGrdCd"], 0)
            _epg.extras.append(p["brdPgmRsolNm"])  # 화질
            if p["subtBrdYn"] == "Y":
                _epg.extras.append("자막")
            if p["explBrdYn"] == "Y":
                _epg.extras.append("화면해설")
            if p["silaBrdYn"] == "Y":
                _epg.extras.append("수화")
            matches = self.title_regex.match(_epg.title)
            if matches:
                _epg.title = (matches.group(1) or "").strip()
                _epg.title_sub = (matches.group(2) or "").strip()
                _epg.ep_num = matches.group(3) or ""
                _epg.rebroadcast = bool(matches.group(4))
            _epg.categories = [self.pcate[p["urcBrdCntrTvSchdGnreCd"]]]
            _epgs.append(_epg)
        return _epgs
