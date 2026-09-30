import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from datetime import datetime
import unittest

from event_engine import date_is_grounded, extract_event_dates, parse_date


class EventDateParsingTests(unittest.TestCase):
    def test_month_without_day_is_not_a_date(self):
        self.assertIsNone(parse_date("август 2026"))
        self.assertIsNone(parse_date("— август 2026"))
        self.assertIsNone(parse_date("August 2026"))

    def test_day_and_month_parse(self):
        dt = parse_date("14 октября 2026")
        self.assertEqual(dt, datetime(2026, 10, 14))
        dt = parse_date("14.10.2026")
        self.assertEqual(dt.date(), datetime(2026, 10, 14).date())

    def test_ceremony_beats_founding_date(self):
        text = (
            "Премия учреждена 20 марта 2019 года. "
            "Церемония состоится 14 октября 2026 года."
        )
        start, end = extract_event_dates(text)
        self.assertEqual(start, datetime(2026, 10, 14))
        self.assertEqual(end, datetime(2026, 10, 14))

    def test_founding_date_alone_is_not_event_date(self):
        start, _ = extract_event_dates("Премия учреждена 20 марта 2019 года.")
        self.assertIsNone(start)

    def test_month_only_listing_is_not_event_date(self):
        start, _ = extract_event_dates("Премия «Стандартизатор года» — август 2026")
        self.assertIsNone(start)

    def test_invented_today_is_not_grounded(self):
        blob = "Премия учреждена 20 марта 2019. Афиша: август 2026."
        invented = datetime(2026, 8, 25)
        self.assertFalse(date_is_grounded(invented, blob))
        self.assertTrue(date_is_grounded(datetime(2026, 10, 14), "Церемония 14 октября 2026"))

    def test_comma_year_is_not_this_year(self):
        start, end = extract_event_dates("3 марта, 2027")
        self.assertEqual(start, datetime(2027, 3, 3))
        self.assertEqual(end, datetime(2027, 3, 3))
        start, _ = extract_event_dates("29 сентября, 2026")
        self.assertEqual(start, datetime(2026, 9, 29))
        start, _ = extract_event_dates("3\xa0марта, 2027")
        self.assertEqual(start, datetime(2027, 3, 3))
        title = "23-24 июля в Москве состоится конференция ГДЕ МАРЖА 2026"
        self.assertTrue(date_is_grounded(datetime(2026, 7, 23), title))
        self.assertTrue(date_is_grounded(datetime(2026, 7, 24), title))
        start, end = extract_event_dates(title)
        self.assertEqual(start, datetime(2026, 7, 23))
        self.assertEqual(end, datetime(2026, 7, 24))


if __name__ == "__main__":
    unittest.main()
