import logging
from datetime import date, datetime, timedelta
from typing import List

from epg2xml.providers import EPGProgram, EPGProvider
from epg2xml.utils import time_to_td

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class SPOTV(EPGProvider):
    """EPGProvider for SPOTV

    데이터: jsonapi
    요청수: #days
    특이사항:
    - 5일치만 제공
    """

    referer = "https://www.spotvnow.co.kr/channel"
    title_regex = r"\s?(?:\[(.*?)\])?\s?(.*?)\s?(?:[\(<](.*)[\)>])?\s?(?:-(\d+))?\s?(?:<?([\d,]+)회>?)?\s?$"

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
            response = self.request(url)
            if not isinstance(response, list):
                log.warning("예상치 못한 응답: %s", type(response).__name__)
                continue
            data.extend(response)

        # 날짜 경계에서 같은 편성이 중복으로 내려오는 경우를 제거한다.
        _data = []
        seen = set()
        for item in data:
            key = (item.get("channelId"), item.get("startTime"), item.get("endTime"))
            if key in seen:
                continue
            seen.add(key)
            _data.append(item)

        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            try:
                _epgs = self.__epgs_of_channel(_ch.id, _data, _ch.svcid)
            except ValueError as e:
                log.warning("%s: %s", e, _ch)
            except (AttributeError, KeyError, TypeError):
                log.exception("프로그램 파싱 중 예외: %s", _ch)
            else:
                _ch.programs.extend(_epgs)

    def __epgs_of_channel(self, channelid: str, data: dict, svcid: str) -> List[EPGProgram]:
        programs = [x for x in data if x["channelId"] == svcid]
        if not programs:
            raise ValueError("EPG 정보가 없거나 없는 채널입니다")

        _epgs = []
        for p in programs:
            _epg = EPGProgram(channelid)
            _epg.title = p["title"]
            start_day, start_time = p["startTime"].split(" ", maxsplit=1)
            _epg.stime = datetime.strptime(start_day, "%Y-%m-%d") + time_to_td(start_time)
            # 끝나는 시간이 없으면 해당일 자정으로 강제
            end_time = None
            if p["endTime"]:
                end_day, end_time = p["endTime"].split(" ", maxsplit=1)
                end_time = datetime.strptime(end_day, "%Y-%m-%d") + time_to_td(end_time)
            _epg.etime = end_time or (_epg.stime.replace(hour=0, minute=0) + timedelta(days=1))
            if _epg.stime == _epg.etime:
                continue

            if m := self.title_regex.match(_epg.title):
                _epg.title = m.group(2)
                subs = []
                if prefix := m.group(1):
                    subs.append(prefix)
                if sub := m.group(3):
                    subs += [sub.replace(")(", ", ").replace(") (", ", ")]
                title_sub = ", ".join(subs)
                if num := m.group(4):
                    title_sub += f"-{num}"
                if title_sub:
                    _epg.title_sub = title_sub
                _epg.ep_num = m.group(5)
            if p["type"] == 300:
                # 100: live, 200: 본방송
                _epg.rebroadcast = True
            _epgs.append(_epg)
        return _epgs
