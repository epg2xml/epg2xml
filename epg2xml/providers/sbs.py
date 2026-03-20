import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from epg2xml.providers import EPGProgram, EPGProvider, no_endtime

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


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
                    "Category": str(ch.get("type") or "").strip() or None,
                }
            )
        return svc_channels

    @no_endtime
    def get_programs(self) -> None:
        for idx, ch in enumerate(self.req_channels):
            log.info("%03d/%03d %s", idx + 1, len(self.req_channels), ch)
            for nd in range(int(self.cfg["FETCH_LIMIT"])):
                day = date.today() + timedelta(days=nd)
                try:
                    epgs = self.__epgs_of_day(ch, day)
                except (KeyError, TypeError, ValueError):
                    log.exception("프로그램 파싱 중 예외: %s, %s", ch, day)
                    continue
                ch.programs.extend(epgs)

    def __epgs_of_day(self, ch, day: date) -> List[EPGProgram]:
        url = self.schedule_url.format(
            year=int(day.year),
            month=int(day.month),
            day=int(day.day),
            schedule_name=self.SUPPORTED_CHANNELS.get(ch.svcid),
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
                log.exception("프로그램 항목 파싱 중 예외: %s, %s, %s", ch, day, item)
                continue
            epgs.append(epg)
        return epgs

    def __epg_of_program(self, channelid: str, day: date, item: dict) -> EPGProgram:
        epg = EPGProgram(channelid)
        epg.title = self.__strip_or_none(item.get("title"))
        epg.desc = self.__strip_or_none(item.get("description"))
        epg.poster_url = self.__strip_or_none(item.get("program_image"))
        epg.rating = int(item.get("target_age") or "0")
        epg.stime = self.__parse_dt(day, item.get("start_time"))
        epg.etime = self.__parse_dt(day, item.get("end_time"))
        if epg.etime and epg.stime and epg.etime <= epg.stime:
            epg.etime += timedelta(days=1)
        return epg

    def __parse_dt(self, day: date, hhmm: Optional[str]) -> Optional[datetime]:
        text = self.__strip_or_none(hhmm)
        if text is None:
            return None
        parts = text.split(":", maxsplit=1)
        if len(parts) != 2:
            return None
        hour_text, minute_text = parts
        if not hour_text.isdigit() or not minute_text.isdigit():
            return None
        hour = int(hour_text)
        minute = int(minute_text)
        if minute > 59:
            return None
        return datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=minute)

    def __strip_or_none(self, value) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
