import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from xml.sax.saxutils import unescape

from epg2xml.providers import EPGProgram, EPGProvider

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())
today = date.today()


class WAVVE(EPGProvider):
    """EPGProvider for WAVVE

    데이터: jsonapi
    요청수: 1
    """

    referer = "https://www.wavve.com/"
    title_regex = r"^(.*?)(?:\s*[\(<]?([\d]+)회[\)>]?)?(?:\([월화수목금토일]?\))?(\([선별전주\(\)재방]*?재[\d방]?\))?\s*(?:\[(.+)\])?$"
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
    no_endtime = False

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
        return self.request(url, params=params)

    def get_svc_channels(self):
        # update parameters for requests
        today_str = today.strftime("%Y-%m-%d")
        hour_min = datetime.now().hour // 3
        params = {
            "enddatetime": f"{today_str} {(hour_min+1)*3:02d}:00",
            "genre": "all",
            "limit": 500,
            "offset": 0,
            "startdatetime": f"{today_str} {hour_min*3:02d}:00",
        }
        self.svc_channel_list = [
            {
                "Name": x["channelname"],
                "Icon_url": self.__url(x["channelimage"]),
                "ServiceId": x["channelid"],
            }
            for x in self.__get("/live/epgs", params=params)["list"]
        ]

    def get_programs(self, lazy_write=False):
        # update parameters for requests
        params = {
            "enddatetime": (today + timedelta(days=int(self.cfg["FETCH_LIMIT"]) - 1)).strftime("%Y-%m-%d") + " 24:00",
            "genre": "all",
            "limit": 500,
            "offset": 0,
            "startdatetime": today.strftime("%Y-%m-%d") + " 00:00",
        }
        channeldict = {x["channelid"]: x for x in self.__get("/live/epgs", params=params)["list"]}

        for idx, _ch in enumerate(self.req_channels):
            # 채널이름은 그대로 들어오고 프로그램 제목은 escape되어 들어옴
            srcChannel = channeldict[_ch.svcid]
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            for program in srcChannel["list"]:
                try:
                    _prog = EPGProgram(_ch.id)
                    _prog.stime = datetime.strptime(program["starttime"], "%Y-%m-%d %H:%M")
                    _prog.etime = datetime.strptime(program["endtime"], "%Y-%m-%d %H:%M")
                    _prog.title = unescape(program["title"])
                    matches = self.title_regex.match(_prog.title)
                    if matches:
                        _prog.title = (matches.group(1) or "").strip()
                        _prog.title_sub = (matches.group(4) or "").strip()
                        episode = (matches.group(2) or "").replace("회", "").strip()
                        _prog.ep_num = "" if episode == "0" else episode
                        _prog.rebroadcast = bool(matches.group(3))
                    _prog.rating = 0 if program["targetage"] == "n" else int(program["targetage"])

                    # 추가 정보 가져오기
                    programid = program["programid"].strip()
                    if self.cfg["GET_MORE_DETAILS"] and programid:
                        # 개별 programid가 없는 경우도 있으니 체크해야함
                        programdetail = self.get_program_details(programid)
                        if programdetail:
                            # programtitle = programdetail['programtitle']
                            # log.info('%s / %s' % (programName, programtitle))
                            _prog.desc = "\n".join(
                                [x.replace("<br>", "\n").strip() for x in programdetail["programsynopsis"].splitlines()]
                            )  # carriage return(\r) 제거, <br> 제거
                            _prog.category = programdetail["genretext"].strip()
                            _prog.poster_url = self.__url(programdetail["programposterimage"].strip())
                            # tags = programdetail['tags']['list'][0]['text']
                            if programdetail["actors"]["list"]:
                                _prog.actors = [x["text"] for x in programdetail["actors"]["list"]]
                    _ch.programs.append(_prog)
                except Exception:
                    log.exception("파싱 에러: %s", program)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)

    @lru_cache()
    def get_program_details(self, programid):
        ret = None
        try:
            contentid = self.__get(f"/vod/programs-contentid/{programid}")["contentid"].strip()
            # log.info("contentid: %s", contentid)

            ret = self.__get(f"/cf/vod/contents/{contentid}")
            # log.info("ret: %s", ret)
        except Exception:
            pass
            # log.exception("Exception while requesting data for %s with %s", url2, param)
        return ret
