import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from datetime import datetime
import unittest

from event_engine import (
    clean_location,
    event_is_over,
    extract_event_dates,
    guess_location,
    guess_topic_from_text,
    is_eventish_title,
    make_item,
    parse_html,
    resolve_topic,
    source_topic,
    split_title_meta,
    strip_dates_from_place,
)

CNCONF_HTML = """
<html><body>
  <a class="top-slider__item" href="/events/tehnologii_iskusstvennogo_intellekta_2026-09-22.shtml">
    <span class="top-slider__w">Форум</span>
    <span class="top-slider__date">22.09.2026</span>
    <span class="top-slider__title">Технологии искусственного интеллекта</span>
    <span class="top-slider__link">Подробнее</span>
  </a>
  <a class="events__item" href="/events/robotizaciya_biznes_processov_2026_2026-10-13.shtml">
    <span class="events__title"><span class="events__link">Роботизация бизнес-процессов 2026</span></span>
    <span class="events__date">13.10.2026</span>
    <span class="events__status">Конференция</span>
  </a>
</body></html>
"""

CONFEROS_HTML = """
<html><body>
  <div class="header__index-content content">
    <a href="/event/sed_i_ecm_day_2026_2026-09-23">
      <h2 class="content__title title">СЭД И ECM DAY 2026</h2>
      <div class="content__description">23 сентября состоится конференция</div>
      <div class="header-banner__date">23 сентября, 2026</div>
      <a class="content__btn" href="/event/sed_i_ecm_day_2026_2026-09-23#registration">регистрация</a>
    </a>
  </div>
  <ul>
    <li class="events__item events-card">
      <div class="events-card__name">Офлайн-конференция</div>
      <div class="events-card__date">29 сентября, 2026</div>
      <h3 class="events-card__title"><a href="/event/banks_it_day_2026-09-29">BANKS IT DAY</a></h3>
      <a class="events-card__btn" href="/event/banks_it_day_2026-09-29">Подробнее</a>
    </li>
  </ul>
</body></html>
"""



