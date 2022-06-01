import re
import logging
from functools import partial
from datetime import datetime, timedelta, date
from xml.sax.saxutils import unescape

from epg2xml.providers import EPGProvider, EPGProgram
from epg2xml.providers import ParserBeautifulSoup as BeautifulSoup, SoupStrainer

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class SK(EPGProvider):
    """EPGProvider for SK

    데이터: jsonapi(채널목록) rawhtml(편성표)
    요청수: #channels * #days
    특이사항:
    - 4일치만 제공
    - 프로그램 시작 시각만 제공
    """

    referer = "http://m.skbroadband.com/content/realtime/Channel_List.do"
    title_regex = r"^(.*?)(\(([\d,]+)회\))?(<(.*)>)?(\((재)\))?$"
    no_endtime = True

    @staticmethod
    def replacement(match, tag):
        if match:
            tag = tag.strip()
            programName = unescape(match.group(1)).replace("<", "&lt;").replace(">", "&gt;").strip()
            programName = f'<{tag} class="cont">{programName}'
            return programName
        return ""

    def get_svc_channels(self):
        url = "https://m.skbroadband.com/content/realtime/Realtime_List_Ajax.do"
        params = {"package_name": "PM50305785", "pack": "18"}
        c_name = ""
        for x in self.request(url, method="POST", data=params):
            if x["depth"] == "1":
                c_name = x["m_name"]
            elif x["depth"] == "2" and c_name and c_name not in ["프로모션"]:
                self.svc_channel_list.append(
                    {
                        "Name": unescape(x["m_name"]),
                        "No": str(x["ch_no"]),
                        "ServiceId": x["c_menu"],
                        "Category": c_name,
                    }
                )

    def get_programs(self, lazy_write=False):
        max_ndays = 4
        if int(self.cfg["FETCH_LIMIT"]) > max_ndays:
            log.warning(
                """

***********************************************************************

%s는 당일포함 %d일치만 EPG를 제공하고 있습니다.

***********************************************************************
        """,
                self.provider_name,
                max_ndays,
            )
        url = "http://m.skbroadband.com/content/realtime/Channel_List.do"
        params = {"key_depth2": "SVCID", "key_depth3": "EPGDATE"}

        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            for nd in range(min(int(self.cfg["FETCH_LIMIT"]), max_ndays)):
                day = date.today() + timedelta(days=nd)
                params.update({"key_depth2": _ch.svcid, "key_depth3": day.strftime("%Y%m%d")})
                try:
                    data = self.request(url, params=params)
                    data = re.sub("EUC-KR", "utf-8", data)
                    data = re.sub("<!--(.*?)-->", "", data, 0, re.I | re.S)
                    data = re.sub('<span class="round_flag flag02">(.*?)</span>', "", data)
                    data = re.sub('<span class="round_flag flag03">(.*?)</span>', "", data)
                    data = re.sub('<span class="round_flag flag04">(.*?)</span>', "", data)
                    data = re.sub('<span class="round_flag flag09">(.*?)</span>', "", data)
                    data = re.sub('<span class="round_flag flag10">(.*?)</span>', "", data)
                    data = re.sub('<span class="round_flag flag11">(.*?)</span>', "", data)
                    data = re.sub('<span class="round_flag flag12">(.*?)</span>', "", data)
                    data = re.sub('<strong class="hide">프로그램 안내</strong>', "", data)
                    data = re.sub('<p class="cont">(.*)', partial(SK.replacement, tag="p"), data)
                    data = re.sub('<p class="tit">(.*)', partial(SK.replacement, tag="p"), data)
                    strainer = SoupStrainer("div", {"id": "uiScheduleTabContent"})
                    soup = BeautifulSoup(data, parse_only=strainer)
                    for row in soup.find_all("li", {"class": "list"}):
                        _prog = EPGProgram(_ch.id)
                        _prog.stime = datetime.strptime(
                            f"{str(day)} {row.find('p', {'class': 'time'}).text}", "%Y-%m-%d %H:%M"
                        )
                        for itag in row.select('i[class="hide"]'):
                            itxt = itag.text.strip()
                            if "세 이상" in itxt:
                                _prog.rating = int(itxt.replace("세 이상", "").strip())
                            else:
                                _prog.extras.append(itxt)
                        cell = row.find("p", {"class": "cont"})
                        if cell:
                            if cell.find("span"):
                                cell.span.decompose()
                            _prog.title = cell.text.strip()
                            matches = re.match(self.title_regex, _prog.title)
                            if matches:
                                _prog.title = matches.group(1) or ""
                                _prog.title_sub = matches.group(5) or ""
                                _prog.rebroadcast = bool(matches.group(7))
                                _prog.ep_num = matches.group(3) or ""
                            _ch.programs.append(_prog)
                except Exception:
                    log.exception("파싱 에러: %s", _ch)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
