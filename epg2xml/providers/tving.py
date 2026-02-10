import logging
from datetime import date, datetime, timedelta
from itertools import islice
from typing import List

try:
    from curl_cffi import requests
except ImportError:
    import requests

from epg2xml.providers import EPGProgram, EPGProvider

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())
today = date.today()

PRIORITY_IMG_CODE = ["CAIC2300", "CAIC1600", "CAIC0100", "CAIC0400"]
G_CODE = {
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


class TVING(EPGProvider):
    """EPGProvider for TVING

    데이터: jsonapi
    요청수: #channels/20 * #days * 24/3
    특이사항:
    - 최대 20채널 최대 3시간 허용
    """

    referer = "https://www.tving.com/schedule/main.do"
    tps = 3.0

    url = "https://api.tving.com/v2/media/schedules"
    base_params = {
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

    def __params(self, **params) -> dict:
        """returns url parameters for api requests with base ones"""
        p = self.base_params.copy()
        p.update(params)
        return p

    def __get(self, url: str, **kwargs) -> List[dict]:
        params = self.__params(**kwargs.pop("params", {}))
        _page = 1
        _results = []
        while True:
            params["pageNo"] = str(_page)
            _data = self.request(url=url, params=params, **kwargs)
            if _data["header"]["status"] != 200:
                raise requests.exceptions.RequestException
            _results.extend(_data["body"]["result"])
            if _data["body"]["has_more"] == "Y":
                _page += 1
            else:
                break
        return _results

    def get_svc_channels(self) -> List[dict]:
        def get_imgurl(_item: dict):
            for _code in PRIORITY_IMG_CODE:
                try:
                    img_list = [x for x in _item["image"] if x["code"] == _code]
                    if not img_list:
                        continue
                    return "https://image.tving.com" + (img_list[0].get("url") or img_list[0]["url2"])
                except Exception:
                    pass
            return None

        params = {
            "broadDate": today.strftime("%Y%m%d"),
            "broadcastDate": today.strftime("%Y%m%d"),
            "startBroadTime": datetime.now().strftime("%H0000"),
            "endBroadTime": (datetime.now() + timedelta(hours=3)).strftime("%H0000"),
        }
        return [
            {
                "Name": x["channel_name"]["ko"],
                "Icon_url": get_imgurl(x),
                "ServiceId": x["channel_code"],
                "Category": x["schedules"][0]["channel"]["category_name"]["ko"],
            }
            for x in self.__get(self.url, params=params)
            if x["schedules"] is not None
        ]

    def get_programs(self) -> None:
        def grouper(iterable, n):
            it = iter(iterable)
            group = tuple(islice(it, n))
            while group:
                yield group
                group = tuple(islice(it, n))

        for gid, chgroup in enumerate(grouper(self.req_channels, 20)):
            schdict = {}
            params = {"channelCode": ",".join([x.svcid.strip() for x in chgroup])}
            for nd in range(int(self.cfg["FETCH_LIMIT"])):
                day = today + timedelta(days=nd)
                params.update({"broadDate": day.strftime("%Y%m%d"), "broadcastDate": day.strftime("%Y%m%d")})
                for t in range(8):
                    if nd == 0 and (t + 1) * 3 < datetime.now().hour:
                        continue
                    params.update({"startBroadTime": f"{t*3:02d}0000", "endBroadTime": f"{t*3+3:02d}0000"})
                    for ch in self.__get(self.url, params=params):
                        chcode = ch["channel_code"]
                        schdict.setdefault(chcode, [])
                        toappend = ch.get("schedules") or []
                        try:
                            # 3시간 단위로 요청된 스케줄 앞 뒤로 중복이 있을 수 있다.
                            if schdict[chcode][-1] == toappend[0]:
                                toappend = toappend[1:]
                        except Exception:
                            pass
                        schdict[chcode] += toappend

            for idx, _ch in enumerate(chgroup):
                log.info("%03d/%03d %s", gid * 20 + idx + 1, len(self.req_channels), _ch)
                try:
                    _epgs = self.__epgs_of_channel(_ch.id, schdict[_ch.svcid])
                except Exception:
                    log.exception("프로그램 파싱 중 예외: %s", _ch)
                else:
                    _ch.programs.extend(_epgs)

    def __epgs_of_channel(self, channelid: str, schedules: List[dict]) -> List[EPGProgram]:
        _epgs = []
        for sch in schedules:
            _epg = EPGProgram(channelid)
            # 공통
            _epg.stime = datetime.strptime(str(sch["broadcast_start_time"]), "%Y%m%d%H%M%S")
            _epg.etime = datetime.strptime(str(sch["broadcast_end_time"]), "%Y%m%d%H%M%S")
            _epg.rebroadcast = sch["rerun_yn"] == "Y"

            get_from = "movie" if sch["movie"] else "program"
            img_code = "CAIM2100" if sch["movie"] else "CAIP0900"

            _epg.rating = G_CODE[sch[get_from].get("grade_code", "CPTG0100")]
            _epg.title = sch[get_from]["name"]["ko"]
            _epg.title_sub = sch[get_from]["name"].get("en")
            if cate1 := sch[get_from]["category1_name"]:
                _epg.categories = [cate1.get("ko")]
            if cate2 := sch[get_from]["category2_name"]:
                _epg.categories = (_epg.categories or []) + [cate2.get("ko")]
            if actors := sch[get_from]["actor"]:
                _epg.cast = [{"name": x, "title": "actor"} for x in actors]
            if directors := sch[get_from]["director"]:
                _epg.crew = [{"name": x, "title": "director"} for x in directors]

            poster = [x["url"] for x in sch[get_from]["image"] if x["code"] == img_code]
            if poster:
                _epg.poster_url = "https://image.tving.com" + poster[0]
                # _prog.poster_url += '/dims/resize/236'

            _epg.desc = sch[get_from]["story" if sch["movie"] else "synopsis"]["ko"]
            if episode := sch["episode"]:
                frequency = episode["frequency"]
                _epg.ep_num = "" if frequency == 0 else str(frequency)
                _epg.desc = (episode["synopsis"] or {}).get("ko")
            _epgs.append(_epg)
        return _epgs
