from datetime import date, datetime, timedelta
from typing import List
from xml.sax.saxutils import unescape

from epg2xml.providers import EPGProgram, EPGProvider

today = date.today()


class WAVVE(EPGProvider):
    """EPGProvider for WAVVE

    데이터: jsonapi
    요청수: 1
    특이사항:
    - 해외나 VPS는 차단 가능성이 높음
    """

    referer = "https://www.wavve.com/"
    title_regex = r"^(.*?)(?:\s*[\(<]?([\d]+)회[\)>]?)?(?:\([월화수목금토일]?\))?(\([선별전주\(\)재방]*?재[\d방]?\))?\s*(?:\[(.+)\])?$"
    tps = 3.0

    base_url = "https://apis.wavve.com"
    base_params = {
        "apikey": "E5F3E0D30947AA5440556471321BB6D9",
        "client_version": "6.0.1",
        "device": "pc",
        "drm": "wm",
        "partner": "pooq",
        "pooqzone": "none",
        "region": "kor",
        "targetage": "all",
    }

    def __init__(self, cfg):
        super().__init__(cfg)
        self.sess.headers.update({"wavve-credential": "none"})

    def __url(self, url: str) -> str:
        """completes partial urls from api response or for api request"""
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("/"):
            return self.base_url + url
        return "https://" + url

    def __params(self, **params) -> dict:
        """returns url parameters for api requests with base ones"""
        p = self.base_params.copy()
        p.update(params)
        return p

    def __get(self, url: str, **kwargs):
        url = self.__url(url)
        params = self.__params(**kwargs.pop("params", {}))
        return self.request(url, params=params, **kwargs)

    def get_svc_channels(self) -> List[dict]:
        today_str = today.strftime("%Y-%m-%d")
        hour_min = datetime.now().hour // 3
        # 현재 시간과 가까운 미래에 서비스 가능한 채널만 가져옴
        params = {
            "enddatetime": f"{today_str} {(hour_min+1)*3:02d}:00",
            "genre": "all",
            "limit": 500,
            "offset": 0,
            "startdatetime": f"{today_str} {hour_min*3:02d}:00",
        }
        return [
            {
                "Name": x["channelname"],
                "Icon_url": self.__url(x["channelimage"]),
                "ServiceId": x["channelid"],
            }
            for x in self.__get("/live/epgs", params=params)["list"]
        ]

    def __epg_of_program(self, channelid: str, data: dict) -> EPGProgram:
        _epg = EPGProgram(channelid)
        _epg.stime = datetime.strptime(data["starttime"], "%Y-%m-%d %H:%M")
        _epg.etime = datetime.strptime(data["endtime"], "%Y-%m-%d %H:%M")
        # 채널이름은 그대로 들어오고 프로그램 제목은 escape되어 들어옴
        _epg.title = unescape(data["title"])
        if m := self.title_regex.match(_epg.title):
            _epg.title = m.group(1)
            _epg.title_sub = m.group(4)
            episode = (m.group(2) or "").replace("회", "").strip()
            _epg.ep_num = None if episode == "0" else episode
            _epg.rebroadcast = bool(m.group(3))
        _epg.rating = 0 if data["targetage"] == "n" else int(data["targetage"])
        return _epg

    def get_programs(self) -> None:
        # parameters for requests
        channel_map = {}
        params = {"genre": "all", "limit": 500, "offset": 0}
        for nd in range(int(self.cfg["FETCH_LIMIT"])):
            day = (today + timedelta(days=nd)).strftime("%Y-%m-%d")
            for t in range(8):
                if nd == 0 and (t + 1) * 3 < datetime.now().hour:
                    continue
                params.update({"startdatetime": f"{day} {t*3:02d}:00", "enddatetime": f"{day} {t*3+3:02d}:00"})
                for ch in self.__get("/live/epgs", params=params)["list"]:
                    cid = ch["channelid"]
                    channel_map.setdefault(cid, [])
                    toappend = ch.get("list") or []
                    # 3시간 단위로 요청된 스케줄 앞 뒤로 중복이 있을 수 있다.
                    if channel_map[cid] and toappend and channel_map[cid][-1] == toappend[0]:
                        toappend = toappend[1:]
                    channel_map[cid] += toappend

        for idx, _ch in enumerate(self.req_channels):
            self.log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            programs = channel_map.get(_ch.svcid)
            if not programs:
                self.log.warning("EPG 정보가 없거나 응답에서 누락된 채널입니다: %s", _ch)
                continue
            for program in programs:
                try:
                    _epg = self.__epg_of_program(_ch.id, program)
                except (AttributeError, KeyError, TypeError, ValueError):
                    self.log.exception("프로그램 파싱 중 예외: %s", _ch)
                else:
                    _ch.programs.append(_epg)
