from datetime import date, datetime, timedelta
from typing import List

from epg2xml.providers import EPGChannel, EPGProgram, EPGProvider, no_endtime
from epg2xml.utils import norm_text, time_to_td


class SBS(EPGProvider):
    """EPGProvider for SBS

    데이터: jsonapi
    요청수: #channels * #days
    특이사항:
    - 채널 목록 API와 편성표 JSON endpoint가 분리되어 있다.
    """

    referer = "https://www.sbs.co.kr/"
    channel_url = "https://apis.sbs.co.kr/play-api/1.0/onair/channels"
    schedule_url = "https://static.cloud.sbs.co.kr/schedule/{year}/{month}/{day}/{schedule_name}.json"
    SUPPORTED_CHANNELS = {
        "S01": "SBS",  # SBS
        "S02": "ESPN",  # SBS Sports
        "S03": "Plus",  # SBS Plus
        "S04": "ETV",  # SBS funE
        "S05": "Golf",  # SBS Golf
        "S06": "CNBC",  # SBS Biz
        "S07": "Power",  # SBS 파워FM
        "S08": "Love",  # SBS 러브FM
        "S11": "Fil",  # SBS Life
        "S12": "Golf2",  # SBS Golf2
        "S19": "DMB+Radio",  # SBS 고릴라M
    }

    def get_svc_channels(self) -> List[dict]:
        data = self.request(self.channel_url)
        channels = data.get("list") if isinstance(data, dict) else None
        if not isinstance(channels, list):
            raise ValueError(f"Unexpected channel payload type: {type(data).__name__}")

        svc_channels = []
        for ch in channels:
            if (channelid := ch["channelid"]) not in self.SUPPORTED_CHANNELS:
                continue  # 편성표가 없는 채널 제외
            svc_channels.append(
                {
                    "Name": str(ch["channelname"]).strip(),
                    "ServiceId": channelid,
                    "Category": norm_text(ch.get("type")),
                }
            )
        return svc_channels

    @no_endtime
    def get_programs(self) -> None:
        for idx, ch in enumerate(self.req_channels):
            self.log.info("%03d/%03d %s", idx + 1, len(self.req_channels), ch)
            for nd in range(int(self.cfg["FETCH_LIMIT"])):
                day = date.today() + timedelta(days=nd)
                try:
                    epgs = self.__epgs_of_day(ch, day)
                except (KeyError, TypeError, ValueError):
                    self.log.exception("프로그램 파싱 중 예외: %s, %s", ch, day)
                    continue
                ch.programs.extend(epgs)

    def __epgs_of_day(self, ch: EPGChannel, day: date) -> List[EPGProgram]:
        url = self.schedule_url.format(
            year=int(day.year),
            month=int(day.month),
            day=int(day.day),
            schedule_name=self.SUPPORTED_CHANNELS[ch.svcid],
        )
        data = self.request(url)
        if not isinstance(data, list):
            raise ValueError(f"Unexpected schedule payload type: {type(data).__name__} {url}")

        epgs = []
        for item in data:
            try:
                epg = self.__epg_of_program(ch.id, day, item)
                if not epg.title or not epg.stime:
                    raise ValueError("Invalid schedule item")
            except (KeyError, TypeError, ValueError):
                self.log.exception("프로그램 항목 파싱 중 예외: %s, %s, %s", ch, day, item)
                continue
            epgs.append(epg)
        return epgs

    def __epg_of_program(self, channelid: str, day: date, item: dict) -> EPGProgram:
        epg = EPGProgram(channelid)
        epg.title = item.get("title")
        epg.desc = item.get("description")
        epg.poster_url = item.get("program_image")
        epg.rating = int(item.get("target_age") or "0")
        base_day = datetime.combine(day, datetime.min.time())
        start_delta = time_to_td(item.get("start_time"))
        end_delta = time_to_td(item.get("end_time"))
        epg.stime = None if start_delta is None else base_day + start_delta
        epg.etime = None if end_delta is None else base_day + end_delta
        if epg.etime and epg.stime and epg.etime <= epg.stime:
            epg.etime += timedelta(days=1)
        return epg