class TopicSourceTests(unittest.TestCase):
    def _source(self, **extra):
        payload = {
            "id": "seed_events_it",
            "name": "Каталог IT",
            "kind": "event",
            "category": "мероприятие",
            "region": "РФ",
            "topic": "it",
        }
        payload.update(extra)
        return payload

    def test_source_topic_defaults_to_apk(self):
        self.assertEqual(source_topic({}), "apk")
        self.assertEqual(source_topic({"topic": "industry"}), "industry")
        self.assertEqual(source_topic({"topic": "спорт"}), "apk")

    def test_event_gets_topic_tag(self):
        item = make_item(
            source=self._source(),
            title="ЦИПР 2026",
            url="https://cipr.ru/",
            published_at=datetime(2026, 5, 19),
        )
        self.assertIn("topic:it", item.tags)

    def test_news_has_no_topic_tag(self):
        item = make_item(
            source={"id": "ikar", "name": "ИКАР", "kind": "news"},
            title="Новость",
            url="https://example.com/news/1",
        )
        self.assertEqual(item.tags, [])

    def test_birthday_is_not_an_event(self):
        self.assertFalse(is_eventish_title("18 сентября, Пт. День рождения Соколов М.К."))
        self.assertTrue(is_eventish_title("ЦИПР 2026"))
        self.assertTrue(is_eventish_title("Связь-2026"))

    def test_cnews_cards(self):
        items = parse_html(
            {
                **self._source(id="cnews_conferences"),
                "url": "https://www.cnconf.ru/",
                "fallback_links": False,
                "selectors": {
                    "item": "a.events__item, a.top-slider__item",
                    "title": ".events__title, .top-slider__title",
                    "link": "a",
                    "date": ".events__date, .top-slider__date",
                    "event_type": ".events__status, .top-slider__w",
                },
            },
            CNCONF_HTML,
        )
        titles = [item.title for item in items]
        self.assertIn("Роботизация бизнес-процессов 2026", titles)
        self.assertIn("Технологии искусственного интеллекта", titles)
        self.assertTrue(all("topic:it" in item.tags for item in items))
        robot = next(item for item in items if "Роботизация" in item.title)
        self.assertEqual(robot.published_at.date().isoformat(), "2026-10-13")

    def test_conferos_cards_skip_registration(self):
        items = parse_html(
            {
                **self._source(id="conferos"),
                "url": "https://conferos.ru/",
                "fallback_links": False,
                "selectors": {
                    "item": "li.events-card, .header__index-content",
                    "title": ".events-card__title, .content__title",
                    "link": ".events-card__title a, a[href*='/event/']",
                    "date": ".events-card__date, .header-banner__date",
                    "event_type": ".events-card__name",
                },
            },
            CONFEROS_HTML,
        )
        titles = [item.title for item in items]
        self.assertIn("BANKS IT DAY", titles)
        self.assertIn("СЭД И ECM DAY 2026", titles)
        self.assertNotIn("регистрация", titles)
        self.assertNotIn("Подробнее", titles)
        banks = next(item for item in items if item.title == "BANKS IT DAY")
        self.assertEqual(banks.published_at.date().isoformat(), "2026-09-29")

    def test_conferos_keeps_next_year_and_industry_it_as_it(self):
        html = """
        <ul>
          <li class="events__item events-card">
            <div class="events-card__name">Офлайн-конференция</div>
            <div class="events-card__date">3&nbsp;марта, 2027</div>
            <h3 class="events-card__title"><a href="/event/unified_communications_day_2026">Unified Communications Day 2026</a></h3>
          </li>
          <li class="events__item events-card">
            <div class="events-card__name">Конференция</div>
            <div class="events-card__date">26 ноября, 2026</div>
            <h3 class="events-card__title"><a href="/event/it_v_promyshlennosti_2026">ИТ в промышленности 2026</a></h3>
          </li>
          <li class="archive__item archive-card">
            <div class="archive-card__name">Конференция</div>
            <div class="archive-card__date">16 сентября, 2026</div>
            <h3 class="archive-card__title"><a href="/event/cifrovye_tehnologii_v_promyshlennosti__praktika_i_keisy">Цифровые технологии в промышленности: практика и кейсы</a></h3>
          </li>
        </ul>
        """
        items = parse_html(
            {
                **self._source(id="conferos"),
                "url": "https://www.conferos.ru/",
                "topic": "it",
                "default_location": "Москва",
                "fallback_links": False,
                "selectors": {
                    "item": "li.events-card, li.archive-card",
                    "title": ".events-card__title, .archive-card__title",
                    "link": ".events-card__title a, .archive-card__title a",
                    "date": ".events-card__date, .archive-card__date",
                    "event_type": ".events-card__name, .archive-card__name",
                },
            },
            html,
        )
        by_title = {item.title: item for item in items}
        self.assertIn("Unified Communications Day 2026", by_title)
        self.assertEqual(
            by_title["Unified Communications Day 2026"].published_at.date().isoformat(),
            "2027-03-03",
        )
        industry_it = by_title["ИТ в промышленности 2026"]
        self.assertIn("topic:it", industry_it.tags)
        self.assertEqual(industry_it.location, "Москва")
        self.assertIn("Цифровые технологии в промышленности: практика и кейсы", by_title)

    def test_conferos_keeps_tadviser_and_skips_old_site(self):
        html = """
        <ul>
          <li class="events__item events-card">
            <div class="events-card__name">Конференция</div>
            <div class="events-card__date">26 ноября, 2026</div>
            <h3 class="events-card__title"><a href="https://tadvisersummit.ru/">TAdviser SummIT 2026</a></h3>
          </li>
          <li class="events__item events-card">
            <div class="events-card__name">Онлайн-конференция</div>
            <div class="events-card__date">6 октября, 2026</div>
            <h3 class="events-card__title"><a href="/event/informacionnaya_bezopasnost_2026__ot_reagirovaniya">IT Security Day 2026</a></h3>
          </li>
          <li class="archive__item archive-card">
            <h3 class="archive-card__title"><a href="https://old.conferos.ru">Архивный сайт Conferos.ru 2014-2024</a></h3>
            <a href="https://old.conferos.ru">Посмотреть</a>
          </li>
        </ul>
        """
        items = parse_html(
            {
                **self._source(id="conferos"),
                "url": "https://www.conferos.ru/",
                "topic": "it",
                "default_location": "Москва",
                "fallback_links": False,
            },
            html,
        )
        by_title = {item.title: item for item in items}
        self.assertEqual(set(by_title), {"TAdviser SummIT 2026", "IT Security Day 2026"})
        self.assertEqual(by_title["TAdviser SummIT 2026"].url, "https://tadvisersummit.ru/")
        self.assertEqual(by_title["IT Security Day 2026"].location, "Онлайн")

    def test_guess_topic_from_title(self):
        self.assertEqual(guess_topic_from_text("Weldex сварка 2026"), "industry")
        self.assertEqual(guess_topic_from_text("АГРОРУСЬ 2026"), "apk")
        self.assertEqual(
            guess_topic_from_text("Smart Agro 2026. Цифровизация АПК"),
            "it",
        )
        self.assertEqual(guess_topic_from_text("Цифропром 2026"), "industry")
        self.assertEqual(
            guess_topic_from_text("Цифровизация промышленности 2026"),
            "industry",
        )
        self.assertEqual(guess_topic_from_text("CPM Collection Premiere Moscow"), "")

    def test_industry_source_agro_title_becomes_apk(self):
        self.assertEqual(
            resolve_topic({"topic": "industry"}, "АГРОРУСЬ - 2026", ""),
            "apk",
        )

    def test_crocus_date_range(self):
        start, end = extract_event_dates("01 Сентября 2026 — 04 Сентября 2026")
        self.assertEqual(start.date().isoformat(), "2026-09-01")
        self.assertEqual(end.date().isoformat(), "2026-09-04")

    def test_crocus_card(self):
        html = """
        <div class="list-item">
          <p class="leftimg-exhibition-about">
            <span>Weldex</span>
            <em>Международная выставка сварочных материалов</em>
            <b>06 Октября 2026 — 09 Октября 2026</b>
          </p>
          <article class="ac-full">
            <a href="https://www.weldex.ru" target="_blank">www.weldex.ru</a>
          </article>
        </div>
        """
        items = parse_html(
            {
                "id": "crocus_expo",
                "name": "Крокус",
                "kind": "event",
                "topic": "industry",
                "url": "https://www.crocus-expo.ru/exhibition/",
                "fallback_links": False,
                "selectors": {
                    "item": "div.list-item",
                    "title": ".leftimg-exhibition-about span",
                    "link": "article a[href^='http']",
                    "date": ".leftimg-exhibition-about b",
                    "summary": ".leftimg-exhibition-about em",
                },
            },
            html,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Weldex")
        self.assertEqual(items[0].published_at.date().isoformat(), "2026-10-06")
        self.assertEqual(items[0].date_end.date().isoformat(), "2026-10-09")
        self.assertIn("topic:industry", items[0].tags)

    def test_mixed_calendar_skips_fashion(self):
        html = """
        <div class="list-item">
          <p class="leftimg-exhibition-about">
            <span>CPM Collection Premiere</span>
            <em>Выставка одежды</em>
            <b>01 Сентября 2026 — 04 Сентября 2026</b>
          </p>
          <article class="ac-full">
            <a href="https://www.cpm-moscow.ru/" target="_blank">сайт</a>
          </article>
        </div>
        <div class="list-item">
          <p class="leftimg-exhibition-about">
            <span>Weldex</span>
            <em>Выставка сварочных материалов</em>
            <b>06 Октября 2026 — 09 Октября 2026</b>
          </p>
          <article class="ac-full">
            <a href="https://www.weldex.ru" target="_blank">сайт</a>
          </article>
        </div>
        """
        items = parse_html(
            {
                "id": "crocus_expo",
                "name": "Крокус",
                "kind": "event",
                "topic": "industry",
                "require_topic_guess": True,
                "url": "https://www.crocus-expo.ru/exhibition/",
                "fallback_links": False,
                "selectors": {
                    "item": "div.list-item",
                    "title": ".leftimg-exhibition-about span",
                    "link": "article a[href^='http']",
                    "date": ".leftimg-exhibition-about b",
                    "summary": ".leftimg-exhibition-about em",
                },
            },
            html,
        )
        titles = [item.title for item in items]
        self.assertEqual(titles, ["Weldex"])

    def test_expo_place_order_is_junk(self):
        self.assertFalse(is_eventish_title("Заказ экспоместа"))

    def test_title_gives_up_its_date_and_city(self):
        self.assertEqual(
            split_title_meta("16-18 сентября - Kazan Digital Week (Казань)"),
            ("Kazan Digital Week", "Казань"),
        )
        self.assertEqual(
            split_title_meta("24 сентября - «Цифроземье» (Воронеж)"),
            ("«Цифроземье»", "Воронеж"),
        )
        self.assertEqual(
            split_title_meta("23.09 25.09.2026 Радэл-экспо: Радиоэлектроника (г. Санкт-Петербург) Специализированная промышленная выставка"),
            ("Радэл-экспо: Радиоэлектроника", "Санкт-Петербург"),
        )
        self.assertEqual(
            split_title_meta("Цифропром 11 ноября 2026 г."),
            ("Цифропром", ""),
        )

    def test_title_without_date_prefix_stays_whole(self):
        title = "Агропромышленная выставка АГРОРУСЬ 2026"
        self.assertEqual(split_title_meta(title), (title, ""))

    def test_calendar_row_keeps_title_date_and_city_together(self):
        html = """
        <ul>
          <li class="cal-row">16-18 сентября - Kazan Digital Week (Казань)</li>
        </ul>
        """
        items = parse_html(
            {
                "id": "digital_economy_calendar",
                "name": "Цифровая экономика",
                "kind": "event",
                "topic": "it",
                "url": "https://d-economy.ru/news/kalendar/",
                "fallback_links": False,
                "selectors": {"item": "li.cal-row"},
            },
            html,
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "Kazan Digital Week")
        self.assertEqual(item.published_at, datetime(2026, 9, 16))
        self.assertEqual(item.date_end, datetime(2026, 9, 18))
        self.assertEqual(item.location, "Казань")

    def test_place_column_drops_the_date(self):
        self.assertEqual(strip_dates_from_place("16.10.2026 | Москва"), "Москва")
        self.assertEqual(strip_dates_from_place("16 сентября - 18 сентября"), "")
        self.assertEqual(
            strip_dates_from_place("Москва, МВЦ «Крокус Экспо»"),
            "Москва, МВЦ «Крокус Экспо»",
        )

    def test_place_that_is_only_a_date_becomes_empty(self):
        self.assertEqual(clean_location("16.09.2026", "Productronica India 2026"), "")

    def test_city_is_taken_by_first_mention(self):
        # раньше побеждала Москва просто потому, что стоит первой в списке городов
        self.assertEqual(guess_location("Казань, партнеры: Москва"), "Казань")
        self.assertEqual(guess_location("Москва, партнеры: Казань"), "Москва")

    def test_short_acronym_needs_a_date_nearby(self):
        self.assertTrue(is_eventish_title("ЦИПР", "19-22 мая - ЦИПР (Нижний Новгород)"))
        self.assertFalse(is_eventish_title("ЦИПР", "раздел сайта"))

    def test_finished_event_is_not_collected(self):
        today = datetime(2026, 9, 18)
        self.assertTrue(event_is_over(datetime(2026, 9, 10), datetime(2026, 9, 12), today=today))
        # идет прямо сейчас
        self.assertFalse(event_is_over(datetime(2026, 9, 16), datetime(2026, 9, 20), today=today))
        # начинается сегодня
        self.assertFalse(event_is_over(datetime(2026, 9, 18), None, today=today))
        # даты нет, она может быть впереди
        self.assertFalse(event_is_over(None, None, today=today))

    def test_first_of_january_without_year_is_a_placeholder(self):
        start, end = extract_event_dates("1 января - 1 января XV газовый форум")
        self.assertIsNone(start)
        self.assertIsNone(end)
        start, _ = extract_event_dates("1 января 2027 года пройдет форум")
        self.assertEqual(start, datetime(2027, 1, 1))

    def test_ict2go_card(self):
        html = """
        <div class="index-events-item media">
          <div class="media-body">
            <div class="organizer">B-FORUMS</div>
            <div class="date-place">16.10.2026 | Москва</div>
            <a class="event-title" href="https://ict2go.ru/events/68689/">GLOBAL TECH FORUM 2026</a>
            <div class="event-themes">
              <div class="event-type">Форум</div>
              <div>Цифровизация,</div>
            </div>
          </div>
        </div>
        """
        items = parse_html(
            {
                "id": "ict2go",
                "name": "ICT2GO",
                "kind": "event",
                "topic": "it",
                "url": "https://ict2go.ru/events/",
                "fallback_links": False,
                "selectors": {
                    "item": "div.index-events-item",
                    "title": "a.event-title",
                    "link": "a.event-title",
                    "date": "div.date-place",
                    "location": "div.date-place",
                    "event_type": "div.event-type",
                    "summary": "div.event-themes",
                },
            },
            html,
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "GLOBAL TECH FORUM 2026")
        self.assertEqual(item.published_at, datetime(2026, 10, 16))
        self.assertEqual(item.location, "Москва")
        self.assertEqual(item.event_type, "форум")
        self.assertIn("topic:it", item.tags)

    def test_expoforum_card_range_without_year(self):
        html = """
        <div class="event-slider-item">
          <a class="item" href="https://www.expoforum.ru/calendar/agrorus-2026/">
            <div class="date mont_09">16 сентября - 18 сентября</div>
            <ul class="options-v2">
              <li><div class="title-v2">Агропромышленная выставка АГРОРУСЬ 2026</div></li>
              <li class="clipsisv4">Международная агропромышленная выставка</li>
            </ul>
          </a>
        </div>
        """
        items = parse_html(
            {
                "id": "expoforum_spb",
                "name": "Экспофорум",
                "kind": "event",
                "topic": "industry",
                "require_topic_guess": True,
                "default_location": "Санкт-Петербург, Экспофорум",
                "url": "https://www.expoforum.ru/calendar/",
                "fallback_links": False,
                "selectors": {
                    "item": "div.event-slider-item",
                    "title": ".title-v2",
                    "link": "a.item",
                    "date": ".date",
                    "summary": "li.clipsisv4",
                },
            },
            html,
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.published_at, datetime(2026, 9, 16))
        self.assertEqual(item.date_end, datetime(2026, 9, 18))
        self.assertIn("topic:apk", item.tags)


if __name__ == "__main__":
    unittest.main()
