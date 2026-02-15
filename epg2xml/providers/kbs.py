import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import List

from epg2xml.providers import EPGProgram, EPGProvider

log = logging.getLogger(__name__.rsplit(".", maxsplit=1)[-1].upper())


class KBS(EPGProvider):
    """EPGProvider for KBS"""

    referer = "https://onair.kbs.co.kr"
    tps = 2.0

    channel_url = "https://onair.kbs.co.kr"
    schedule_url = "https://static.api.kbs.co.kr/mediafactory/v1/schedule/weekly"
    myk_schedule_url = "https://cfpwwwapi.kbs.co.kr/api/v1/myk/weekly"
    max_batch_channels = 5  # 20까지 가능하지만 과도한 요청을 방지하기 위해...

    PTN_CHANNEL_LIST = re.compile(
        r"var\s+channelList\s*=\s*JSON\.parse\('(?P<data>.*?)'\);",
        flags=re.DOTALL,
    )

    def get_svc_channels(self) -> List[dict]:
        text = self.request(self.channel_url)
        if not (m := self.PTN_CHANNEL_LIST.search(text)):
            raise ValueError("channelList variable not found")
        data = json.loads(m.group("data").replace('\\"', '"').replace("\\/", "/").replace("\\'", "'"))

        svc_channels = []
        for group in data["channel"]:
            category = group.get("channel_group_name") or group.get("channel_group")
            for ch in group["channel_master"]:
                svc_channels.append(
                    {
                        "Name": str(ch.get("title", "")).strip(),
                        "ServiceId": ch["channel_code"],
                        "Category": str(category).strip() if category else None,
                        "Icon_url": ch.get("image_path_channel_logo") or None,
                    }
                )
        return svc_channels

    def get_programs(self) -> None:
        max_ndays = 7
        fetch_days = min(int(self.cfg["FETCH_LIMIT"]), max_ndays)
        if int(self.cfg["FETCH_LIMIT"]) > max_ndays:
            log.warning(
                """

***********************************************************************

%s는 당일포함 %d일치만 EPG를 제공한다고 가정하고 있습니다.

***********************************************************************
                """,
                self.provider_name,
                max_ndays,
            )

        start_day = date.today()
        end_day = start_day + timedelta(days=fetch_days - 1)
        start_ymd = start_day.strftime("%Y%m%d")
        end_ymd = end_day.strftime("%Y%m%d")

        groups = {}
        for idx, ch in enumerate(self.req_channels):
            endpoint, local_station_code, channel_code = None, "00", ch.svcid  # default
            if ch.svcid.startswith(("cctv", "nvod")):
                endpoint = self.myk_schedule_url
            else:
                endpoint = self.schedule_url
                try:
                    local_station_code, channel_code = ch.svcid.split("_", maxsplit=1)
                except ValueError:
                    pass
            item = (idx, ch, endpoint, local_station_code, channel_code)
            groups.setdefault((endpoint, local_station_code), []).append(item)

        for (endpoint, local_station_code), bucket in groups.items():
            for offset in range(0, len(bucket), self.max_batch_channels):
                batch = bucket[offset : offset + self.max_batch_channels]
                channel_codes = ",".join(code for _, _, _, _, code in batch)
                params = {
                    "channel_code": channel_codes,
                    "program_planned_date_from": start_ymd,
                    "program_planned_date_to": end_ymd,
                }
                if endpoint == self.schedule_url:
                    params["local_station_code"] = local_station_code

                try:
                    data = self.request(endpoint, params=params)
                except Exception:
                    log.exception("프로그램 요청 중 예외: endpoint=%s params=%s", endpoint, params)
                    continue

                sch_map = self.__build_schedule_map(data)
                for idx, _ch, _, _, channel_code in batch:
                    log.info("%03d/%03d %s", idx + 1, len(self.req_channels), _ch)
                    try:
                        _epgs = self.__epgs_of_channel(_ch.id, sch_map.get((local_station_code, channel_code), []))
                    except Exception:
                        log.exception("프로그램 파싱 중 예외: %s", _ch)
                        continue
                    _ch.programs.extend(_epgs)

    def __build_schedule_map(self, data: List[dict]) -> dict:
        if not isinstance(data, list):
            raise ValueError("Unexpected schedule payload")

        sch_map = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("local_station_code") or "00"), str(item.get("channel_code") or ""))
            if not key[1]:
                continue
            schedules = item.get("schedules")
            if not isinstance(schedules, list):
                continue
            sch_map.setdefault(key, []).extend(schedules)
        return sch_map

    def __epgs_of_channel(self, channelid: str, schedules: List[dict]) -> List[EPGProgram]:
        epg_items = []
        for sch in schedules:
            try:
                _epg = self.__epg_of_program(channelid, sch)
            except Exception:
                log.exception("프로그램 파싱 중 예외: %s", sch)
                continue

            dedup_key = sch.get("schedule_unique_id") or (channelid, _epg.stime, _epg.title)
            epg_items.append((dedup_key, _epg))

        # dedup by stable schedule key if provided; fallback to (stime,title)
        unique = {}
        for key, epg in epg_items:
            unique[key] = epg
        return sorted(unique.values(), key=lambda x: x.stime)

    def __epg_of_program(self, channelid: str, sch: dict) -> EPGProgram:
        _epg = EPGProgram(channelid)
        program_date = datetime.strptime(sch["program_planned_date"], "%Y%m%d")
        stime_delta = self.__parse_timedelta(sch["program_planned_start_time"])
        if stime_delta is None:
            raise ValueError("invalid program_planned_start_time")
        etime_delta = self.__parse_timedelta(sch["program_planned_end_time"])
        if etime_delta is None:
            raise ValueError("invalid program_planned_end_time")
        if etime_delta < stime_delta:
            # API에서 간헐적으로 발생하는 시간 역전(human error) 데이터를 보정한다.
            etime_delta += timedelta(days=1)
        _epg.stime = program_date + stime_delta
        _epg.etime = program_date + etime_delta
        _epg.title = self.__strip_or_none(sch.get("program_title"))
        if not _epg.title:
            _epg.title = self.__strip_or_none(sch.get("programming_table_title"))
        if not _epg.title:
            raise ValueError("empty program_title")
        _epg.title_sub = self.__strip_or_none(sch.get("program_subtitle"))
        _epg.ep_num = self.__strip_or_none(sch.get("program_sequence_number"))

        _epg.rating = 0
        if grade := sch.get("deliberation_grade"):
            if m := re.search(r"\d+", grade):
                _epg.rating = int(m.group(0))

        if rerun := sch["rerun_classification"]:
            _epg.rebroadcast = "재" in rerun  # 전연령, x세 이상

        # 규칙이 애매해서 가장 strict한 콤마로만 구분
        if actors := sch.get("program_actor"):
            _epg.cast = [{"name": x.strip(), "title": "actor"} for x in actors.split(",") if x.strip()]
        if staff := sch.get("program_staff"):
            _epg.crew = [{"name": x.strip(), "title": "director"} for x in staff.split(",") if x.strip()]

        _epg.desc = self.__strip_or_none(sch.get("program_intention"))

        if image_url := sch.get("image_w"):
            _epg.poster_url = image_url
        return _epg

    def __parse_timedelta(self, value):
        text = str(value).strip().replace(":", "")
        if not text.isdecimal():
            return None
        if len(text) != 8:
            return None

        # HHMMSSff (8자리). ff는 밀리초 유사 하위단위로 간주하며 버림
        hour = int(text[:2])  # 24시 초과(예: 27, 28) 허용
        minute = int(text[2:4])
        second = int(text[4:6])

        if minute > 59 or second > 59:
            return None
        return timedelta(hours=hour, minutes=minute, seconds=second)

    def __strip_or_none(self, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None
