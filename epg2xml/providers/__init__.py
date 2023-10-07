import re
import sys
import logging
from copy import copy
from datetime import datetime, timedelta
from importlib import import_module
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import ClassVar, List

from requests import Session
from bs4 import BeautifulSoup, FeatureNotFound, SoupStrainer

from epg2xml.utils import ua, request_data, dump_json, PrefixLogger, Element

log = logging.getLogger("PROV")


def load_providers(cfgs):
    providers = []
    for name, cfg in cfgs.items():
        if cfg["ENABLED"]:
            try:
                m = import_module(f"epg2xml.providers.{name.lower()}")
                providers.append(getattr(m, name.upper())(cfg))
            except ModuleNotFoundError:
                log.error("No such provider found: '%s'", name)
                sys.exit(1)
    return providers


def load_channels(providers, conf, channeljson=None):
    if conf.settings["parallel"]:
        with ThreadPoolExecutor() as exe:
            for p in providers:
                exe.submit(p.load_svc_channels, channeljson=channeljson)
    else:
        for p in providers:
            p.load_svc_channels(channeljson=channeljson)
    if any(p.need_channel_update for p in providers):
        for p in providers:
            channeljson[p.provider_name.upper()] = {
                "UPDATED": datetime.now().isoformat(),
                "TOTAL": len(p.svc_channel_list),
                "CHANNELS": p.svc_channel_list,
            }
        dump_json(conf.settings["channelfile"], channeljson)
        log.info("Channel file was upgraded. You may check the changes here: %s", conf.settings["channelfile"])


class ParserBeautifulSoup(BeautifulSoup):
    """A ``bs4.BeautifulSoup`` that picks the first available parser."""

    def insert_before(self, *args):
        pass

    def insert_after(self, *args):
        pass

    def __init__(self, markup, **kwargs):
        # pick the first parser available
        for parser in ["lxml", "html.parser"]:
            try:
                super().__init__(markup, parser, **kwargs)
                return
            except FeatureNotFound:
                pass

        raise FeatureNotFound


class EPGProvider:
    """Base class for EPG Providers"""

    referer = None
    title_regex = ""
    no_endtime = False
    need_channel_update = True

    def __init__(self, cfg):
        self.provider_name = self.__class__.__name__
        self.cfg = cfg
        self.sess = Session()
        self.sess.headers.update({"User-Agent": ua, "Referer": self.referer})
        if self.title_regex:
            self.title_regex = re.compile(self.title_regex)
        # placeholders
        self.svc_channel_list: list = []
        self.svc_channel_dict: dict = {}
        self.req_channels: list = []

    def request(self, url, method="GET", **kwargs):
        return request_data(url=url, method=method, session=self.sess, **kwargs)

    def load_svc_channels(self, channeljson=None):
        plog = PrefixLogger(log, f"[{self.provider_name:5s}]")

        # check if update required
        try:
            channelinfo = channeljson[self.provider_name.upper()]
            total = channelinfo["TOTAL"]
            channels = channelinfo["CHANNELS"]
            assert total == len(channels), "TOTAL != len(CHANNELS)"
            updated = channelinfo["UPDATED"]
            datetime_up = datetime.fromisoformat(updated)
            if (datetime.now() - datetime_up).total_seconds() <= 3600 * 24 * 4:
                self.svc_channel_list = channels
                self.need_channel_update = False
            else:
                plog.debug("Updating service channels as outdated ...")
        except Exception:
            plog.debug("Updating service channels as cache broken")

        if self.need_channel_update:
            try:
                self.svc_channel_list.clear()
                self.get_svc_channels()
                plog.info("%03d service channels successfully fetched from server.", len(self.svc_channel_list))
            except Exception:
                plog.exception("Exception while retrieving service channels:")
                sys.exit(1)
        else:
            plog.info("%03d service channels loaded from cache.", len(self.svc_channel_list))
        self.svc_channel_dict = {x["ServiceId"]: x for x in self.svc_channel_list}

    def get_svc_channels(self):
        pass

    def load_my_channels(self):
        """from MY_CHANNELS to req_channels"""
        plog = PrefixLogger(log, f"[{self.provider_name:5s}]")
        my_channels = self.cfg["MY_CHANNELS"]
        if my_channels == "*":
            plog.debug("Overriding all MY_CHANNELS by service channels ...")
            my_channels = self.svc_channel_list
        if not my_channels:
            return
        req_channels = []
        for my_no, my_ch in enumerate(my_channels):
            if "ServiceId" not in my_ch:
                plog.warning("'ServiceId' Not Found: %s", my_ch)
                continue
            if my_ch["ServiceId"] not in self.svc_channel_dict:
                plog.warning("'ServiceId' Not in Service: %s", my_ch)
                continue
            req_ch = copy(self.svc_channel_dict[my_ch["ServiceId"]])
            for _k, _v in my_ch.items():
                if _v:
                    req_ch[_k] = _v
            req_ch["Source"] = self.provider_name
            if "No" not in req_ch:
                req_ch["No"] = str(my_no)
            if "Id" not in req_ch:
                try:
                    req_ch["Id"] = eval(f"f'{self.cfg['ID_FORMAT']}'", None, req_ch)
                except Exception:
                    req_ch["Id"] = f'{req_ch["ServiceId"]}.{req_ch["Source"].lower()}'
            if not self.cfg["ADD_CHANNEL_ICON"]:
                req_ch.pop("Icon_url", None)
            req_channels.append(EPGChannel(req_ch))
        plog.info("요청 %d - 불가 %d = 최종 %d", len(my_channels), len(my_channels) - len(req_channels), len(req_channels))
        self.req_channels = req_channels

    def write_channel_headers(self):
        for ch in self.req_channels:
            chel = Element("channel", id=ch.id)
            # TODO: something better for display-name?
            chel.append(Element("display-name", ch.name))
            chel.append(Element("display-name", ch.src))
            if ch.no:
                chel.append(Element("display-name", f"{ch.no}"))
                chel.append(Element("display-name", f"{ch.no} {ch.name}"))
                chel.append(Element("display-name", f"{ch.no} {ch.src}"))
            if ch.icon:
                chel.append(Element("icon", src=ch.icon))
            print(chel.tostring(level=1))

    def get_programs(self, lazy_write=False):
        pass

    def write_programs(self):
        for ch in self.req_channels:
            ch.to_xml(self.cfg, no_endtime=self.no_endtime)


