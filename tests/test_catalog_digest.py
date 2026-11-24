import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from catalog_digest import additions


class DigestTests(unittest.TestCase):
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
