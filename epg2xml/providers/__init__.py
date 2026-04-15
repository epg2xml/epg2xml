import json
import logging
import re
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import InitVar, asdict, dataclass, fields
from datetime import datetime, timedelta
from functools import wraps
from importlib import import_module
from itertools import chain
from os import PathLike
from typing import Any, ClassVar, Iterable, Iterator, List, Literal, Optional, TextIO, Tuple, Union

try:
    from curl_cffi import requests
except ImportError:
    import requests

from epg2xml import __title__, __version__
from epg2xml.id_format import render_id_format
from epg2xml.utils import Element, PrefixLogger, RateLimiter, dump_json, norm_text

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


def norm_text_list(values: List[str]) -> Optional[List[str]]:
    if not values:
        return None
    normalized = []
    seen = set()
    for text in (norm_text(value) for value in values):
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized or None


class DuplicateChannelIdError(ValueError):
    """Raised when requested channels resolve to duplicate XML channel IDs."""


@dataclass
class Credit:
    name: str
    title: str
    role: str = None

    def sanitize(self) -> None:
        for field_name in ("name", "title", "role"):
            setattr(self, field_name, norm_text(getattr(self, field_name)))

    def validate(self) -> None:
        # Assumes sanitize() has already normalized field values.
        if not self.name:
            raise ValueError("Credit.name is required")
        if not self.title:
            raise ValueError("Credit.title is required")
        if self.title not in TAG_CREDITS:
            raise ValueError(f"Unsupported credit title: {self.title}")


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
    cast: List[Credit] = None  # 출연진
    crew: List[Credit] = None  # 제작진
    extras: List[str] = None
    keywords: List[str] = None

    def __str__(self) -> str:
        title = self.title or "<untitled>"
        stime = self.stime.isoformat() if isinstance(self.stime, datetime) else repr(self.stime)
        return f"{title} <{self.channelid}> @ {stime}"

    def _add_text_item(self, field_name: str, value: str) -> None:
        value = norm_text(value)
        if not value:
            return
        items = getattr(self, field_name) or []
        if value not in items:
            items.append(value)
            setattr(self, field_name, items)

    def add_category(self, value: str) -> None:
        self._add_text_item("categories", value)

    def add_keyword(self, value: str) -> None:
        self._add_text_item("keywords", value)

    def add_extra(self, value: str) -> None:
        self._add_text_item("extras", value)

    def _add_credit(self, field_name: str, name: str, title: str, role: str = None) -> None:
        credit = Credit(name=name, title=title, role=role)
        credit.sanitize()
        if not credit.name or not credit.title:
            return
        items = getattr(self, field_name) or []
        if credit not in items:
            items.append(credit)
            setattr(self, field_name, items)

    def add_cast(self, values: Iterable[str]) -> None:
        for value in values:
            self._add_credit("cast", value, "actor")

    def add_crew(self, values: Iterable[str], title: str) -> None:
        for value in values:
            self._add_credit("crew", value, title)

    @classmethod
    def _norm_credits(cls, values: List[Credit]) -> Optional[List[Credit]]:
        if not values:
            return None

        normalized = []
        seen = set()
        for value in values:
            if not isinstance(value, Credit):
                continue
            credit = value
            credit.sanitize()
            if not credit.name or not credit.title:
                continue
            credit_key = (credit.name, credit.title, credit.role)
            if credit_key in seen:
                continue
            seen.add(credit_key)
            normalized.append(credit)
        return normalized or None

    def sanitize(self) -> None:
        for field_name in ("title", "title_sub", "part_num", "ep_num", "desc", "poster_url"):
            setattr(self, field_name, norm_text(getattr(self, field_name)))
        for field_name in ("categories", "extras", "keywords"):
            setattr(self, field_name, norm_text_list(getattr(self, field_name)))
        for field_name in ("cast", "crew"):
            setattr(self, field_name, self._norm_credits(getattr(self, field_name)))
        if self.title and self.title_sub == self.title:
            self.title_sub = None
        try:
            self.rating = max(0, int(self.rating or 0))
        except (TypeError, ValueError):
            self.rating = 0

    def validate(self) -> None:
        # Assumes sanitize() has already normalized field values.
        if not self.channelid:
            raise ValueError(f"EPGProgram.channelid is required: {self}")
        if not isinstance(self.stime, datetime):
            raise TypeError(f"EPGProgram.stime must be a datetime: {self}")
        if not self.title:
            log.warning("EPGProgram.title is missing: %s", self)
        if not isinstance(self.rebroadcast, bool):
            raise TypeError(f"EPGProgram.rebroadcast must be a bool: {self}")
        if not isinstance(self.rating, int):
            raise TypeError(f"EPGProgram.rating must be an int: {self}")
        if self.etime is not None:
            if not isinstance(self.etime, datetime):
                raise TypeError(f"EPGProgram.etime must be a datetime when present: {self}")
            if self.etime < self.stime:
                raise ValueError(f"EPGProgram.etime must not be earlier than stime: {self}")
        for credit in (self.cast or []) + (self.crew or []):
            credit.validate()

    def to_xml(self, cfg: dict, writer: TextIO = None) -> None:
        self.sanitize()
        self.validate()
        if self.etime is None:
            raise ValueError("EPGProgram.etime is required for XML serialization")
        writer = writer or sys.stdout

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
        if title and (matches := PTN_TITLE.match(title)):
            title = matches.group(1).strip()
            title_sub = " ".join(filter(bool, [matches.group(2), title_sub]))
            title_sub = title_sub or None
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
                f"출연 : {','.join(x.name for x in cast)}" if cast else "",
                f"제작 : {','.join(x.name for x in crew)}" if crew else "",
                f"등급 : {rating}",
                self.desc,
            ]
            desc = PTN_SPACES.sub(" ", "\n".join(filter(bool, desc)))
            _p.append(Element("desc", desc, lang="ko"))

        # credits
        if cast or crew:
            _c = Element("credits")
            for cc in sorted(cast + crew, key=lambda x: TAG_CREDITS.index(x.title)):
                attrs = {k: v for k, v in asdict(cc).items() if k not in {"title", "name"} and v is not None}
                _c.append(Element(cc.title, cc.name, **attrs))
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
            # 한국 TV 프로그램 시청등급은 영화·비디오물 쪽 제도인 KMRB 표기로 적기 어렵다.
            # KCSC(방심위)도 검토했지만, 실제 등급 표시는 방송사/플랫폼의 자체 분류에 가깝고
            # 방심위는 기준 설정·조정 역할이어서 특정 기관명을 system 값으로 단정하기 애매하다.
            # 그래서 여기서는 가장 중립적인 국가 단위 표기인 KR을 사용한다.
            _r = Element("rating", system="KR")
            _r.append(Element("value", rating))
            _p.append(_r)

        # dumps
        writer.write(_p.tostring(level=1))
        writer.write("\n")


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

    def sanitize(self) -> None:
        for field_name in ("id", "src", "svcid", "name", "icon", "no", "category"):
            setattr(self, field_name, norm_text(getattr(self, field_name)))

    def validate(self) -> None:
        # Assumes sanitize() has already normalized field values.
        if not self.id:
            raise ValueError("EPGChannel.id is required")
        if not self.src:
            raise ValueError("EPGChannel.src is required")
        if not self.svcid:
            raise ValueError("EPGChannel.svcid is required")
        if not self.name:
            raise ValueError("EPGChannel.name is required")
        for program in self.programs:
            if not isinstance(program, EPGProgram):
                raise TypeError(f"EPGChannel.programs for {self} must contain only EPGProgram instances")
            if program.channelid != self.id:
                raise ValueError(f"EPGChannel.programs for {self} must match channel id: {program.channelid}")
            if not isinstance(program.stime, datetime):
                raise TypeError(f"EPGChannel.programs for {self} must have datetime stime values: {program.stime!r}")

    def set_etime(self) -> None:
        """Completes missing program endtimes based on the successive relationship between programs."""
        previous_stime = None
        for ind, prog in enumerate(self.programs):
            if previous_stime is not None and prog.stime < previous_stime:
                raise ValueError(
                    f"EPGChannel.programs must be ordered by stime for {self}: "
                    f"{previous_stime.isoformat()} > {prog.stime.isoformat()}"
                )
            previous_stime = prog.stime
            if prog.etime:
                continue
            try:
                prog.etime = self.programs[ind + 1].stime
            except IndexError:
                prog.etime = (prog.stime + timedelta(days=1)).replace(hour=0, minute=0, second=0)

    def to_xml(self, writer: TextIO = None) -> None:
        writer = writer or sys.stdout
        self.sanitize()
        self.validate()
        chel = Element("channel", id=self.id)
        # TODO: Find a better strategy for display-name values.
        chel.append(Element("display-name", self.name))
        chel.append(Element("display-name", self.src))
        if self.no:
            chel.append(Element("display-name", f"{self.no}"))
            chel.append(Element("display-name", f"{self.no} {self.name}"))
            chel.append(Element("display-name", f"{self.no} {self.src}"))
        if self.icon:
            chel.append(Element("icon", src=self.icon))
        writer.write(chel.tostring(level=1))
        writer.write("\n")


