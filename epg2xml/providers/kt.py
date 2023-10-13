import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import unquote

from epg2xml.providers import EPGProgram, EPGProvider
from epg2xml.providers import ParserBeautifulSoup as BeautifulSoup
from epg2xml.providers import SoupStrainer

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class KT(EPGProvider):
    """EPGProvider for KT

    데이터: rawhtml
    요청수: #channels * #days
    특이사항:
    - 가끔 업데이트 지연
    - 프로그램 시작 시각만 제공
    """

    referer = "https://tv.kt.com/"
    no_endtime = True

    def get_svc_channels(self):
        channelcate = [
            # 0은 전체 채널
            {"name": "지상파/종합편성", "id": "3"},
            {"name": "홈쇼핑", "id": "4"},
            {"name": "드라마/여성", "id": "5"},
            {"name": "오락/음악", "id": "6"},
            {"name": "영화/시리즈", "id": "8"},
            {"name": "스포츠", "id": "10"},
            {"name": "취미/레저", "id": "12"},
            {"name": "애니/유아/교육", "id": "137"},
            {"name": "다큐/교양", "id": "206"},
            {"name": "뉴스/경제", "id": "317"},
            {"name": "공공/공익/정보", "id": "442"},
            {"name": "종교", "id": "446"},
            {"name": "오픈", "id": "447"},
            {"name": "유료", "id": "448"},
            {"name": "오디오", "id": "449"},
        ]
        url = "https://tv.kt.com/tv/channel/pChList.asp"
        params = {"ch_type": "1", "parent_menu_id": "0"}
        for c in channelcate:
            params.update({"parent_menu_id": c["id"]})
            soup = BeautifulSoup(self.request(url, method="POST", data=params))
            raw_channels = [unquote(x.find("span", {"class": "ch"}).text.strip()) for x in soup.select("li > a")]
            # 몇몇 채널은 (TV로만 제공, 유료채널) 웹에서 막혀있지만 실제로는 데이터가 있을 수 있다.
            for x in raw_channels:
                self.svc_channel_list.append(
                    {
                        "Name": " ".join(x.split()[1:]),
                        "No": str(x.split()[0]),
                        "ServiceId": x.split()[0],
                        "Category": c["name"],
                    }
                )

    def get_programs(self, lazy_write=False):
        url = "https://tv.kt.com/tv/channel/pSchedule.asp"
        params = {
            "ch_type": "1",  # 1: live 2: skylife 3: uhd live 4: uhd skylife
            "view_type": "1",  # 1: daily 2: weekly
            "service_ch_no": "SVCID",
            "seldate": "EPGDATE",
        }
        for idx, _ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
            for nd in range(int(self.cfg["FETCH_LIMIT"])):
                day = date.today() + timedelta(days=nd)
                params.update({"service_ch_no": _ch.svcid, "seldate": day.strftime("%Y%m%d")})
                try:
                    data = self.request(url, method="POST", data=params)
                    soup = BeautifulSoup(data, parse_only=SoupStrainer("tbody"))
                    for row in soup.find_all("tr"):
                        cell = row.find_all("td")
                        hour = cell[0].text.strip()
                        for minute, program, category in zip(
                            cell[1].find_all("p"), cell[2].find_all("p"), cell[3].find_all("p")
                        ):
                            _prog = EPGProgram(_ch.id)
                            _prog.stime = datetime.strptime(f"{day} {hour}:{minute.text.strip()}", "%Y-%m-%d %H:%M")
                            _prog.title = program.text.replace("방송중 ", "").strip()
                            _prog.categories = [category.text.strip()]
                            for image in program.find_all("img", alt=True):
                                grade = re.match(r"([\d,]+)", image["alt"])
                                _prog.rating = int(grade.group(1)) if grade else 0
                            _ch.programs.append(_prog)
                except Exception:
                    log.exception("파싱 에러: %s", _ch)
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
