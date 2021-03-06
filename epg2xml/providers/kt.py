# -*- coding: utf-8 -*-
import re
import logging
from urllib.parse import unquote
from datetime import datetime, timedelta, date

from epg2xml.providers import EPGProvider, EPGProgram
from epg2xml.providers import ParserBeautifulSoup as BeautifulSoup, SoupStrainer

log = logging.getLogger(__name__.split('.')[-1].upper())


class KT(EPGProvider):
    referer = 'https://tv.kt.com/'
    no_endtime = True

    def get_svc_channels(self):
        url = 'https://tv.kt.com/tv/channel/pChList.asp'
        params = {"ch_type": "1", "parent_menu_id": "0"}
        soup = BeautifulSoup(self.request(url, params, method='POST', output='html'))
        raw_channels = [unquote(x.find('span', {'class': 'ch'}).text.strip()) for x in soup.select('li > a')]
        self.svc_channel_list = [{
            'Name': ' '.join(x.split()[1:]),
            'No': str(x.split()[0]),
            'ServiceId': x.split()[0]
        } for x in raw_channels]

    def get_programs(self, lazy_write=False):
        url = 'https://tv.kt.com/tv/channel/pSchedule.asp'
        params = {
            'ch_type': '1',             # 1: live 2: skylife 3: uhd live 4: uhd skylife
            'view_type': '1',           # 1: daily 2: weekly
            'service_ch_no': 'SVCID',
            'seldate': 'EPGDATE',
        }
        for idx, _ch in enumerate(self.req_channels):
            log.info(f'{idx+1:03d}/{len(self.req_channels):03d} {_ch}')
            for nd in range(int(self.cfg['FETCH_LIMIT'])):
                day = date.today() + timedelta(days=nd)
                params.update({'service_ch_no': _ch.svcid, 'seldate': day.strftime('%Y%m%d')})
                try:
                    data = self.request(url, params, method='POST', output='html')
                    soup = BeautifulSoup(data, parse_only=SoupStrainer('tbody'))
                    for row in soup.find_all('tr'):
                        cell = row.find_all('td')
                        hour = cell[0].text.strip()
                        for minute, program, category in zip(cell[1].find_all('p'), cell[2].find_all('p'), cell[3].find_all('p')):
                            _prog = EPGProgram(_ch.id)
                            _prog.stime = datetime.strptime(f'{str(day)} {hour}:{minute.text.strip()}', '%Y-%m-%d %H:%M')
                            _prog.title = program.text.replace('방송중 ', '').strip()
                            _prog.category = category.text.strip()
                            for image in program.find_all('img', alt=True):
                                grade = re.match(r'([\d,]+)', image['alt'])
                                _prog.rating = int(grade.group(1)) if grade else 0
                            _ch.programs.append(_prog)
                except Exception as e:
                    log.error(f'파싱 에러: {_ch}: {str(e)}')
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
