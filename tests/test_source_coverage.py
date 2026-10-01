import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from source_discovery import page_links
from source_transport import decode_page
from source_adapters import parse_special
from event_engine import parse_source,resolve_topic
from refresh_catalog import merge_live


def source(sid,**kw):
    return dict(id=sid,name=sid,url='https://example.org/',type='html',kind='event',region='РФ',topic='it',**kw)


class CoverageTests(unittest.TestCase):
    def test_general_calendar_does_not_default_to_agriculture(self):
        s=source('expomap_russia');s['topic']='mixed'
        self.assertEqual(resolve_topic(s,'ShoesStar 2026'),'other')
        self.assertEqual(resolve_topic(s,'Агропродмаш 2026'),'apk')

    def test_css_cannot_become_summary_or_venue_become_city(self):
        from catalog_core import short_summary,normalize_city
        self.assertEqual(short_summary('ИННОПРОМ. 2s;transition-property:background-color,color;} ИННОПРОМ.'),'')
        self.assertEqual(normalize_city('Пространство ВЕСНА'),'')
        self.assertEqual(normalize_city('Центр событий, Москва'),'Москва')

    def test_filters_and_archives_are_not_pagination(self):
        s=source('test',pagination=True,pagination_params=['PAGEN_1'])
        html='<a href="?PAGEN_1=2">2</a><a href="?PAGEN_2=3">archive</a><a href="?PAGEN_1=2&set_filter=y">reset</a>'
        self.assertEqual(page_links(BeautifulSoup(html,'lxml'),s['url'],s),['https://example.org/?PAGEN_1=2'])

    def test_filter_preserved_and_first_page_normalized(self):
        s=source('test',pagination=True);s['url']='https://example.org/?tag=IT'
        html='<a href="?page=1&tag=IT">1</a><a href="?tag=IT&page=2">2</a><a href="?page=3">wrong</a>'
        self.assertEqual(page_links(BeautifulSoup(html,'lxml'),s['url'],s),['https://example.org/?tag=IT','https://example.org/?page=2&tag=IT'])

    def test_failed_last_page_does_not_drop_discovered_pages(self):
        fetched=[]
        async def fetch(client,url,**kw):
            fetched.append(url)
            if url.endswith('page=9'):raise RuntimeError('unavailable')
            if url.endswith('page=2'):return '<a href="?page=3">3</a>'
            if url.endswith('page=3'):return '<p>last</p>'
            return '<a href="?page=2">2</a><a href="?page=9">9</a>'
        async def run():
            async with httpx.AsyncClient() as client:
                with patch('event_engine.fetch_text',fetch),patch('event_engine.parse_html',return_value=[]):
                    return await parse_source(client,source('test',pagination=True))
        _,result=asyncio.run(run())
        self.assertIn('https://example.org/?page=3',fetched)
        self.assertEqual(result.pages_fetched,3)
        self.assertEqual(result.pages_failed,1)
        self.assertEqual(result.coverage,'partial_fetch_error')

    def test_legacy_charset(self):
        html='<meta charset="windows-1251"><h1>Выставка</h1>'
        self.assertEqual(decode_page(html.encode('cp1251')),html)

    def test_nested_schema_and_visible_event_format(self):
        obj={'@type':'CollectionPage','mainEntity':{'@type':'ItemList','itemListElement':[{'@type':'ListItem','item':{'@type':'Event','name':'Design Space','url':'/event/space','startDate':'2026-10-01','endDate':'2026-11-10','location':{'address':{'addressLocality':'Москва','addressCountry':'RU'}}}}]}}
        html='<script type="application/ld+json">'+json.dumps(obj)+'</script><div class="ev"><h3><a href="/event/space#occurrence-1">Design Space</a></h3><span class="bdg--format">выставка</span></div>'
        rows=parse_special(source('expoafisha'),BeautifulSoup(html,'lxml'))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].event_type,'выставка')
        self.assertEqual(rows[0].country,'Россия')
        self.assertEqual(rows[0].date_end.day,10)

    def test_calendario_weekdays_range_and_address(self):
        html='<div class="item-content__info"><div class="item-content__title"><a href="/test/">IT Forum</a></div><div class="item-content__date">чт, 01 октября - пт, 02 октября 2026 / Конгресс-центр</div><div class="item-content__address">Москва, улица Ленина</div><div class="item-content__nameplate">Форум</div></div>'
        row=parse_special(source('calendario'),BeautifulSoup(html,'lxml'))[0]
        self.assertEqual((row.published_at.day,row.date_end.day),(1,2))
        self.assertEqual(row.location,'Москва')
        self.assertEqual(row.event_type,'форум')

    def test_confec_visible_date_overrides_inconsistent_schema(self):
        html='<h1>IT Conference 2026</h1><div><span>Дата</span><span>12 марта 2026</span></div><script type="application/ld+json">{"@type":"Event","startDate":"2026-11-01"}</script>'
        row=parse_special(source('confec_it'),BeautifulSoup(html,'lxml'))[0]
        self.assertEqual(row.published_at.date().isoformat(),'2026-03-12')

    def test_same_card_corrections_update_without_duplicate(self):
        old=dict(title='Tech Day',source='calendario',organizer_url='https://example.org/day',starts_at='2026-10-01',ends_at='2026-10-01',city='Congress Hall',country='Россия',type='Мероприятие')
        new={**old,'city':'Москва','type':'форум','ends_at':'2026-10-02'}
        rows,added,_=merge_live([old],[new])
        self.assertEqual(added,0)
        self.assertEqual(rows[0]['city'],'Москва')
        self.assertEqual(rows[0]['ends_at'],'2026-10-02')


if __name__=='__main__':unittest.main()
