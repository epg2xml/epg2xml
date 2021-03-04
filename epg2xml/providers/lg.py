# -*- coding: utf-8 -*-
import re
import logging
from datetime import datetime, timedelta, date

from epg2xml.providers import EPGProvider, EPGProgram
from epg2xml.providers import ParserBeautifulSoup as BeautifulSoup, SoupStrainer

log = logging.getLogger(__name__.split('.')[-1].upper())


class LG(EPGProvider):
    referer = 'http://www.uplus.co.kr/css/chgi/chgi/RetrieveTvContentsMFamily.hpi'
    title_regex = r'\s?(?:\[.*?\])?(.*?)(?:\[(.*)\])?\s?(?:\(([\d,]+)회\))?\s?(<재>)?$'
    no_endtime = True

    def get_svc_channels(self):
        channelcate = [
            {'name': '지상파', 'category': '00'},
            {'name': '스포츠/취미', 'category': '01'},
            {'name': '영화', 'category': '02'},
            {'name': '뉴스/경제', 'category': '03'},
            {'name': '교양/다큐', 'category': '04'},
            {'name': '여성/오락', 'category': '05'},
            {'name': '어린이/교육', 'category': '06'},
            {'name': '홈쇼핑', 'category': '07'},
            {'name': '공공/종교', 'category': '08'}
        ]
        p_name = re.compile(r'.+(?=[(])')
        p_no = re.compile(r'(?<=Ch[.])\d+')
        p_svcid = re.compile(r"(?<=[('])\d+(?=[',])")
        url = 'https://www.uplus.co.kr/css/chgi/chgi/RetrieveTvChannel.hpi'
        params = {'code': '12810'}
        for c in channelcate:
            params.update({'category': c['category']})
            soup = BeautifulSoup(self.request(url, params, method='GET', output='html'))
            for ch in soup.select('li > a[name="chList"]'):
                ch_txt = ch.text
                self.svc_channel_list.append({
                    'Name': p_name.search(ch_txt).group(),
                    'No': str(p_no.search(ch_txt).group()),
                    'ServiceId': p_svcid.search(ch['onclick']).group(),
                    'Category': c['name'],
                })

    def get_programs(self, lazy_write=False):
        max_ndays = 5
        if int(self.cfg['FETCH_LIMIT']) > max_ndays:
            log.warning(f"""

***********************************************************************

{self.provider_name}는 당일포함 {max_ndays:d}일치만 EPG를 제공하고 있습니다.

***********************************************************************
            """)
        url = 'http://www.uplus.co.kr/css/chgi/chgi/RetrieveTvSchedule.hpi'
        params = {'chnlCd': 'SVCID', 'evntCmpYmd': 'EPGDATE'}
        for idx, _ch in enumerate(self.req_channels):
            log.info(f'{idx+1:03d}/{len(self.req_channels):03d} {_ch}')
            for nd in range(min(int(self.cfg['FETCH_LIMIT']), max_ndays)):
                day = date.today() + timedelta(days=nd)
                params.update({'chnlCd': _ch.svcid, 'evntCmpYmd': day.strftime('%Y%m%d')})
                try:
                    data = self.request(url, params, method='POST', output='html')
                    data = data.replace('<재>', '&lt;재&gt;').replace(' [..', '').replace(' (..', '')
                    soup = BeautifulSoup(data, parse_only=SoupStrainer('table'))
                    if not str(soup):
                        log.warning(f'EPG 정보가 없거나 없는 채널입니다: {_ch}')
                        # 오늘 없으면 내일도 없는 채널로 간주
                        break
                    for row in soup.find('table').tbody.find_all('tr'):
                        cell = row.find_all('td')
                        _prog = EPGProgram(_ch.id)
                        _prog.stime = datetime.strptime(f'{str(day)} {cell[0].text}', '%Y-%m-%d %H:%M')
                        for span in cell[1].select('span > span[class]'):
                            span_txt = span.text.strip()
                            if 'cte_all' in span['class']:
                                _prog.rating = 0 if span_txt == 'All' else int(span_txt)
                            else:
                                _prog.extras.append(span_txt)
                        cell[1].find('span', {'class': 'tagGroup'}).decompose()
                        _prog.title = cell[1].text.strip()
                        matches = re.match(self.title_regex, _prog.title)
                        if matches:
                            _prog.title = matches.group(1).strip() if matches.group(1) else ''
                            _prog.title_sub = matches.group(2).strip() if matches.group(2) else ''
                            _prog.ep_num = matches.group(3) if matches.group(3) else ''
                            _prog.rebroadcast = True if matches.group(4) else False
                        _prog.category = cell[2].text.strip()
                        _ch.programs.append(_prog)
                except Exception as e:
                    log.error(f'파싱 에러: {_ch}: {str(e)}')
            if not lazy_write:
                _ch.to_xml(self.cfg, no_endtime=self.no_endtime)
