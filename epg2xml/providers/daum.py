import logging
from datetime import datetime, timedelta
from typing import List
from urllib.parse import quote

from epg2xml.providers import EPGProgram, EPGProvider
from epg2xml.utils import ParserBeautifulSoup as BeautifulSoup

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())

CH_CATE = ["지상파", "종합편성", "케이블", "스카이라이프", "해외위성", "라디오"]


class DAUM(EPGProvider):
    """EPGProvider for DAUM

    데이터: rawhtml
    요청수: #channels
    특이사항:
    - 최대 7일치를 한 번에
    - 프로그램 시작 시각만 제공
    """

    referer = None
    no_endtime = True
    title_regex = r"^(?P<title>.*?)\s?([\<\(]?(?P<part>\d{1})부[\>\)]?)?\s?(<(?P<subname1>.*)>)?\s?((?P<epnum>\d+)회)?\s?(<(?P<subname2>.*)>)?$"

    def get_svc_channels(self) -> List[dict]:
        svc_channels = []
        url = "https://search.daum.net/search?DA=B3T&w=tot&rtmaxcoll=B3T&q={}"
        channelsel1 = '#channelNaviLayer > div[class^="layer_tv layer_all"] ul > li'
        channelsel2 = 'div[class="wrap_sub"] > span > a'
        for c in CH_CATE:
            search_url = url.format(f"{c} 편성표")
            data = self.request(search_url)
            soup = BeautifulSoup(data)
            if not soup.find_all(attrs={"disp-attr": "B3T"}):
                continue
            all_channels = [str(x.text.strip()) for x in soup.select(channelsel1)]
            if not all_channels:
                all_channels += [str(x.text.strip()) for x in soup.select(channelsel2)]
            svc_cate = c.replace("스카이라이프", "SKYLIFE")
            for x in all_channels:
                svc_channels.append(
                    {
                        "Name": x,
                        "ServiceId": f"{svc_cate} {x}",
                        "Category": c,
                    }
                )
        return svc_channels

    def get_programs(self, lazy_write: bool = False) -> None:
        url = "https://search.daum.net/search?DA=B3T&w=tot&rtmaxcoll=B3T&q={}"
        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            search_url = url.format(quote(_ch.svcid + " 편성표"))
            data = self.request(search_url)
            try:
                _epgs = self.__epgs_of_days(_ch.id, data)
            except AssertionError as e:
                log.warning("%s: %s", e, _ch)
            except Exception:
                log.exception("프로그램 파싱 중 예외: %s", _ch)
            else:
                _ch.programs.extend(_epgs)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)

    def __epgs_of_days(self, channelid: str, data: str) -> List[EPGProgram]:
        soup = BeautifulSoup(data)
        assert soup.find_all(attrs={"disp-attr": "B3T"}), "EPG 정보가 없거나 없는 채널입니다"
        days = soup.select('div[class="tbl_head head_type2"] > span > span[class="date"]')

        # 연도 추정
        currdate = datetime.now()  # 언제나 basedate보다 미래
        basedate = datetime.strptime(days[0].text.strip(), "%m.%d").replace(year=currdate.year)
        if (basedate - currdate).days > 0:
            basedate = basedate.replace(year=basedate.year - 1)

        _epgs = []
        for nd, _ in enumerate(days):
            hours = soup.select(f'[id="tvProgramListWrap"] > table > tbody > tr > td:nth-of-type({nd+1})')
            assert len(hours) == 24, f"24개의 시간 행이 있어야 합니다: 현재: {len(hours):d}"
            for nh, hour in enumerate(hours):
                for dl in hour.select("dl"):
                    _epg = EPGProgram(channelid)
                    nm = int(dl.select("dt")[0].text.strip())
                    _epg.stime = basedate + timedelta(days=nd, hours=nh, minutes=nm)
                    for atag in dl.select("dd > a"):
                        _epg.title = atag.text.strip()
                    for span in dl.select("dd > span"):
                        class_val = " ".join(span["class"])
                        if class_val == "":
                            _epg.title = span.text.strip()
                        elif "ico_re" in class_val:
                            _epg.rebroadcast = True
                        elif "ico_rate" in class_val:
                            _epg.rating = int(class_val.split("ico_rate")[1].strip())
                        else:
                            # ico_live ico_hd ico_subtitle ico_hand ico_uhd ico_talk ico_st
                            _epg.extras.append(span.text.strip())
                    match = self.title_regex.search(_epg.title)
                    _epg.title = match.group("title") or None
                    _epg.part_num = match.group("part") or None
                    _epg.ep_num = match.group("epnum") or ""
                    _epg.title_sub = match.group("subname1") or ""
                    _epg.title_sub = match.group("subname2") or _epg.title_sub
                    if _epg.part_num:
                        _epg.title += f" {_epg.part_num}부"
                    _epgs.append(_epg)
        return _epgs
