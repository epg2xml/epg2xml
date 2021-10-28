import re
import logging
from datetime import datetime, timedelta, date

from epg2xml.providers import EPGProvider, EPGProgram
from epg2xml.utils import request_data

log = logging.getLogger(__name__.split('.')[-1].upper())


class BTV(EPGProvider):
    referer = 'http://mobilebtv.com:8080/view/v3.0/epg'
    title_regex = r'^(.*?)(?:\s*[\(<]([\d,회]+)[\)>])?(?:\s*<([^<]*?)>)?(\((재)\))?$'
    no_endtime = False

    def request(self, url, params, method="GET", output='json'):
        ret = []
        try:
            data = request_data(url, params, method=method, output=output, session=self.sess)
            if data['statusCode'].lower() == 'ok':
                ret = data['data']['ServiceInfoArray']
            else:
                raise ValueError('유효한 응답이 아닙니다: %s' % data['statusMessage'])
        except Exception:
            log.exception(f"Exception while requesting data for {url} with {params}")
        return ret

    def get_svc_channels(self):
        url = 'http://mobilebtv.com:8080/api/v3.0/epg'
        icon_url = 'http://mobilebtv.com:8080/static/images/epg/channelLogo/nsepg_{}.png'
        self.svc_channel_list = [{
            'Name': x['NM_CH'],
            'No': str(x['NO_CH']),
            'Icon_url': icon_url.format(x['ID_SVC']),
            'ServiceId': x['ID_SVC']
        } for x in self.request(url, {})]

    def get_programs(self, lazy_write=False):
        max_ndays = 1
        if int(self.cfg['FETCH_LIMIT']) > max_ndays:
            log.warning(f"""

***********************************************************************

{self.provider_name}는 현재 시간부터 당일 자정까지만 EPG를 제공하고 있습니다.

***********************************************************************
            """)
        url = 'http://mobilebtv.com:8080/api/v3.0/epg'
        params = {
            'o_date': 'EPGDATE',    # 기존에 날짜를 지정해서 가져오는 파라미터가 없어진 것 같다.
            'serviceIds': '|'.join([info.svcid.strip() for info in self.req_channels]),
        }
        genre_code = {
            '1': '드라마',
            '2': '영화',
            '4': '만화',
            '8': '스포츠',
            '9': '교육',
            '11': '홈쇼핑',
            '13': '예능',
            '14': '시사/다큐',
            '15': '음악',
            '16': '라이프',
            '17': '교양',
            '18': '뉴스',
        }
        for nd in range(min(int(self.cfg['FETCH_LIMIT']), max_ndays)):
            day = date.today() + timedelta(days=nd)
            params.update({'o_date': day.strftime('%Y%m%d')})
            channels = {x['ID_SVC']: x['EventInfoArray'] for x in self.request(url + '/details', params)}

            for idx, _ch in enumerate(self.req_channels):
                log.info(f'{idx+1:03d}/{len(self.req_channels):03d} {_ch}')
                for program in channels[_ch.svcid]:
                    try:
                        _prog = EPGProgram(_ch.id)
                        _prog.title = program['NM_TITLE'].replace('...', '>')
                        matches = re.match(self.title_regex, _prog.title)
                        if matches:
                            _prog.title = matches.group(1).strip() if matches.group(1) else ''
                            _prog.title_sub = matches.group(3).strip() if matches.group(3) else ''
                            episode = matches.group(2).replace('회', '') if matches.group(2) else ''
                            _prog.ep_num = '' if episode == '0' else episode
                            _prog.rebroadcast = True if matches.group(5) else False
                        _prog.stime = datetime.strptime(program['DT_EVNT_START'], '%Y%m%d%H%M%S')
                        _prog.etime = datetime.strptime(program['DT_EVNT_END'], '%Y%m%d%H%M%S')
                        _prog.desc = program['NM_SYNOP'] if program['NM_SYNOP'] else ''
                        if 'AdditionalInfoArray' in program:
                            info_array = program['AdditionalInfoArray'][0]
                            if info_array['NM_ACT']:
                                _prog.actors = info_array['NM_ACT'].replace('...', '').strip(', ').split(',')
                            if info_array['NM_DIRECTOR']:
                                _prog.staff = info_array['NM_DIRECTOR'].replace('...', '').strip(', ').split(',')
                        if program['CD_GENRE'] and (program['CD_GENRE'] in genre_code):
                            _prog.category = genre_code[program['CD_GENRE']]
                        _prog.rating = int(program['CD_RATING']) if program['CD_RATING'] else 0
                        _ch.programs.append(_prog)
                    except Exception:
                        log.exception(f'해당 날짜에 EPG 정보가 없거나 없는 채널입니다: {day.strftime("%Y%m%d")} {_ch}')
                if not lazy_write:
                    _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
