import logging
from datetime import date, datetime, timedelta
from typing import List

from epg2xml.providers import EPGProgram, EPGProvider

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class SPOTV(EPGProvider):
    """EPGProvider for SPOTV

    데이터: jsonapi
    요청수: #days
    특이사항:
    - 5일치만 제공
    """

    referer = "https://www.spotvnow.co.kr/channel"
    title_regex = r"\s?(?:\[(.*?)\])?\s?(.*?)\s?(?:\((.*)\))?\s?(?:<([\d,]+)회>)?\s?$"

    def get_svc_channels(self) -> List[dict]:
        url = "https://www.spotvnow.co.kr/api/v3/channel"
        return [
            {
                "Name": ch["name"],
                "ServiceId": ch["id"],
                "Icon_url": ch["logo"],
            }
            for ch in self.request(url)
        ]

    def __dt(self, dt: str) -> datetime:
        if not dt:
            return None
        if dt.endswith("24:00"):
            return datetime.strptime(dt.replace("24:00", "00:00"), "%Y-%m-%d %H:%M") + timedelta(days=1)
        return datetime.strptime(dt, "%Y-%m-%d %H:%M")

    def get_programs(self) -> None:
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
        data = []
        for nd in range(min(int(self.cfg["FETCH_LIMIT"]), max_ndays)):
            day = date.today() + timedelta(days=nd)
            url = "https://www.spotvnow.co.kr/api/v3/program/" + day.strftime("%Y-%m-%d")
            try:
                data.extend(self.request(url))
            except Exception:
                log.exception("데이터 가져오는 중 에러:")
                continue

        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            try:
                _epgs = self.__epgs_of_channel(_ch.id, data, _ch.svcid)
            except AssertionError as e:
                log.warning("%s: %s", e, _ch)
            except Exception:
                log.exception("프로그램 파싱 중 예외: %s", _ch)
            else:
                _ch.programs.extend(_epgs)

    def __epgs_of_channel(self, channelid: str, data: dict, svcid: str) -> List[EPGProgram]:
        programs = [x for x in data if x["channelId"] == svcid]
        assert programs, "EPG 정보가 없거나 없는 채널입니다"

        _epgs = []
        for p in programs:
            _epg = EPGProgram(channelid)
            _epg.title = p["title"]
            _epg.stime = self.__dt(p["startTime"])
            # 끝나는 시간이 없으면 해당일 자정으로 강제
            _epg.etime = self.__dt(p["endTime"]) or (_epg.stime.replace(hour=0, minute=0) + timedelta(days=1))
            if _epg.stime == _epg.etime:
                continue

            matches = self.title_regex.match(_epg.title)
            if matches:
                _epg.title = (matches.group(2) or "").strip()
                _epg.title_sub = (matches.group(1) or "").strip()
                if matches.group(3):
                    _epg.title_sub += " " + matches.group(3).strip()
                _epg.ep_num = matches.group(4) or ""
            if p["type"] == 300:
                # 100: live, 200: 본방송
                _epg.rebroadcast = True
            _epgs.append(_epg)
        return _epgs
