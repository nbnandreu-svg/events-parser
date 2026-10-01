import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from catalog_core import assess_translation

def card(**fields):
    return {'title':'Форум технологий 2026','description':'','starts_at':'2026-10-07','country':'Россия','city':'Москва',**fields}

class MarkerTests(unittest.TestCase):
    def test_international_title_is_enough(self):
        self.assertEqual(assess_translation(card(title='Международная выставка технологий'))['audience'],'intl')
    def test_international_description_is_enough(self):
        self.assertEqual(assess_translation(card(description='Международный форум для бизнеса.'))['audience'],'intl')
    def test_declared_topics_are_used(self):
        self.assertEqual(assess_translation(card(topics=['Международная кооперация']))['audience'],'intl')
    def test_english_marker(self):
        self.assertEqual(assess_translation(card(title='International Technology Forum'))['audience'],'intl')
    def test_foreign_speakers_without_programme_proof(self):
        self.assertEqual(assess_translation(card(description='Приглашены иностранные спикеры.'))['audience'],'intl')
    def test_brics_topic(self):
        self.assertEqual(assess_translation(card(title='Деловой форум БРИКС'))['audience'],'intl')
    def test_foreign_location_still_excluded(self):
        self.assertEqual(assess_translation(card(title='International Forum',country='Германия',city='Берлин'))['audience'],'unknown')
    def test_russian_speaking_country_venue_excluded(self):
        self.assertEqual(assess_translation(card(title='Международный форум',country='Беларусь',city='Минск'))['audience'],'unknown')
    def test_explicit_cis_only_speakers(self):
        self.assertEqual(assess_translation(card(title='Международный форум',description='Спикеры из Беларуси и Казахстана.'))['audience'],'unknown')
    def test_no_marker(self):
        self.assertEqual(assess_translation(card())['audience'],'unknown')

if __name__=='__main__':unittest.main()
