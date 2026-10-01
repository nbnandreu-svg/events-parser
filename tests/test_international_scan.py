import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from international_scan import official_links,program_links,finalize_review,Fetcher,fingerprint
from catalog_core import assess_translation
from page_facts import related_evidence

def event(**change):
    return dict(dict(id='test',title='Connected 2026',starts_at='2026-10-07',ends_at='2026-10-07',country='Россия',city='Москва',organizer_url='https://example.org/connected-2026/'),**change)

def proof(text,**extra):return dict(text=text,url='https://example.org/program',edition_year='2026',**extra)

class ScanTests(unittest.TestCase):
    def test_expomap_official_link_wording(self):
        s=BeautifulSoup('<a href="https://organizer.org/">Перейти на официальный сайт</a>','lxml')
        self.assertEqual(official_links(s,'https://expomap.ru/expo/event',event()),['https://organizer.org/'])
    def test_next_data_website(self):
        raw=json.dumps({'id':42,'title':'Connected 2026','website':'https://organizer.org/'},ensure_ascii=False,separators=(',',':'))
        html='<script>self.__next_f.push('+json.dumps([1,raw])+')</script>'
        self.assertEqual(official_links(BeautifulSoup(html,'lxml'),'https://workevent.ru/event/test',event()),['https://organizer.org/'])
    def test_other_event_programme_not_followed(self):
        html='<a href="/connected-2026/program/">Программа</a><a href="/otherforum2026/#programme">Программа</a>'
        links=program_links(BeautifulSoup(html,'lxml'),'https://example.org/connected-2026/',event())
        self.assertEqual(links,['https://example.org/connected-2026/program/'])
    def test_pdf_and_embedded_programme_discovered(self):
        html='<title>Программа Connected 2026</title><a href="https://cdn.example.org/2026/program.pdf">Деловая программа PDF</a><iframe src="https://programme.example.org/2026/"></iframe>'
        self.assertEqual(len(program_links(BeautifulSoup(html,'lxml'),'https://example.org/program/',event())),2)
    def test_date_change_invalidates_review(self):
        self.assertNotEqual(fingerprint(event()),fingerprint(event(starts_at='2027-10-07')))
    def test_explicit_translation_without_language_list(self):
        p=proof('Для участников предусмотрен синхронный перевод всех выступлений.')
        self.assertEqual(assess_translation(event(),[p])['translation_status'],'confirmed')
    def test_chinese_market_topic_is_not_speech_language(self):
        p=proof('Вводный доклад: актуальный китайский опыт. Спикеры расскажут на конференции о маркетплейсах.')
        self.assertEqual(assess_translation(event(),[p])['audience'],'unknown')
    def test_delegations_are_not_speakers(self):
        p=proof('К участию приглашены делегации и эксперты из стран Австралии и Европы.',kind='foreign_speaker')
        self.assertEqual(assess_translation(event(),[p])['audience'],'unknown')
    def test_former_job_is_not_foreign_speaker(self):
        p=proof('Михаил Хавин Руководитель информационной безопасности АСКОНА. Профессиональная деятельность: работал в Райффайзен Банк Австрия.',kind='foreign_speaker',speaker_name='Михаил Хавин')
        self.assertEqual(assess_translation(event(),[p])['audience'],'unknown')
    def test_organizer_history_is_not_current_programme(self):
        p=proof('Корпорация имеет многолетний опыт. В их числе форумы со спикерами из России и других стран (China Business Forum).',kind='foreign_speaker')
        self.assertEqual(assess_translation(event(),[p])['audience'],'unknown')
    def test_kuala_lumpur_mislabeled_as_russia_is_excluded(self):
        p=proof('Спикер из Malaysia',kind='foreign_speaker',speaker_name='James')
        self.assertEqual(assess_translation(event(city='Куала-Лумпур'),[p])['translation_status'],'outside_russia')
    def test_online_only_not_a_physical_russian_event(self):
        p=proof('Доклад на английском языке.')
        self.assertEqual(assess_translation(event(city='Онлайн'),[p])['translation_status'],'online_only')
    def test_real_individual_speaker_card(self):
        html='''<title>Connected 2026</title><div class="speaker-item"><div class="speaker-item-content-top">
        <span class="text">James</span><span class="text-paragraph">Cotti Coffee Professional China</span>
        <span class="speaker-item-position">Вице-президент</span></div></div>'''
        ps=related_evidence(html,'https://example.org/connected-2026/',event())
        verdict=assess_translation(event(),ps)
        self.assertEqual(verdict['audience'],'intl');self.assertEqual(verdict['translation_evidence'][0]['speaker_name'],'James')
    def test_unreadable_pdf_is_not_missing_programme(self):
        row={'pages':[{'matched':True},{'matched':False,'reason':'pdf_needs_ocr'}],'evidence':[],'errors':[],'programme_pages':0}
        self.assertEqual(finalize_review(event(),row)['state'],'programme_unreadable')

class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_urls_share_one_request(self):
        calls=[]
        def handler(request):calls.append(str(request.url));return httpx.Response(200,text='<h1>Event</h1>')
        with tempfile.TemporaryDirectory() as td,patch('international_scan.ROOT',Path(td)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                f=Fetcher(c);await asyncio.gather(f.get('https://example.org/a'),f.get('https://example.org/a'))
        self.assertEqual(len(calls),1)
    async def test_refresh_assigns_ids_before_whole_catalog_review(self):
        from datetime import datetime
        from unittest.mock import AsyncMock
        import refresh_catalog
        from parser_models import NewsItem,ParseResult
        from catalog_core import write_catalog as save
        item=NewsItem(id='source-id',title='Test event 2077',url='https://example.org/event',source_id='test',source_name='Test',
                      kind='event',published_at=datetime(2077,10,1),date_end=datetime(2077,10,1),region='РФ',location='Москва',tags=['topic:it'])
        async def review(events,**kwargs):
            self.assertTrue(events[0]['id'].startswith('ev-'))
            return {'targeted':len(events)}
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'events_upcoming.json').write_text('[]')
            with patch.object(refresh_catalog,'ROOT',root),patch.object(refresh_catalog,'run_parse',AsyncMock(return_value=([item],[ParseResult(source_id='test',source_name='Test',fetched=1,accepted=1)]))),patch('international_scan.scan',side_effect=review),patch.object(refresh_catalog,'write_catalog',side_effect=lambda events,**kw:save(events,root=root,**kw)):
                result=await refresh_catalog.refresh_async(enrich_limit=0,review_international=True)
            self.assertEqual(result['international_review']['targeted'],1)

if __name__=='__main__':unittest.main()
