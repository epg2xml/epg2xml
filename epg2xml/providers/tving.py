import logging
from itertools import islice
from datetime import datetime, timedelta, date

import requests

from epg2xml.providers import EPGProvider, EPGProgram
from epg2xml.utils import request_data

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())
today = date.today()


class TVING(EPGProvider):
    """EPGProvider for TVING

    데이터: jsonapi
    요청수: #channels/20 * #days * 24/3
    특이사항:
    - 최대 20채널 최대 3시간 허용
    """

    url = "https://api.tving.com/v2/media/schedules"
    referer = "https://www.tving.com/schedule/main.do"
    params = {
        "pageNo": "1",
        "pageSize": "20",  # maximum 20
        "order": "chno",
        "scope": "all",
        "adult": "all",
        "free": "all",
        "broadDate": "20200608",
        "broadcastDate": "20200608",
        "startBroadTime": "030000",  # 최대 3시간 간격
        "endBroadTime": "060000",
        # "channelCode": "C06941,C07381,...",
        "screenCode": "CSSD0100",
        "networkCode": "CSND0900",
        "osCode": "CSOD0900",
        "teleCode": "CSCD0900",
        "apiKey": "1e7952d0917d6aab1f0293a063697610",
    }
    gcode = {
        "CPTG0100": 0,
        "CPTG0200": 7,
        "CPTG0300": 12,
        "CPTG0400": 15,
        "CPTG0500": 19,
        "CMMG0100": 0,
        "CMMG0200": 12,
        "CMMG0300": 15,
        "CMMG0400": 19,
    }
    no_endtime = False

    def request(self, url, method="GET", **kwargs):
        kwargs.setdefault("params", {})
        _page = 1
        _results = []
        while True:
            kwargs["params"]["pageNo"] = str(_page)
            _data = request_data(url=url, method=method, session=self.sess, **kwargs)
            if _data["header"]["status"] != 200:
                raise requests.exceptions.RequestException
            _results.extend(_data["body"]["result"])
            if _data["body"]["has_more"] == "Y":
                _page += 1
            else:
                return _results

    def get_svc_channels(self):
        def get_imgurl(_item):
            priority_img_code = ["CAIC1600", "CAIC0100", "CAIC0400"]
            for _code in priority_img_code:
                try:
                    img_list = [x for x in _item["image"] if x["code"] == _code]
                    if img_list:
                        return "https://image.tving.com" + (
                            img_list[0]["url"] if "url" in img_list[0] else img_list[0]["url2"]
                        )
                except Exception:
                    pass
            return ""

        self.params.update(
            {
                "broadDate": today.strftime("%Y%m%d"),
                "broadcastDate": today.strftime("%Y%m%d"),
                "startBroadTime": datetime.now().strftime("%H") + "0000",
                "endBroadTime": (datetime.now() + timedelta(hours=3)).strftime("%H") + "0000",
            }
        )
        self.svc_channel_list = [
            {
                "Name": x["channel_name"]["ko"],
                "Icon_url": get_imgurl(x),
                "ServiceId": x["channel_code"],
                "Category": x["schedules"][0]["channel"]["category_name"]["ko"],
            }
            for x in self.request(self.url, params=self.params)
            if x["schedules"] is not None
        ]

    def get_programs(self, lazy_write=False):
        def grouper(iterable, n):
            it = iter(iterable)
            group = tuple(islice(it, n))
            while group:
                yield group
                group = tuple(islice(it, n))

        for gid, chgroup in enumerate(grouper(self.req_channels, 20)):
            channeldict = {}
            self.params.update({"channelCode": ",".join([x.svcid.strip() for x in chgroup])})
            for nd in range(int(self.cfg["FETCH_LIMIT"])):
                day = today + timedelta(days=nd)
                self.params.update({"broadDate": day.strftime("%Y%m%d"), "broadcastDate": day.strftime("%Y%m%d")})
                for t in range(8):
                    self.params.update({"startBroadTime": f"{t*3:02d}0000", "endBroadTime": f"{t*3+3:02d}0000"})
                    for ch in self.request(self.url, params=self.params):
                        try:
                            if ch["schedules"]:
                                channeldict[ch["channel_code"]]["schedules"] += ch["schedules"]
                        except KeyError:
                            channeldict[ch["channel_code"]] = ch

            for idx, _ch in enumerate(chgroup):
                log.info("%03d/%03d %s", gid * 20 + idx + 1, len(self.req_channels), _ch)
                for sch in channeldict[_ch.svcid]["schedules"]:
                    _prog = EPGProgram(_ch.id)
                    # 공통
                    _prog.stime = datetime.strptime(str(sch["broadcast_start_time"]), "%Y%m%d%H%M%S")
                    _prog.etime = datetime.strptime(str(sch["broadcast_end_time"]), "%Y%m%d%H%M%S")
                    _prog.rebroadcast = sch["rerun_yn"] == "Y"

                    get_from = "movie" if sch["movie"] else "program"
                    img_code = "CAIM2100" if sch["movie"] else "CAIP0900"

                    _prog.rating = self.gcode[sch[get_from].get("grade_code", "CPTG0100")]
                    _prog.title = sch[get_from]["name"]["ko"]
                    _prog.title_sub = sch[get_from]["name"].get("en", "")
                    _prog.categories = [sch[get_from]["category1_name"].get("ko", "")]
                    try:
                        _prog.categories += [sch[get_from]["category2_name"]["ko"]]
                    except KeyError:
                        pass
                    _prog.actors = sch[get_from]["actor"]
                    _prog.staff = sch[get_from]["director"]

                    poster = [x["url"] for x in sch[get_from]["image"] if x["code"] == img_code]
                    if poster:
                        _prog.poster_url = "https://image.tving.com" + poster[0]
                        # _prog.poster_url += '/dims/resize/236'

                    _prog.desc = sch[get_from]["story" if sch["movie"] else "synopsis"]["ko"]
                    if sch["episode"]:
                        episode = sch["episode"]["frequency"]
                        _prog.ep_num = "" if episode == 0 else str(episode)
                        _prog.desc = sch["episode"]["synopsis"]["ko"]
                    _ch.programs.append(_prog)
                if not lazy_write:
                    _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