# user-agent - curl -L microlink.io/user-agents.json | jq -r .user[0]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"


class EPGProvider:
    """Base class for EPG Providers"""

    referer: str = None
    title_regex: Union[str, re.Pattern] = None
    tps: float = 1.0
    timeout: float = 10.0
    retry_attempts: int = 3
    retry_backoff: float = 0.5
    was_channel_updated: bool = False

    def __init__(self, cfg: dict):
        self.provider_name = self.__class__.__name__
        self.log = PrefixLogger(log, f"[{self.provider_name:5s}]")
        self.cfg = cfg
        # session
        if "cffi" in requests.__name__:
            self.sess = requests.Session(headers={"Referer": self.referer}, impersonate="chrome")
        else:
            self.sess = requests.Session()
            self.sess.headers.update({"Referer": self.referer, "User-Agent": UA})
        if http_proxy := cfg["HTTP_PROXY"]:
            self.sess.proxies.update({"http": http_proxy, "https": http_proxy})
        if self.title_regex:
            self.title_regex = re.compile(self.title_regex)
        self.request = RateLimiter(tps=self.tps)(self.__request)
        # Runtime state placeholders.
        self.svc_channels: List[dict] = []
        self.req_channels: List[EPGChannel] = []

    def __request(self, url: str, method: str = "GET", **kwargs) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        request_desc = f"{method.upper()} {url}"
        if params := kwargs.get("params"):
            request_desc += f" params={params}"

        for attempt in range(1, self.retry_attempts + 1):
            try:
                r = self.sess.request(method=method, url=url, **kwargs)
                r.raise_for_status()
                try:
                    return r.json()
                except (json.decoder.JSONDecodeError, ValueError):
                    return r.text
            except requests.exceptions.RequestException as e:
                if attempt >= self.retry_attempts:
                    self.log.error("Request failed: %s (%s)", request_desc, e)
                    return ""
                self.log.warning(
                    "Request failed, retrying %d/%d: %s (%s)",
                    attempt,
                    self.retry_attempts - 1,
                    request_desc,
                    e,
                )
                time.sleep(self.retry_backoff * attempt)

        return ""

    def load_svc_channels(self, channeljson: dict = None) -> None:
        # Check whether the cache needs to be refreshed.
        try:
            channelinfo = channeljson[self.provider_name.upper()]
            total = channelinfo["TOTAL"]
            channels = channelinfo["CHANNELS"]
            if total != len(channels):
                raise ValueError("TOTAL != len(CHANNELS)")
            updated_at = datetime.fromisoformat(channelinfo["UPDATED"])
            if (datetime.now() - updated_at).total_seconds() <= 3600 * 24 * 4:
                self.svc_channels = channels
                self.log.info("%03d service channels loaded from cache", len(channels))
                return
            self.log.debug("Refreshing service channels because the cache is stale...")
        except (KeyError, TypeError, ValueError) as e:
            self.log.debug("Refreshing service channels because the cache is invalid: %s", e)

        try:
            channels = self.get_svc_channels()
        except (AttributeError, KeyError, TypeError, ValueError):
            self.log.exception("Error while retrieving service channels:")
        else:
            self.svc_channels = channels
            self.was_channel_updated = True
            self.log.info("Fetched %03d service channels from the server", len(channels))

    def get_svc_channels(self) -> List[dict]:
        raise NotImplementedError("The 'get_svc_channels' method must be implemented")

    def load_req_channels(self) -> None:
        """Load requested channels from MY_CHANNELS into req_channels."""
        my_channels = self.cfg["MY_CHANNELS"]
        if my_channels == "*":
            self.log.debug("Replacing MY_CHANNELS with all service channels...")
            my_channels = self.svc_channels
        if not my_channels:
            return
        req_channels = []
        svc_channels = {x["ServiceId"]: x for x in self.svc_channels}
        for my_no, my_ch in enumerate(my_channels):
            if "ServiceId" not in my_ch:
                self.log.warning("'ServiceId' not found: %s", my_ch)
                continue
            req_ch = svc_channels.pop(my_ch["ServiceId"], None)
            if req_ch is None:
                self.log.warning("'ServiceId' not found in service channels: %s", my_ch)
                continue
            for _k, _v in my_ch.items():
                if _v:
                    req_ch[_k] = _v
            req_ch["Source"] = self.provider_name
            req_ch.setdefault("No", str(my_no))
            if "Id" not in req_ch:
                try:
                    req_ch["Id"] = render_id_format(self.cfg["ID_FORMAT"], req_ch)
                except (KeyError, TypeError, ValueError, SyntaxError) as e:
                    self.log.warning("Invalid ID_FORMAT '%s': %s", self.cfg["ID_FORMAT"], e)
                    req_ch["Id"] = f'{req_ch["ServiceId"]}.{req_ch["Source"].lower()}'
            if not self.cfg["ADD_CHANNEL_ICON"]:
                req_ch.pop("Icon_url", None)
            req_channels.append(EPGChannel.fromdict(**req_ch))
        self.log.info(
            "Requested %3d - unavailable %3d = final %3d",
            len(my_channels),
            len(my_channels) - len(req_channels),
            len(req_channels),
        )
        self.req_channels = req_channels

    def write_channels(self, writer: TextIO = None) -> None:
        for ch in self.req_channels:
            if not ch.programs:
                log.warning("Skipping '%s' because no program entries were found", ch.id)
                continue
            ch.to_xml(writer=writer)

    def get_programs(self) -> None:
        raise NotImplementedError("The 'get_programs' method must be implemented")

    def write_programs(self, writer: TextIO = None) -> None:
        for ch in self.req_channels:
            for prog in ch.programs:
                prog.to_xml(self.cfg, writer=writer)
            ch.programs.clear()  # for memory efficiency