class EPGChannel:
    """For individual channel entities"""

    __slots__ = ["id", "src", "svcid", "name", "icon", "no", "programs"]

    def __init__(self, channelinfo):
        self.id = channelinfo["Id"]
        self.src = channelinfo["Source"]
        self.svcid = channelinfo["ServiceId"]
        self.name = channelinfo["Name"]
        self.icon = channelinfo.get("Icon_url", None)
        self.no = channelinfo.get("No", None)
        # placeholder
        self.programs: list = []
        """
        개별 EPGProgram이 소속 channelid를 가지고 있어서 굳이 EPGChannel의 하위 리스트로 관리해야할
        이유는 없지만, endtime이 없는 EPG 항목을 위해 한 번에 써야할 필요가 있는 Provider가 있기에
        (kt, lg, skb, naver, daum) 채널 단위로 관리하는 편이 유리하다.
        """

    def __str__(self):
        return f"{self.name} <{self.id}>"

    def to_xml(self, conf, no_endtime=False):
        if no_endtime:
            for ind, x in enumerate(self.programs):
                if not self.programs[ind].etime:
                    try:
                        self.programs[ind].etime = self.programs[ind + 1].stime
                    except IndexError:
                        self.programs[ind].etime = self.programs[ind].stime + timedelta(days=1)
                        self.programs[ind].etime.replace(hour=0, minute=0, second=0)
        for x in self.programs:
            x.to_xml(conf)
        # for memory efficiency
        self.programs.clear()


