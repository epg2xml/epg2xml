import logging
from xml.sax.saxutils import unescape
from datetime import datetime, timedelta, date

from epg2xml.providers import EPGProvider, EPGProgram
from epg2xml.utils import ua, request_data

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())
today = date.today()


class WAVVE(EPGProvider):
    """EPGProvider for WAVVE

    데이터: jsonapi
    요청수: 1
    """

    referer = "https://www.wavve.com/schedule/index.html"
    title_regex = r"^(.*?)(?:\s*[\(<]?([\d]+)회[\)>]?)?(?:\([월화수목금토일]?\))?(\([선별전주\(\)재방]*?재[\d방]?\))?\s*(?:\[(.+)\])?$"
    url = "https://apis.wavve.com/live/epgs"
    params = {
        "enddatetime": "2020-01-20 24:00",
        "genre": "all",
        "limit": 200,
        "offset": 0,
        "startdatetime": "2020-01-20 21:00",
        "apikey": "E5F3E0D30947AA5440556471321BB6D9",
        "credential": "none",
        "device": "pc",
        "drm": "wm",
        "partner": "pooq",
        "pooqzone": "none",
        "region": "kor",
        "targetage": "auto",
    }
    no_endtime = False

    def get_svc_channels(self):
        # update parameters for requests
        today_str = today.strftime("%Y-%m-%d")
        hour_min = datetime.now().hour // 3
        self.params.update(
            {
                "startdatetime": f"{today_str} {hour_min*3:02d}:00",
                "enddatetime": f"{today_str} {(hour_min+1)*3:02d}:00",
            }
        )
        self.svc_channel_list = [
            {
                "Name": x["channelname"],
                "Icon_url": "https://" + x["channelimage"],
                "ServiceId": x["channelid"],
            }
            for x in self.request(self.url, params=self.params)["list"]
        ]

    def get_programs(self, lazy_write=False):
        # for caching program details
        programcache = {}
        # update parameters for requests
        self.params.update(
            {
                "startdatetime": today.strftime("%Y-%m-%d") + " 00:00",
                "enddatetime": (today + timedelta(days=int(self.cfg["FETCH_LIMIT"]) - 1)).strftime("%Y-%m-%d")
                + " 24:00",
            }
        )
        channeldict = {x["channelid"]: x for x in self.request(self.url, params=self.params)["list"]}

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
                    if self.cfg["GET_MORE_DETAILS"]:
                        programid = program["programid"].strip()
                        if programid and (programid not in programcache):
                            # 개별 programid가 없는 경우도 있으니 체크해야함
                            programdetail = self.get_program_details(programid)
                            if programdetail is not None:
                                programdetail["hit"] = 0  # to know cache hit rate
                            programcache[programid] = programdetail

                        if (programid in programcache) and bool(programcache[programid]):
                            programcache[programid]["hit"] += 1
                            programdetail = programcache[programid]
                            # programtitle = programdetail['programtitle']
                            # log.info('%s / %s' % (programName, programtitle))
                            _prog.desc = "\n".join(
                                [x.replace("<br>", "\n").strip() for x in programdetail["programsynopsis"].splitlines()]
                            )  # carriage return(\r) 제거, <br> 제거
                            _prog.category = programdetail["genretext"].strip()
                            _prog.poster_url = "https://" + programdetail["programposterimage"].strip()
                            # tags = programdetail['tags']['list'][0]['text']
                            if programdetail["actors"]["list"]:
                                _prog.actors = [x["text"] for x in programdetail["actors"]["list"]]
                    _ch.programs.append(_prog)
                except Exception:
                    log.exception("파싱 에러: %s", program)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)

    def get_program_details(self, programid):
        url = "https://apis.wavve.com/vod/programs-contentid/" + programid
        referer = "https://www.wavve.com/player/vod?programid=" + programid
        param = {
            "apikey": "E5F3E0D30947AA5440556471321BB6D9",
            "credential": "none",
            "device": "pc",
            "drm": "wm",
            "partner": "pooq",
            "pooqzone": "none",
            "region": "kor",
            "targetage": "auto",
        }
        self.sess.headers.update({"User-Agent": ua, "Referer": referer})

        ret = None
        try:
            contentid = request_data(url, params=param)["contentid"].strip()

            url2 = 'https://apis.wavve.com/cf/vod/contents/' + contentid # 둘 사이를 왔다갔다 하는 모양이네요 지금은 여기만 됩니다.
            # url2 = "https://apis.wavve.com/vod/contents/" + contentid  
            ret = self.request(url2, params=param)
        except Exception:
            log.exception("Exception while requesting data for %s with %s", url2, param)
        return ret
