import logging
from datetime import datetime, timedelta
from urllib.parse import quote

from epg2xml.providers import EPGProgram, EPGProvider
from epg2xml.providers import ParserBeautifulSoup as BeautifulSoup

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class DAUM(EPGProvider):
    """EPGProvider for DAUM

    데이터: rawhtml
    요청수: #channels
    특이사항:
    - 최대 7일치를 한 번에
    - 프로그램 시작 시각만 제공
    """

    referer = ""
    no_endtime = True
    title_regex = r"^(?P<title>.*?)\s?([\<\(]?(?P<part>\d{1})부[\>\)]?)?\s?(<(?P<subname1>.*)>)?\s?((?P<epnum>\d+)회)?\s?(<(?P<subname2>.*)>)?$"

    def get_svc_channels(self):
        url = "https://search.daum.net/search?DA=B3T&w=tot&rtmaxcoll=B3T&q={}"
        channelcate = ["지상파", "종합편성", "케이블", "스카이라이프", "해외위성", "라디오"]
        channelsel1 = '#channelNaviLayer > div[class^="layer_tv layer_all"] ul > li'
        channelsel2 = 'div[class="wrap_sub"] > span > a'
        for c in channelcate:
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
                self.svc_channel_list.append(
                    {
                        "Name": x,
                        "ServiceId": f"{svc_cate} {x}",
                        "Category": c,
                    }
                )

    def get_programs(self, lazy_write=False):
        url = "https://search.daum.net/search?DA=B3T&w=tot&rtmaxcoll=B3T&q={}"
        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            search_url = url.format(quote(_ch.svcid + " 편성표"))
            data = self.request(search_url)
            soup = BeautifulSoup(data)
            if not soup.find_all(attrs={"disp-attr": "B3T"}):
                log.warning("EPG 정보가 없거나 없는 채널입니다: %s", _ch)
                continue
            days = soup.select('div[class="tbl_head head_type2"] > span > span[class="date"]')

            # 연도 추정
            currdate = datetime.now()  # 언제나 basedate보다 미래
            basedate = datetime.strptime(days[0].text.strip(), "%m.%d").replace(year=currdate.year)
            if (basedate - currdate).days > 0:
                basedate = basedate.replace(year=basedate.year - 1)

            for nd, _ in enumerate(days):
                hours = soup.select(f'[id="tvProgramListWrap"] > table > tbody > tr > td:nth-of-type({nd+1})')
                if len(hours) != 24:
                    log.warning("24개의 시간 행이 있어야 합니다: %s, 현재: %d", _ch, len(hours))
                    break
                for nh, hour in enumerate(hours):
                    for dl in hour.select("dl"):
                        _prog = EPGProgram(_ch.id)
                        nm = int(dl.select("dt")[0].text.strip())
                        _prog.stime = basedate + timedelta(days=nd, hours=nh, minutes=nm)
                        for atag in dl.select("dd > a"):
                            _prog.title = atag.text.strip()
                        for span in dl.select("dd > span"):
                            class_val = " ".join(span["class"])
                            if class_val == "":
                                _prog.title = span.text.strip()
                            elif "ico_re" in class_val:
                                _prog.rebroadcast = True
                            elif "ico_rate" in class_val:
                                _prog.rating = int(class_val.split("ico_rate")[1].strip())
                            else:
                                # ico_live ico_hd ico_subtitle ico_hand ico_uhd ico_talk ico_st
                                _prog.extras.append(span.text.strip())
                        match = self.title_regex.search(_prog.title)
                        _prog.title = match.group("title") or None
                        _prog.part_num = match.group("part") or None
                        _prog.ep_num = match.group("epnum") or ""
                        _prog.title_sub = match.group("subname1") or ""
                        _prog.title_sub = match.group("subname2") or _prog.title_sub
                        if _prog.part_num:
                            _prog.title += f" {_prog.part_num}부"
                        _ch.programs.append(_prog)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