@dataclass
class EPGProgram:
    """For individual program entities"""

    channelid: str
    stime: datetime = None
    etime: datetime = None
    title: str = None
    title_sub: str = None
    part_num: str = None
    ep_num: str = None
    categories: List[str] = field(default_factory=list)
    rebroadcast: bool = False
    rating: int = 0
    # not usually given by default
    desc: str = None
    poster_url: str = None
    actors: List[str] = field(default_factory=list)
    staff: List[str] = field(default_factory=list)
    extras: List[str] = field(default_factory=list)

    PTN_TITLE: ClassVar[re.Pattern] = re.compile(r"(.*) \(?(\d+부)\)?")
    PTN_SPACES: ClassVar[re.Pattern] = re.compile(" +")
    CAT_KO2EN: ClassVar[dict] = {
        "교양": "Arts / Culture (without music)",
        "만화": "Cartoons / Puppets",
        "교육": "Education / Science / Factual topics",
        "취미": "Leisure hobbies",
        "드라마": "Movie / Drama",
        "영화": "Movie / Drama",
        "음악": "Music / Ballet / Dance",
        "뉴스": "News / Current affairs",
        "다큐": "Documentary",
        "라이프": "Documentary",
        "시사/다큐": "Documentary",
        "연예": "Show / Game show",
        "스포츠": "Sports",
        "홈쇼핑": "Advertisement / Shopping",
    }

    def to_xml(self, cfg):
        stime = self.stime.strftime("%Y%m%d%H%M%S +0900")
        etime = self.etime.strftime("%Y%m%d%H%M%S +0900")
        title = (self.title or "").strip()
        title_sub = (self.title_sub or "").strip()
        actors = ",".join(self.actors)
        staff = ",".join(self.staff)
        cats_ko = [x.strip() for x in self.categories if x]
        cats_ko = [x for x in cats_ko if x]  # 결과가 empty string일 수 있으니 제거
        episode = self.ep_num or ""
        rating = "전체 관람가" if self.rating == 0 else f"{self.rating}세 이상 관람가"
        rebroadcast = self.rebroadcast
        desc = self.desc

        matches = self.PTN_TITLE.match(title)
        if matches:
            title = matches.group(1).strip()
            title_sub = (matches.group(2) + " " + title_sub).strip()
        if not title:
            title = title_sub
        if not title:
            title = "제목 없음"
        if episode and cfg["ADD_EPNUM_TO_TITLE"]:
            title += f" ({str(episode)}회)"
        if rebroadcast and cfg["ADD_REBROADCAST_TO_TITLE"]:
            title += " (재)"

        _p = Element("programme", start=stime, stop=etime, channel=self.channelid)
        _p.append(Element("title", title, lang="ko"))
        if title_sub:
            _p.append(Element("sub-title", title_sub, lang="ko"))
        if cfg["ADD_DESCRIPTION"]:
            desclines = [title]
            if title_sub:
                desclines += [f"부제 : {title_sub}"]
            if rebroadcast and cfg["ADD_REBROADCAST_TO_TITLE"]:
                desclines += ["방송 : 재방송"]
            if episode:
                desclines += [f"회차 : {str(episode)}회"]
            if cats_ko:
                desclines += [f"장르 : {','.join(cats_ko)}"]
            if actors:
                desclines += [f"출연 : {actors.strip()}"]
            if staff:
                desclines += [f"제작 : {staff.strip()}"]
            desclines += [f"등급 : {rating}"]
            if desc:
                desclines += [desc]
            desc = self.PTN_SPACES.sub(" ", "\n".join(desclines))
            _p.append(Element("desc", desc, lang="ko"))
            if actors or staff:
                _c = Element("credits")
                for actor in map(str.strip, self.actors):
                    if actor:
                        _c.append(Element("actor", actor))
                for staff in map(str.strip, self.staff):
                    if staff:
                        _c.append(Element("producer", staff))
                _p.append(_c)

        for cat_ko in cats_ko:
            _p.append(Element("category", cat_ko, lang="ko"))
            cat_en = self.CAT_KO2EN.get(cat_ko)
            if cat_en:
                _p.append(Element("category", cat_en, lang="en"))
        if self.poster_url:
            _p.append(Element("icon", src=self.poster_url))
        if episode:
            if cfg["ADD_XMLTV_NS"]:
                try:
                    episode_ns = int(episode) - 1
                except ValueError:
                    episode_ns = int(episode.split(",", 1)[0]) - 1
                episode_ns = f"0.{str(episode_ns)}.0/0"
                _p.append(Element("episode-num", episode_ns, system="xmltv_ns"))
            else:
                _p.append(Element("episode-num", episode, system="onscreen"))
        if rebroadcast:
            _p.append(Element("previously-shown"))
        if rating:
            _r = Element("rating", system="KMRB")
            _r.append(Element("value", rating))
            _p.append(_r)
        print(_p.tostring(level=1))
