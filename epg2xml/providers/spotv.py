import logging
from datetime import datetime, timedelta, date

from epg2xml.providers import EPGProvider, EPGProgram

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
    no_endtime = False

    def get_svc_channels(self):
        url = "https://www.spotvnow.co.kr/api/v2/channel"
        for ch in self.request(url):
            self.svc_channel_list.append(
                {
                    "Name": ch["name"],
                    "ServiceId": ch["id"],
                    "Icon_url": ch["logo"],
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
        data = []
        for nd in range(min(int(self.cfg["FETCH_LIMIT"]), max_ndays)):
            day = date.today() + timedelta(days=nd)
            url = "https://www.spotvnow.co.kr/api/v2/program/" + day.strftime("%Y-%m-%d")
            try:
                data.extend(self.request(url))
            except Exception:
                log.exception("데이터 가져오는 중 에러:")
                continue

        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            programs = [x for x in data if x["channelId"] == _ch.svcid]
            if not programs:
                log.warning("EPG 정보가 없거나 없는 채널입니다: %s", _ch)
                # 오늘 없으면 내일도 없는 채널로 간주
                break
            for p in programs:
                _prog = EPGProgram(_ch.id)
                _prog.title = p["title"]
                _prog.stime = datetime.strptime(p["startTime"], "%Y-%m-%d %H:%M")
                if p["endTime"]:
                    _prog.etime = datetime.strptime(p["endTime"], "%Y-%m-%d %H:%M")
                else:
                    # 끝나는 시간이 없으면 해당일 자정으로 강제
                    _prog.etime = _prog.stime.replace(hour=0, minute=0) + timedelta(days=1)

                matches = self.title_regex.match(_prog.title)
                if matches:
                    _prog.title = (matches.group(2) or "").strip()
                    _prog.title_sub = (matches.group(1) or "").strip()
                    if matches.group(3):
                        _prog.title_sub += " " + matches.group(3).strip()
                    _prog.ep_num = matches.group(4) or ""
                if p["type"] == 300:
                    # 100: live, 200: 본방송
                    _prog.rebroadcast = True
                _ch.programs.append(_prog)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
