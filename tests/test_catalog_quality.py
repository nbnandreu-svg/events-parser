import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from catalog_core import assess_translation,prepare_catalog,write_catalog,short_summary,canonical_url,same_edition
from page_facts import extract_facts
from event_engine import parse_html
from refresh_catalog import merge_live

def event(**kw):
    return dict(title='IT Security Day 2026',starts_at='2026-10-06',ends_at='2026-10-06',country='Россия',city='Москва',
                organizer_url='https://conferos.ru/event/security',source='conferos',description='',**kw)

def proof(text,**kw):
    return dict(text=text,url='https://example.org/program',edition_year='2026',**kw)

class AudienceTests(unittest.TestCase):
    def check(self,text,expected='unknown',country='Россия',**kw):
        e=event();e['country']=country
        self.assertEqual(assess_translation(e,[proof(text,**kw)])['audience'],expected)

    def test_brand_international_is_not_proof(self):self.check('Международная выставка, экспоненты из Китая и Германии.')
    def test_foreign_exhibitors_are_not_speakers(self):self.check('Участники выставки из Индии и Китая.')
    def test_russian_speaking_countries_do_not_qualify(self):self.check('Спикеры из Беларуси, Казахстана и Кыргызстана.',kind='foreign_speaker')
    def test_foreign_speaker_qualifies_as_potential(self):
        self.check('Спикер Ли Вэй, Китай.','intl',kind='foreign_speaker')
    def test_speaker_explicitly_speaks_russian(self):self.check('Спикер из Китая выступит на русском.',kind='foreign_speaker')
    def test_translation_has_language_and_source(self):self.check('Предусмотрен синхронный перевод с английского на русский.','intl')
    def test_country_of_venue_must_be_russia(self):self.check('Предусмотрен синхронный перевод с английского на русский.',country='Казахстан')
    def test_negated_translation(self):self.check('Рабочий язык английский, синхронный перевод не предусмотрен.')
    def test_working_languages_alone_do_not_prove_foreign_speakers(self):self.check('Рабочие языки: русский и английский.')
    def test_previous_edition_not_reused(self):
        p=proof('Синхронный перевод с английского языка.');p['edition_year']='2025'
        self.assertEqual(assess_translation(event(),[p])['audience'],'unknown')

class CatalogTests(unittest.TestCase):
    def test_different_editions_remain_separate(self):
        a=event();b=event();b['starts_at']=b['ends_at']='2026-11-06'
        self.assertFalse(same_edition(a,b))
    def test_exact_date_duplicate_merges_and_retains_sources(self):
        a=event();b=event();b.update(source='ict2go',organizer_url='https://ict2go.ru/events/123/')
        with patch('catalog_core.today',return_value=date(2026,10,1)):rows,removed=prepare_catalog([a,b])
        self.assertEqual(len(rows),1);self.assertEqual(set(rows[0]['sources']),{'conferos','ict2go'})
    def test_same_title_different_cities_stays_separate(self):
        a=event();b=event();b['city']='Казань';self.assertFalse(same_edition(a,b))
    def test_roman_numeral_rule_does_not_destroy_viv_brand(self):
        from catalog_core import title_key
        self.assertEqual(title_key('VIV Asia 2027'),'viv asia')
        a=event();b=event();a['title']=b['title']='EIMA 2027'
        self.assertTrue(same_edition(a,b))
    def test_legacy_colliding_ids_become_unique(self):
        a=event();b=event();a['id']=b['id']='legacy'
        b['starts_at']=b['ends_at']='2026-11-06'
        with patch('catalog_core.today',return_value=date(2026,10,1)):rows,_=prepare_catalog([a,b])
        self.assertEqual(len({e['id'] for e in rows}),2)
    def test_generic_title_does_not_merge_unrelated_events(self):
        a=event();b=event();a['title']='Международный форум';b['title']='Другой форум'
        b['organizer_url']='https://example.org/other';self.assertFalse(same_edition(a,b))
    def test_refresh_does_not_erase_enriched_description(self):
        a=event();a.update(description='Описание после проверки страницы организатора.',description_checked_at='2026-10-01')
        b=event();b['description']='Конференция'
        rows,_,_=merge_live([a],[b]);self.assertEqual(rows[0]['description'],a['description'])
    def test_empty_source_does_not_remove_future_events(self):self.assertEqual(len(merge_live([event()],[])[0]),1)
    def test_export_keeps_classification_and_stable_id(self):
        a=event();a['language_evidence']=[proof('Предусмотрен синхронный перевод с английского языка.')]
        with tempfile.TemporaryDirectory() as d,patch('catalog_core.today',return_value=date(2026,10,1)):
            root=Path(d);write_catalog([a],root=root);saved=json.loads((root/'events_upcoming.json').read_text(encoding='utf-8'))
            first=saved[0]['id'];write_catalog(saved,root=root)
            self.assertEqual(json.loads((root/'events_upcoming.json').read_text(encoding='utf-8'))[0]['id'],first)
            self.assertIn('"audience": "intl"',(root/'events-data.js').read_text(encoding='utf-8'))
    def test_tracking_url_normalization(self):
        self.assertEqual(canonical_url('http://www.example.org/event/?utm_source=a&id=2'),'http://example.org/event?id=2')
    def test_catalog_boilerplate_is_not_summary(self):
        self.assertEqual(short_summary('Информация о международных, национальных, региональных выставках и ярмарках.'),'')
    def test_summary_is_short_and_not_html(self):
        s=short_summary('<p>Конференция посвящена безопасности корпоративных данных и сетей.</p><p>Участники разберут практические примеры защиты инфраструктуры.</p>')
        self.assertNotIn('<',s);self.assertLessEqual(len(s),460);self.assertTrue(s.endswith('.'))

class DetailTests(unittest.TestCase):
    def test_speaker_name_is_not_city(self):
        raw='''<title>IT Security Day 2026</title><h1>IT Security Day 2026</h1>
        <div class="conference__date">6 октября, 2026</div>
        <section class="about"><p>Конференция посвящена защите корпоративных сетей и управлению доступом.</p></section>
        <div class="speakers"><h2>Владимир Ким</h2><p>Представитель компании Екатеринбург Яблоко</p></div>'''
        f=extract_facts(raw,'https://conferos.ru/event/security',event())
        self.assertTrue(f['ok']);self.assertEqual(f['city'],'');self.assertNotIn('Владимир',f['description'])
    def test_wrong_event_description_rejected(self):
        self.assertFalse(extract_facts('<h1>Фестиваль уличной еды 2026</h1>','https://example.org',event())['ok'])
    def test_previous_edition_rejected(self):
        self.assertEqual(extract_facts('<h1>IT Security Day 2025</h1>','https://example.org',event())['reason'],'edition_mismatch')
    def test_tadviser_inherits_month_but_does_not_claim_confirmed_year(self):
        src=dict(id='tadviser_calendar',name='TAdviser',url='https://tadviser.ru/calendar',kind='event',topic='it')
        raw='''<table><tr><td>окт 06</td><td>Конференция</td><td><a href="/a">IT Security Day 2026</a></td></tr>
        <tr><td>21</td><td>Конференция</td><td><a href="/b">IT Retail Day 2026</a></td></tr></table>
        <table class="calendar_table"><tr><td>21 октября</td><td>День рождения</td><td>Иван</td></tr></table>'''
        rows=parse_html(src,raw);self.assertEqual(len(rows),2);self.assertEqual(rows[1].published_at.month,10)
        self.assertEqual(rows[1].published_at.day,21);self.assertEqual(rows[1].date_certainty,'tentative')

if __name__=='__main__':unittest.main()
