"""Regression for the real Tilda speaker-page layout missed by the first pass."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from page_facts import extract_facts,related_evidence
from catalog_core import assess_translation,title_key

ROOT_HTML='''<title>БИОПРОМ: промышленность и технологии для человека</title>
<strong>5–6 октября 2026</strong>
<div class="tn-atom">Место проведения: Геленджик</div>
<a href="https://biopromforum.ru/speakers2026">Приглашенные спикеры – 2026</a>
<a href="https://biopromforum.ru/businessprogram26">Программа мероприятий – 2026</a>
<a href="https://biopromforum.ru/speakers2025">Спикеры 2025</a>'''
SPEAKER_HTML='''<title>Спикеры БИОПРОМ — 2026</title><h1>Приглашенные спикеры — 2026</h1>
<ul><li class="t524__col t-list__item"><div class="t524__itemwrapper">
<div class="t524__persname">Марьям Матар</div><div class="t524__persdescr">
основатель, Ассоциация по борьбе с генетическими заболеваниями ОАЭ, создатель генетической карты ОАЭ
</div></div></li></ul>'''

def event():
    return dict(title='БИОПРОМ 2026',starts_at='2026-10-05',ends_at='2026-10-06',country='Россия',city='Геленджик')

class InternationalEvidenceTests(unittest.TestCase):
    def test_hero_date_and_year_suffixed_program_links(self):
        facts=extract_facts(ROOT_HTML,'https://biopromforum.ru/',event())
        self.assertTrue(facts['ok'])
        self.assertEqual(facts['date_start'],'2026-10-05')
        self.assertEqual(facts['related_urls'][0],'https://biopromforum.ru/speakers2026')
        self.assertNotIn('https://biopromforum.ru/speakers2025',facts['related_urls'])
    def test_tilda_person_card_produces_a_real_positive(self):
        proof=related_evidence(SPEAKER_HTML,'https://biopromforum.ru/speakers2026',event())
        verdict=assess_translation(event(),proof)
        self.assertEqual(verdict['audience'],'intl')
        self.assertEqual(verdict['translation_status'],'potential')
        self.assertEqual(verdict['translation_evidence'][0]['speaker_name'],'Марьям Матар')
        self.assertEqual(verdict['translation_evidence'][0]['participation_status'],'invited')
    def test_same_country_on_sponsor_page_does_not_qualify(self):
        html=SPEAKER_HTML.replace('Спикеры БИОПРОМ','Спонсоры БИОПРОМ').replace('Приглашенные спикеры','Партнеры')
        verdict=assess_translation(event(),related_evidence(html,'https://biopromforum.ru/partners2026',event()))
        self.assertEqual(verdict['audience'],'unknown')
    def test_russian_speaking_country_is_not_a_positive(self):
        html=SPEAKER_HTML.replace('ОАЭ','Казахстана')
        self.assertEqual(assess_translation(event(),related_evidence(html,'https://biopromforum.ru/speakers2026',event()))['audience'],'unknown')
    def test_old_program_page_rejected(self):
        self.assertEqual(related_evidence(SPEAKER_HTML.replace('2026','2025'),'https://biopromforum.ru/speakers2025',event()),[])
    def test_old_section_under_current_page_rejected(self):
        html=SPEAKER_HTML.replace('<ul>','<h2>Спикеры 2025</h2><ul>')
        self.assertEqual(related_evidence(html,'https://biopromforum.ru/speakers2026',event()),[])
    def test_conflicting_foreign_venue_excluded(self):
        e=event();e['city']='Великобритания, Лондон'
        evidence=related_evidence(SPEAKER_HTML,'https://biopromforum.ru/speakers2026',event())
        self.assertEqual(assess_translation(e,evidence)['translation_status'],'outside_russia')
    def test_same_bioprom_edition_has_one_key(self):
        self.assertEqual(title_key('III Международный форум БИОПРОМ'),title_key('Биопром: промышленность и технологии для человека 2026'))

if __name__=='__main__':unittest.main()
