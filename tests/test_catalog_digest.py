import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from catalog_digest import additions, mail_groups


class DigestTests(unittest.TestCase):
    def test_numbered_meetup_variants_across_sources(self):
        base = dict(id='mws-first', title='MWS Meetup #12 : ИИ в инженерной практике',
                    vertical='it', starts_at='2026-11-26', ends_at='2026-11-26', city='Онлайн',
                    country='Россия', organizer_url='https://confec.ru/example')
        duplicate = dict(base, id='mws-second', title='MWS Meetup #12. ИИ в инженерных практиках',
                         organizer_url='https://ict2go.ru/events/74418')
        separate = dict(duplicate, id='mws-next', title='MWS Meetup #13. ИИ в инженерных практиках')
        cutoff = date(2026, 11, 24)
        self.assertEqual(len(additions([], [base, duplicate, separate], as_of=cutoff)), 2)
        self.assertEqual(additions([base], [duplicate], as_of=cutoff), [])

    def test_grouping_uses_venue_country(self):
        base = dict(id='ru', vertical='it', title='Международная выставка в Москве',
                    starts_at='2026-11-24', ends_at='2026-11-26', city='Москва', country='Россия')
        ashgabat = dict(base, id='tm', title='Turkmentel 2026', city='Ашхабад',
                        priority_evidence={'url':'https://www.turkmentel.net/index.php'})
        unknown = dict(base, id='unknown', title='Событие неизвестной страны', city='', country='')
        rows = additions([], [base, ashgabat, unknown], as_of=date(2026, 11, 24))
        groups = mail_groups(rows)
        self.assertEqual([row['id'] for row in groups['russia']['it']], ['ru'])
        self.assertEqual([(row['id'], row['country']) for row in groups['abroad']], [('tm', 'Туркменистан')])

    def test_new_editions_only(self):
        old = dict(id='original', title='Python Days', vertical='it', starts_at='2027-03-12',
                   ends_at='2027-03-12', organizer_url='https://python.example/conf', city='Москва')
        moved = dict(old, id='moved', starts_at='2027-03-14', ends_at='2027-03-14')
        annual = dict(old, id='annual', starts_at='2028-03-12', ends_at='2028-03-12')
        other = dict(annual, id='other', vertical='other', title='Design Day')
        expired = dict(old, id='closed', title='Past event', starts_at='2026-01-01', ends_at='2026-01-01')
        rows = [moved, annual, dict(annual, id='second-source'), other, expired]
        actual = additions([old], rows, as_of=date(2027, 3, 1))
        self.assertEqual([e['id'] for e in actual], ['annual'])
        self.assertEqual(additions([old], rows, ['apk'], date(2027, 3, 1)), [])
        # A second scheduled meeting can share a landing page with the first.
        actual = additions([old], [old, moved], as_of=date(2027, 3, 1))
        self.assertEqual([e['id'] for e in actual], ['moved'])
