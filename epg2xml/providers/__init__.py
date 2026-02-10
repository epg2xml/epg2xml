import json
import logging
import re
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import InitVar, dataclass, fields
from datetime import datetime, timedelta
from functools import wraps
from importlib import import_module
from itertools import chain
from os import PathLike
from typing import ClassVar, Iterator, List, Literal, Tuple, Union

try:
    from curl_cffi import requests
except ImportError:
    import requests

from epg2xml import __title__, __version__
from epg2xml.utils import Element, PrefixLogger, RateLimiter, dump_json

log = logging.getLogger("PROV")


PTN_TITLE = re.compile(r"(.*) \(?(\d+부)\)?")
PTN_SPACES = re.compile(r" {2,}")
CAT_KO2EN = {
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
TAG_CREDITS = (
    "director",
    "actor",
    "writer",
    "adapter",
    "producer",
    "composer",
    "editor",
    "presenter",
    "commentator",
    "guest",
)


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
    categories: List[str] = None
    rebroadcast: bool = False
    rating: int = 0
    # not usually given by default
    desc: str = None
    poster_url: str = None
    cast: List[dict] = None  # 출연진
    crew: List[dict] = None  # 제작진
    extras: List[str] = None
    keywords: List[str] = None

    def sanitize(self) -> None:
        for f in fields(self):
            attr = getattr(self, f.name)
            if f.type == List[str] and attr is not None:
                setattr(self, f.name, [x.strip() for x in filter(bool, attr) if x.strip()])
            elif f.type == str:
                setattr(self, f.name, (attr or "").strip())

    def to_xml(self, cfg: dict) -> None:
        self.sanitize()

        # local variables
        stime = self.stime.strftime("%Y%m%d%H%M%S +0900")
        etime = self.etime.strftime("%Y%m%d%H%M%S +0900")
        title = self.title
        title_sub = self.title_sub
        cast = self.cast or []
        crew = self.crew or []
        categories = self.categories or []
        keywords = self.keywords or []
        episode = self.ep_num
        rebroadcast = "재" if self.rebroadcast else ""
        rating = "전체 관람가" if self.rating == 0 else f"{self.rating}세 이상 관람가"

        # programm
        _p = Element("programme", start=stime, stop=etime, channel=self.channelid)

        # title, sub-title
        if matches := PTN_TITLE.match(title):
            title = matches.group(1).strip()
            title_sub = (matches.group(2) + " " + title_sub).strip()
        title = [
            title or title_sub or "제목 없음",
            f"({episode}회)" if episode and cfg["ADD_EPNUM_TO_TITLE"] else "",
            f"({rebroadcast})" if rebroadcast and cfg["ADD_REBROADCAST_TO_TITLE"] else "",
        ]
        title = PTN_SPACES.sub(" ", " ".join(filter(bool, title)))
        _p.append(Element("title", title, lang="ko"))
        if title_sub:
            _p.append(Element("sub-title", title_sub, lang="ko"))

        # desc
        if cfg["ADD_DESCRIPTION"]:
            desc = [
                title,
                f"부제 : {title_sub}" if title_sub else "",
                f"방송 : {rebroadcast}방송" if rebroadcast else "",
                f"회차 : {episode}회" if episode else "",
                f"장르 : {','.join(categories)}" if categories else "",
                f"출연 : {','.join(x['name'] for x in cast)}" if cast else "",
                f"제작 : {','.join(x['name'] for x in crew)}" if crew else "",
                f"등급 : {rating}",
                self.desc,
            ]
            desc = PTN_SPACES.sub(" ", "\n".join(filter(bool, desc)))
            _p.append(Element("desc", desc, lang="ko"))

        # credits
        if cast or crew:
            _c = Element("credits")
            for cc in sorted(cast + crew, key=lambda x: TAG_CREDITS.index(x["title"])):
                title = cc.pop("title")
                name = cc.pop("name")
                _c.append(Element(title, name, **cc))
            _p.append(_c)

        # categories
        for cat_ko in categories:
            _p.append(Element("category", cat_ko, lang="ko"))
            if cat_en := CAT_KO2EN.get(cat_ko):
                _p.append(Element("category", cat_en, lang="en"))

        # keywords
        for keyword in keywords:
            _p.append(Element("keyword", keyword, lang="ko"))

        # icon
        if self.poster_url:
            _p.append(Element("icon", src=self.poster_url))

        # episode-num
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

        # previously-shown
        if rebroadcast:
            _p.append(Element("previously-shown"))

        # rating
        if rating:
            # TODO: 영상물등급위원회(KMRB)는 TV프로그램 심의에 관여하지 않으므로 수정이 필요
            _r = Element("rating", system="KMRB")
            _r.append(Element("value", rating))
            _p.append(_r)

        # dumps
        print(_p.tostring(level=1))


@dataclass
class EPGChannel:
    """For individual channel entities

    개별 EPGProgram이 소속 channelid를 가지고 있어서 굳이 EPGChannel의 하위 리스트로 관리해야 할
    이유는 없지만, endtime이 없는 프로그램의 처리나 제공자마다 다른 설정을 적용하기 위해서
    채널 단위로 관리하는 편이 유리하다.
    """

    id: str
    src: str
    svcid: str
    name: str
    icon: str = None
    no: str = None
    category: str = None
    programs: InitVar[List[EPGProgram]] = None
    columns: ClassVar[tuple] = ("Id", "Source", "ServiceId", "Name", "Icon_url", "No", "Category")

    def __post_init__(self, programs: List[EPGProgram]) -> None:
        self.programs = programs or []

    @classmethod
    def fromdict(cls, **kwargs):
        this = cls(kwargs["Id"], kwargs["Source"], kwargs["ServiceId"], kwargs["Name"])
        this.icon = kwargs.get("Icon_url")
        this.no = kwargs.get("No")
        this.category = kwargs.get("Category")
        return this

    def __str__(self):
        return f"{self.name} <{self.id}>"

    def set_etime(self) -> None:
        """Completes missing program endtimes based on the successive relationship between programs."""
        for ind, prog in enumerate(self.programs):
            if prog.etime:
                continue
            try:
                prog.etime = self.programs[ind + 1].stime
            except IndexError:
                prog.etime = (prog.stime + timedelta(days=1)).replace(hour=0, minute=0, second=0)

    def to_xml(self) -> None:
        chel = Element("channel", id=self.id)
        # TODO: something better for display-name?
        chel.append(Element("display-name", self.name))
        chel.append(Element("display-name", self.src))
        if self.no:
            chel.append(Element("display-name", f"{self.no}"))
            chel.append(Element("display-name", f"{self.no} {self.name}"))
            chel.append(Element("display-name", f"{self.no} {self.src}"))
        if self.icon:
            chel.append(Element("icon", src=self.icon))
        print(chel.tostring(level=1))


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"


class EPGProvider:
    """Base class for EPG Providers"""

    referer: str = None
    title_regex: Union[str, re.Pattern] = None
    tps: float = 1.0
    was_channel_updated: bool = False

    def __init__(self, cfg: dict):
        self.provider_name = self.__class__.__name__
        self.cfg = cfg
        # session
        sess_kwargs = {"headers": {"Referer": self.referer}}
        if "cffi" in requests.__name__:
            sess_kwargs["impersonate"] = "chrome"
        else:
            sess_kwargs["headers"]["User-Agent"] = UA
        self.sess = requests.Session(**sess_kwargs)
        if http_proxy := cfg["HTTP_PROXY"]:
            self.sess.proxies.update({"http": http_proxy, "https": http_proxy})
        if self.title_regex:
            self.title_regex = re.compile(self.title_regex)
        self.request = RateLimiter(tps=self.tps)(self.__request)
        # placeholders
        self.svc_channels: List[dict] = []
        self.req_channels: List[EPGChannel] = []

    def __request(self, url: str, method: str = "GET", **kwargs) -> str:
        ret = ""
        try:
            r = self.sess.request(method=method, url=url, **kwargs)
            try:
                ret = r.json()
            except (json.decoder.JSONDecodeError, ValueError):
                ret = r.text
        except requests.exceptions.HTTPError as e:
            log.error("요청 중 에러: %s", e)
        except Exception:
            log.exception("요청 중 예외:")
        return ret

    def load_svc_channels(self, channeljson: dict = None) -> None:
        plog = PrefixLogger(log, f"[{self.provider_name:5s}]")

        # check if update required
        try:
            channelinfo = channeljson[self.provider_name.upper()]
            total = channelinfo["TOTAL"]
            channels = channelinfo["CHANNELS"]
            assert total == len(channels), "TOTAL != len(CHANNELS)"
            updated_at = datetime.fromisoformat(channelinfo["UPDATED"])
            if (datetime.now() - updated_at).total_seconds() <= 3600 * 24 * 4:
                self.svc_channels = channels
                plog.info("%03d service channels loaded from cache", len(channels))
                return
            plog.debug("Updating service channels as outdated...")
        except Exception as e:
            plog.debug("Updating service channels as cache broken: %s", e)

        try:
            channels = self.get_svc_channels()
        except Exception:
            plog.exception("Exception while retrieving service channels:")
        else:
            self.svc_channels = channels
            self.was_channel_updated = True
            plog.info("%03d service channels successfully fetched from server", len(channels))

    def get_svc_channels(self) -> List[dict]:
        raise NotImplementedError("method 'get_svc_channels' must be implemented")

    def load_req_channels(self) -> None:
        """from MY_CHANNELS to req_channels"""
        plog = PrefixLogger(log, f"[{self.provider_name:5s}]")
        my_channels = self.cfg["MY_CHANNELS"]
        if my_channels == "*":
            plog.debug("Overriding all MY_CHANNELS by service channels...")
            my_channels = self.svc_channels
        if not my_channels:
            return
        req_channels = []
        svc_channels = {x["ServiceId"]: x for x in self.svc_channels}
        for my_no, my_ch in enumerate(my_channels):
            if "ServiceId" not in my_ch:
                plog.warning("'ServiceId' Not Found: %s", my_ch)
                continue
            req_ch = svc_channels.pop(my_ch["ServiceId"], None)
            if req_ch is None:
                plog.warning("'ServiceId' Not in Service: %s", my_ch)
                continue
            for _k, _v in my_ch.items():
                if _v:
                    req_ch[_k] = _v
            req_ch["Source"] = self.provider_name
            req_ch.setdefault("No", str(my_no))
            if "Id" not in req_ch:
                try:
                    req_ch["Id"] = eval(f"f'{self.cfg['ID_FORMAT']}'", None, req_ch)
                except Exception:
                    req_ch["Id"] = f'{req_ch["ServiceId"]}.{req_ch["Source"].lower()}'
            if not self.cfg["ADD_CHANNEL_ICON"]:
                req_ch.pop("Icon_url", None)
            req_channels.append(EPGChannel.fromdict(**req_ch))
        plog.info(
            "요청 %3d - 불가 %3d = 최종 %3d", len(my_channels), len(my_channels) - len(req_channels), len(req_channels)
        )
        self.req_channels = req_channels

    def write_channels(self) -> None:
        for ch in self.req_channels:
            if not ch.programs:
                log.warning("Skip writing as no program entries found for '%s'", ch.id)
                continue
            ch.to_xml()

    def get_programs(self) -> None:
        raise NotImplementedError("method 'get_programs' must be implemented")

    def write_programs(self) -> None:
        for ch in self.req_channels:
            for prog in ch.programs:
                prog.to_xml(self.cfg)
            ch.programs.clear()  # for memory efficiency


def no_endtime(func):
    @wraps(func)
    def wrapped(self: EPGProvider, *args, **kwargs):
        func(self, *args, **kwargs)
        for ch in self.req_channels:
            ch.set_etime()

    return wrapped


class EPGHandler:
    """for handling EPGProviders"""

    def __init__(self, cfgs: dict):
        self.providers: List[EPGProvider] = self.load_providers(cfgs)

    def load_providers(self, cfgs: dict) -> List[EPGProvider]:
        providers = []
        for name, cfg in cfgs.items():
            if not cfg["ENABLED"]:
                continue
            try:
                m = import_module(f"epg2xml.providers.{name.lower()}")
                providers.append(getattr(m, name.upper())(cfg))
            except ModuleNotFoundError:
                log.error("No such provider found: '%s'", name)
                sys.exit(1)
        return providers

    def load_channels(self, channelfile: str, parallel: bool = False) -> None:
        try:
            log.debug("Trying to load cached channels from json")
            with open(channelfile, "r", encoding="utf-8") as fp:
                channeljson = json.load(fp)
        except (json.decoder.JSONDecodeError, ValueError, FileNotFoundError) as e:
            log.debug("Failed to load cached channels from json: %s", e)
            channeljson = {}
        if parallel:
            with ThreadPoolExecutor() as exe:
                for p in self.providers:
                    exe.submit(p.load_svc_channels, channeljson=channeljson)
        else:
            for p in self.providers:
                p.load_svc_channels(channeljson=channeljson)
        if any(p.was_channel_updated for p in self.providers):
            for p in self.providers:
                channeljson[p.provider_name.upper()] = {
                    "UPDATED": datetime.now().isoformat(),
                    "TOTAL": len(p.svc_channels),
                    "CHANNELS": p.svc_channels,
                }
            dump_json(channelfile, channeljson)
            log.info("Channel file was upgraded. You may check the changes here: %s", channelfile)

    def load_req_channels(self):
        for p in self.providers:
            p.load_req_channels()

        log.debug("Checking uniqueness of channelid...")
        cids = [c.id for p in self.providers for c in p.req_channels]
        assert len(cids) == len(set(cids)), f"채널ID 중복: { {k:v for k,v in Counter(cids).items() if v > 1} }"

    def get_programs(self, parallel: bool = False):
        if parallel:
            with ThreadPoolExecutor() as exe:
                for p in self.providers:
                    exe.submit(p.get_programs)
        else:
            for p in self.providers:
                p.get_programs()

    def to_xml(self):
        print('<?xml version="1.0" encoding="UTF-8"?>')
        print('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
        print(f'<tv generator-info-name="{__title__} v{__version__}">')

        log.debug("Writing channels...")
        for p in self.providers:
            p.write_channels()

        log.debug("Writing programs...")
        for p in self.providers:
            p.write_programs()

        print("</tv>")

    @property
    def all_channels(self) -> Iterator:
        """shortcut to access all channels in providers"""
        return chain.from_iterable(p.req_channels for p in self.providers)

    @property
    def all_programs(self) -> Iterator:
        """shortcut to access all programs in providers"""
        return chain.from_iterable(ch.programs for ch in self.all_channels)

    def to_db(self, dbfile: PathLike) -> None:
        with SQLite(dbfile, "w") as db:
            db.insert_channels(self.all_channels)
            db.insert_programs(self.all_programs)

    def from_db(self, dbfile: PathLike) -> None:
        with SQLite(dbfile, "r") as db:
            for p in self.providers:
                for ch in db.select_channels(p.provider_name):
                    ch.programs = db.select_programs(ch.id)
                    p.req_channels.append(ch)


sqlite3.register_adapter(bool, int)
sqlite3.register_converter("BOOLEAN", lambda v: bool(int(v)))
sqlite3.register_adapter(list, lambda v: json.dumps(v, ensure_ascii=False))
sqlite3.register_converter("JSON", json.loads)

SQLITE_DTYPES = {
    bool: "BOOLEAN",
    datetime: "TIMESTAMP",
    int: "INTEGER",
    List[dict]: "JSON",
    List[str]: "JSON",
}


class SQLite:
    def __init__(self, dbfile: PathLike, mode: Literal["r", "w", "a"] = "r", **kwargs):
        kwargs.setdefault("detect_types", sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        self.conn = sqlite3.connect(dbfile, **kwargs)
        self.mode = mode
        if mode == "w":
            self.__db_init()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and self.mode == "w":
            self.conn.commit()
        self.conn.close()

    def __db_init(self) -> None:
        with closing(self.conn.cursor()) as c:
            cols = [f"{f.name} {SQLITE_DTYPES.get(f.type, 'TEXT')}" for f in fields(EPGProgram)]
            c.executescript(
                f"""CREATE TABLE IF NOT EXISTS epgchannel ({', '.join(EPGChannel.columns)});
                CREATE TABLE IF NOT EXISTS epgprogram ({', '.join(cols)});
                DELETE FROM epgchannel; DELETE FROM epgprogram;"""
            )

    def insert_channels(self, channels: List[EPGChannel]) -> None:
        cols = [f.name for f in fields(EPGChannel)]
        sql = f"INSERT INTO epgchannel VALUES ({','.join('?'*len(cols))})"
        with closing(self.conn.cursor()) as c:
            c.executemany(sql, (tuple(getattr(h, col) for col in cols) for h in channels))

    def insert_programs(self, programs: List[EPGProgram]) -> None:
        cols = [f.name for f in fields(EPGProgram)]
        sql = f"INSERT INTO epgprogram VALUES ({','.join('?'*len(cols))})"
        with closing(self.conn.cursor()) as c:
            c.executemany(sql, (tuple(getattr(p, col) for col in cols) for p in programs))

    def __fetchall(self, *args, **kwargs) -> List[tuple]:
        with closing(self.conn.cursor()) as c:
            return c.execute(*args, **kwargs).fetchall()

    def select_channels(self, source: str) -> List[EPGChannel]:
        sql = "SELECT * FROM epgchannel WHERE Source = ?"
        return [EPGChannel(*x) for x in self.__fetchall(sql, (source,))]

    def select_programs(self, channelid: str) -> List[EPGProgram]:
        sql = "SELECT * FROM epgprogram WHERE channelid = ?"
        return [EPGProgram(*x) for x in self.__fetchall(sql, (channelid,))]
