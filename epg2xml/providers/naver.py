# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta, date
from xml.sax.saxutils import unescape

from epg2xml.providers import EPGProvider, EPGProgram
from epg2xml.providers import ParserBeautifulSoup as BeautifulSoup
from epg2xml.utils import request_data

log = logging.getLogger(__name__.split('.')[-1].upper())
today = date.today()

# TODO: better to parsing desktop page?


class NAVER(EPGProvider):
    referer = 'https://m.search.naver.com/search.naver?where=m&query=%ED%8E%B8%EC%84%B1%ED%91%9C'
    no_endtime = True

    def get_svc_channels(self):
        channelcate = [
            {'name': '지상파', 'u1': '100'},
            {'name': '종합 편성', 'u1': '500'},
            {'name': '케이블', 'u1': '200'},
            {'name': '스카이라이프', 'u1': '300'},
            {'name': '해외위성', 'u1': '9000'},
            {'name': '라디오', 'u1': '400'}
        ]
        url = 'https://m.search.naver.com/p/csearch/content/nqapirender.nhn'
        params = {
            'key': 'ScheduleChannelList',
            'where': 'nexearch',
            'pkid': '66',
            'u1': 'CATEGORY_CODE',
        }
        for c in channelcate:
            params.update({'u1': c['u1']})
            data = self.request(url, params, method='GET', output='json')
            if data['statusCode'].lower() != 'success':
                log.error('유효한 응답이 아닙니다: %s', data['statusCode'])
                continue
            soup = BeautifulSoup(data['dataHtml'])
            for ch in soup.select('li[class="item"]'):
                try:
                    svcid = ch.select('div > div[data-cid]')[0]['data-cid']
                    name = str(ch.select('div[class="channel_name"] > a')[0].text)
                    self.svc_channel_list.append({
                        'Name': name,
                        'ServiceId': svcid,
                        'Category': c['name'],
                    })
                except:
                    pass

    def get_programs(self, lazy_write=False):
        url = 'https://m.search.naver.com/p/csearch/content/nqapirender.nhn'
        params = {
            'key': 'SingleChannelDailySchedule',
            'where': 'm',
            'pkid': '66',
            'u1': 'SVCID',
            'u2': 'EPGDATE'
        }

        for idx, _ch in enumerate(self.req_channels):
            log.info(f'{idx+1:03d}/{len(self.req_channels):03d} {_ch}')
            for nd in range(int(self.cfg['FETCH_LIMIT'])):
                day = today + timedelta(days=nd)
                params.update({'u1': _ch.svcid, 'u2': day.strftime('%Y%m%d')})
                data = request_data(url, params, method='GET', output='json', session=self.sess)
                try:
                    if data['statusCode'].lower() != 'success':
                        log.error(f'유효한 응답이 아닙니다: {_ch} {data["statusCode"]}')
                        continue
                    soup = BeautifulSoup(''.join(data['dataHtml']))
                    for row in soup.find_all('li', {'class': 'list'}):
                        cell = row.find_all('div')
                        _prog = EPGProgram(_ch.id)
                        _prog.title = unescape(cell[4].text.strip())
                        _prog.stime = datetime.strptime(f'{str(day)} {cell[1].text.strip()}', '%Y-%m-%d %H:%M')
                        for span in cell[3].findAll('span', {'class': 'state_ico'}):
                            span_txt = span.text.strip()
                            if 're' in span['class']:
                                _prog.rebroadcast = True
                            else:
                                _prog.extras.append(span_txt)
                        try:
                            _prog.title_sub = cell[5].text.strip()
                        except:
                            pass
                        _ch.programs.append(_prog)
                except Exception as e:
                    log.error(f'파싱 에러: {_ch}: {str(e)}')
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