def no_endtime(func):
    @wraps(func)
    def wrapped(self: EPGProvider, *args, **kwargs):
        func(self, *args, **kwargs)
        for ch in self.req_channels:
            ch.set_etime()

    return wrapped


class EPGHandler:
    """Coordinate multiple EPG providers."""

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
                log.error("Unknown provider: '%s'", name)
                raise ImportError(f"Unknown provider: '{name}'") from None
        return providers

    def load_channels(self, channelfile: str, parallel: bool = False) -> None:
        try:
            log.debug("Trying to load cached channels from JSON")
            with open(channelfile, "r", encoding="utf-8") as fp:
                channeljson = json.load(fp)
        except (json.decoder.JSONDecodeError, ValueError, FileNotFoundError) as e:
            log.debug("Failed to load cached channels from JSON: %s", e)
            channeljson = {}
        if parallel:
            with ThreadPoolExecutor() as exe:
                futures = {exe.submit(p.load_svc_channels, channeljson=channeljson): p for p in self.providers}
                for future in as_completed(futures):
                    future.result()
        else:
            for p in self.providers:
                p.load_svc_channels(channeljson=channeljson)
        updated_providers = [p for p in self.providers if p.was_channel_updated]
        if updated_providers:
            for p in updated_providers:
                channeljson[p.provider_name.upper()] = {
                    "UPDATED": datetime.now().isoformat(),
                    "TOTAL": len(p.svc_channels),
                    "CHANNELS": p.svc_channels,
                }
            dump_json(channelfile, channeljson)
            log.info("The channel file was upgraded. You can review it here: %s", channelfile)

    def load_req_channels(self):
        for p in self.providers:
            p.load_req_channels()

        log.debug("Checking uniqueness of channelid...")
        cids = [c.id for p in self.providers for c in p.req_channels]
        if len(cids) != len(set(cids)):
            raise DuplicateChannelIdError(f"Duplicate channel IDs: { {k:v for k,v in Counter(cids).items() if v > 1} }")

    def get_programs(self, parallel: bool = False):
        if parallel:
            with ThreadPoolExecutor() as exe:
                futures = {exe.submit(p.get_programs): p for p in self.providers}
                for future in as_completed(futures):
                    future.result()
        else:
            for p in self.providers:
                p.get_programs()

    def to_xml(self, writer: TextIO = None):
        writer = writer or sys.stdout
        writer.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        writer.write('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n\n')
        writer.write(f'<tv generator-info-name="{__title__} v{__version__}">\n')

        log.debug("Writing channels...")
        for p in self.providers:
            p.write_channels(writer=writer)

        log.debug("Writing programs...")
        for p in self.providers:
            p.write_programs(writer=writer)

        writer.write("</tv>\n")

    @property
    def all_channels(self) -> Iterator:
        """Return an iterator over all channels across providers."""
        return chain.from_iterable(p.req_channels for p in self.providers)

    @property
    def all_programs(self) -> Iterator:
        """Return an iterator over all programs across providers."""
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
sqlite3.register_adapter(datetime, lambda v: v.isoformat())
sqlite3.register_converter("TIMESTAMP", lambda v: datetime.fromisoformat(v.decode()))
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
                f"""CREATE TABLE IF NOT EXISTS epgchannel (
                {', '.join(EPGChannel.columns)},
                PRIMARY KEY (Id)
                );
                CREATE TABLE IF NOT EXISTS epgprogram ({', '.join(cols)});
                CREATE INDEX IF NOT EXISTS idx_epgchannel_source ON epgchannel (Source);
                CREATE INDEX IF NOT EXISTS idx_epgprogram_channelid_stime ON epgprogram (channelid, stime);
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
        sql = "SELECT * FROM epgchannel WHERE Source = ? ORDER BY No, Name, Id"
        return [EPGChannel(*x) for x in self.__fetchall(sql, (source,))]

    def select_programs(self, channelid: str) -> List[EPGProgram]:
        sql = "SELECT * FROM epgprogram WHERE channelid = ? ORDER BY stime, etime, title"
        return [EPGProgram(*x) for x in self.__fetchall(sql, (channelid,))]
