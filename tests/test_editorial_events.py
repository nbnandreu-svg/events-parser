import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from catalog_core import prepare_catalog, same_edition, to_browser
from catalog_digest import write_release


class EditorialTests(unittest.TestCase):
    def test_cancellation_and_reschedule_apply_to_specific_edition(self):
        base = dict(id='show', title='Test industry show', starts_at='2030-01-10', ends_at='2030-01-12',
                    city='Москва', country='Россия', organizer_url='https://example.org/show')
        correction = dict(ids=['show'], original_date='2030-01-10', url=base['organizer_url'],
                          status='rescheduled', starts_at='2030-03-10', ends_at='2030-03-12')
        with tempfile.TemporaryDirectory() as tmp, patch('catalog_core.ROOT', Path(tmp)):
            path = Path(tmp) / 'editorial_events.json'
            policy = dict(priority_countries=[], featured=[], corrections=[correction])
            path.write_text(json.dumps(policy), encoding='utf-8')
            rows, _ = prepare_catalog([base])
            self.assertEqual((rows[0]['id'], rows[0]['starts_at']), ('show','2030-03-10'))
            correction['status'] = 'cancelled'
            path.write_text(json.dumps(policy), encoding='utf-8')
            next_year = dict(base, id='next', starts_at='2031-01-10', ends_at='2031-01-12')
            rows, _ = prepare_catalog([base, next_year])
            self.assertEqual([e['id'] for e in rows], ['next'])

    def test_bilingual_search_and_distinct_forums(self):
        row = dict(title='Seymartec Energy 2030', starts_at='2030-03-16', ends_at='2030-03-17',
                   country='Россия', city='Челябинск')
        alias = dict(row, title='Сеймартек Энерджи. Эффективность предприятий')
        self.assertTrue(same_edition(row, alias))
        self.assertFalse(same_edition(row, dict(alias,title='Сеймартек Энерджи СНГ')))
        rows, _ = prepare_catalog([row,alias])
        self.assertEqual(to_browser(rows[0])['aliases'], [alias['title']])

    def test_release_link_survives_next_edition(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = write_release([{'id':'a'}], folder)
            second = write_release([{'id':'b'}], folder)
            self.assertNotEqual(first, second)
            self.assertIsNone(write_release([], folder))
            self.assertEqual(json.loads((folder / (first+'.json')).read_text())['ids'], ['a'])
            self.assertEqual(json.loads((folder / 'latest.json').read_text())['ids'], ['b'])
