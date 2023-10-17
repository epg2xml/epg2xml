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
    특이사항:
    - 해외나 VPS는 차단 가능성이 높음
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
        return self.request(url, params=params, **kwargs)

    def get_svc_channels(self):
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
        self.svc_channel_list = [
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
        matches = self.title_regex.match(_epg.title)
        if matches:
            _epg.title = (matches.group(1) or "").strip()
            _epg.title_sub = (matches.group(4) or "").strip()
            episode = (matches.group(2) or "").replace("회", "").strip()
            _epg.ep_num = "" if episode == "0" else episode
            _epg.rebroadcast = bool(matches.group(3))
        _epg.rating = 0 if data["targetage"] == "n" else int(data["targetage"])

        # 추가 정보 가져오기
        if not self.cfg["GET_MORE_DETAILS"]:
            return _epg
        programid = data["programid"].strip()
        if not programid:
            # 개별 programid가 없는 경우도 있으니 체크해야함
            return _epg
        detail = self.get_program_details(programid)
        if not detail:
            return _epg
        # 여러가지 추가 정보가 제공되지만
        # 방송되지 않은 미래의 프로그램/에피소드 정보는 반영되지 않았기에
        # 일부 정보만 유효함을 유념
        synopsis = detail["seasonsynopsis"] or detail["programsynopsis"] or detail["episodesynopsis"]
        _epg.desc = "\n".join(
            [x.replace("<br>", "\n").strip() for x in synopsis.splitlines()]
        )  # carriage return(\r) 제거, <br> 제거
        _epg.categories = [detail["genretext"].strip()]
        _epg.poster_url = self.__url(detail["seasonposterimage"].strip())
        _epg.keywords = [x["text"] for x in detail["tags"]["list"]]
        actors = detail.get("season_actors") or detail.get("actors") or {"list": []}
        directors = detail.get("season_directors") or detail.get("directors") or {"list": []}
        writers = detail.get("season_writers") or detail.get("writers") or {"list": []}
        _epg.cast = [{"name": x["text"], "title": "actor"} for x in actors["list"]]
        _epg.crew = [{"name": x["text"], "title": "director"} for x in directors["list"]]
        _epg.crew += [{"name": x["text"], "title": "writer"} for x in writers["list"]]
        return _epg

    def get_programs(self, lazy_write=False):
        # parameters for requests
        params = {
            "enddatetime": (today + timedelta(days=int(self.cfg["FETCH_LIMIT"]) - 1)).strftime("%Y-%m-%d 24:00"),
            "genre": "all",
            "limit": 500,
            "offset": 0,
            "startdatetime": today.strftime("%Y-%m-%d 00:00"),
        }
        channeldict = {x["channelid"]: x for x in self.__get("/live/epgs", params=params)["list"]}

        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            for program in channeldict[_ch.svcid]["list"]:
                try:
                    _epg = self.__epg_of_program(_ch.id, program)
                except Exception:
                    log.exception("프로그램 파싱 중 예외: %s", _ch)
                else:
                    _ch.programs.append(_epg)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)

    @lru_cache
    def get_program_details(self, programid: str):
        try:
            params = {"history": "season", "programid": programid}
            data = self.__get("/fz/vod/programs/landing", params=params)
            if data.get("resultcode") in ["550"]:
                # 애초에 유효하지 않은 programid가 있을 수 있음
                # { "resultcode": "550", "resultmessage": "해당 데이터가 없습니다." }
                return None
            return self.__get(f"/fz/vod/contents-detail/{data['content_id'].strip()}")
        except Exception:
            log.exception("프로그램 상세 정보 요청 중 예외: %s", programid)
            return None
