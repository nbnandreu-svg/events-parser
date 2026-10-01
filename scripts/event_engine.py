from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from parser_models import NewsItem, ParseResult
ROOT = Path(__file__).resolve().parent.parent
SEED_EVENTS_PATH = ROOT / 'data/seed_events.yaml'
SOURCES_PATH = ROOT / 'sources.yaml'

EVENT_URL_HINTS = (
    "/afisha/",
    "/events",
    "/expo",
    "/anonsy",
    "/vystavki",
    "/education",
    "/event",
    "/exhibition",
    "/calendar",
    "/meropriyat",
    "/dairyevents",
    "/sobytiya",
    "/cropcalendar",
    "/exhibitions/",
    "/conferences",
    "/conference",
)
SKIP_TITLES = {
    "узнать подробнее",
    "узнать больше",
    "в календарь",
    "все события",
    "все агрособытия",
    "каталог",
    "читать далее",
    "подробнее",
    "далее",
    "зарегистрироваться",
    "регистрация",
    "заявка на доклад",
    "заказ экспоместа",
    "календарь выставок",
    "организаторы выставок",
    "предыдущий месяц",
    "предыдущий год",
    "справочник компаний",
    "правила подписки",
}
WEEKDAYS = {
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
    "пон",
    "втр",
    "срд",
    "чет",
    "пят",
    "суб",
    "вск",
    "пн",
    "вт",
    "ср",
    "чт",
    "пт",
    "сб",
    "вс",
}
CITY_HINTS = (
    "Москва",
    "Санкт-Петербург",
    "Петербург",
    "Казань",
    "Краснодар",
    "Нижний Новгород",
    "Воронеж",
    "Новосибирск",
    "Екатеринбург",
    "Ростов-на-Дону",
    "Геленджик",
    "Сочи",
    "Уфа",
    "Самара",
    "Минск",
    "Красноярск",
    "Пермь",
    "Челябинск",
    "Омск",
    "Саратов",
    "Ставрополь",
    "Белгород",
    "Оренбург",
    "Симферополь",
    "Минеральные Воды",
    "Тула",
    "Калуга",
    "Рязань",
    "Волгоград",
    "Астана",
    "Алматы",
    "Ташкент",
    "Париж",
    "Ганновер",
    "Болонья",
    "Кинель",
    "Усть-Лабинск",
    "Зерноград",
    "Тюмень",
    "Якутск",
    "Иннополис",
    "Владивосток",
    "Хабаровск",
    "Иркутск",
    "Кемерово",
    "Барнаул",
    "Ярославль",
    "Тверь",
    "Липецк",
    "Курск",
    "Пенза",
    "Ижевск",
    "Киров",
    "Калининград",
    "Архангельск",
    "Мурманск",
    "Сургут",
    "Великий Новгород",
    "Сколково",
    "Иваново",
    "Владимир",
    "Брянск",
    "Смоленск",
    "Курган",
    "Томск",
    "Магнитогорск",
    "Череповец",
    "Норильск",
)
MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
MONTH_TO_EN = {
    "января": "january",
    "февраля": "february",
    "марта": "march",
    "апреля": "april",
    "мая": "may",
    "июня": "june",
    "июля": "july",
    "августа": "august",
    "сентября": "september",
    "октября": "october",
    "ноября": "november",
    "декабря": "december",
    "январь": "january",
    "февраль": "february",
    "март": "march",
    "апрель": "april",
    "май": "may",
    "июнь": "june",
    "июль": "july",
    "август": "august",
    "сентябрь": "september",
    "октябрь": "october",
    "ноябрь": "november",
    "декабрь": "december",
    "янв": "jan",
    "фев": "feb",
    "мар": "mar",
    "апр": "apr",
    "июн": "jun",
    "июл": "jul",
    "авг": "aug",
    "сен": "sep",
    "окт": "oct",
    "ноя": "nov",
    "дек": "dec",
}
MONTH_GENITIVE = "|".join(MONTHS_RU)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def load_sources(
    kind: Optional[str] = None,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    with SOURCES_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    sources = list(data.get("sources") or [])
    if not include_disabled:
        sources = [s for s in sources if s.get("enabled", True)]
    if kind:
        sources = [s for s in sources if s.get("kind", "news") == kind]
    return sources


def source_kind(source: dict[str, Any]) -> str:
    return source.get("kind", "news")


def make_id(url: str, title: str) -> str:
    raw = f"{url.strip().lower()}|{title.strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def source_topic(source: dict[str, Any]) -> str:
    topic = str(source.get("topic") or "apk").strip().lower()
    if topic in {'mixed','other'}:return 'other'
    if topic not in {"apk", "it", "industry"}:
        return "apk"
    return topic


TOPIC_IT_HINTS = (
    "digital week",
    "искусственн интеллект",
    "нейросет",
    "кибербезопас",
    "киберзащит",
    "информационн технолог",
    "информационн систем",
    "телеком",
    "программн",
    "облачн",
    "дата-центр",
    "дата центр",
    "цод",
    "highload",
    "devops",
    "хакатон",
    "разработчик",
    "cnews",
    "tadviser",
    "conferos",
    "expocifra",
    "expoelectronica",
    "электроника",
    "comnews awards",
    "positive hack",
    "data fusion",
    "cipr",
    "dotnext",
    "smartdata",
    "rigf",
    "crm-систем",
    "erp day",
    "low-code",
    "no-code",
    "большие данные",
    "machine learning",
    "smart agro",
    "цифровая экономика",
    "цифровые технолог",
    "цифровизация hr",
    "цифровизация бизнес",
)
TOPIC_APK_HINTS = (
    "агро",
    "зерн",
    "молок",
    "птицевод",
    "worldfood",
    "foodtech",
    "животнов",
    "растениевод",
    "ветеринар",
    "день поля",
    "дня поля",
    "мясн промыш",
    "куриный король",
    "пищев",
    "продмаш",
    "югагро",
    "агросалон",
    "агрорусь",
    "минводыагро",
    "кормвет",
    "пчеловод",
    "сад и огород",
    "interfood",
)
TOPIC_INDUSTRY_HINTS = (
    "металлообработ",
    "металлург",
    "нефтегаз",
    "нефтехим",
    "weldex",
    "fastenex",
    "сварк",
    "иннопром",
    "mining",
    "горнодобы",
    "горношахт",
    "горно-металлург",
    "рудник",
    "станкостроен",
    "насос",
    "компрессор",
    "полимер",
    "ruplastica",
    "промышлен",
    "электротех",
    "энергет",
    "литмаш",
    "машиностро",
    "heat&power",
    "pcvexpo",
    "ndt russia",
    "криоген",
    "термообработ",
    "expocoating",
    "дефектоскоп",
    "fasttec",
    "нмф-экспо",
    "хими",
    "нефт",
    "цифропром",
    "smart mining",
    "smart oil",
    "oil & gas",
    "oil and gas",
    "rusweld",
    "technoforum",
    "газовой",
    "покрыти",
)
MIXED_CALENDARS = {"crocus_expo", "exponet_all", "expoforum_spb"}
IT_PORTALS = {
    "conferos",
    "cnews_conferences",
    "comnews_conferences",
    "ict2go",
    "ict_moscow",
    "globalcio_events",
    "seed_events_it",
    "digital_economy_calendar",
}


def guess_topic_from_text(text: str) -> str:
    blob = f" {str(text or '').lower().replace('ё', 'е')} "
    if re.search(r"индустрия моды|текстиль и мода|модный товар|детская мода|выставка одежды", blob):
        return ""
    apk = any(hint in blob for hint in TOPIC_APK_HINTS)
    industry = any(hint in blob for hint in TOPIC_INDUSTRY_HINTS)
    it = any(hint in blob for hint in TOPIC_IT_HINTS)
    if apk:
        return "apk"
    if industry:
        return "industry"
    if it:
        return "it"
    if "цифров" in blob:
        return "it"
    return ""


def resolve_topic(source: dict[str, Any], title: str = "", blob: str = "") -> str:
    guessed = guess_topic_from_text(f"{title} {blob}")
    if source.get("id") in IT_PORTALS:
        return "apk" if guessed == "apk" else "it"
    if guessed:
        return guessed
    if source.get("require_topic_guess"):
        return "other"
    return source_topic(source)


def apply_display_topic(item: NewsItem) -> NewsItem:
    guessed = guess_topic_from_text(f"{item.title} {item.summary}")
    existing = next((str(tag)[6:] for tag in (item.tags or []) if str(tag).startswith("topic:")), "")
    if item.source_id in IT_PORTALS:
        topic = "apk" if guessed == "apk" else "it"
    elif guessed:
        topic = guessed
    elif item.source_id in MIXED_CALENDARS:
        topic = "other"
    elif existing in {"apk", "it", "industry", "other"}:
        topic = existing
    else:
        topic = "apk" if item.kind == "event" else ""
    rest = [tag for tag in (item.tags or []) if not str(tag).startswith("topic:")]
    if topic:
        rest.append(f"topic:{topic}")
    item.tags = rest
    return item


def make_item(
    *,
    source: dict[str, Any],
    title: str,
    url: str,
    published_at: Optional[datetime] = None,
    date_end: Optional[datetime] = None,
    summary: str = "",
    location: str = "",
    event_type: str = "",
    date_certainty: str = "",
    date_evidence: str = "",
) -> NewsItem:
    kind = source_kind(source)
    if kind == "event":
        title, place_from_title = split_title_meta(title)
        if place_from_title and not location:
            location = place_from_title
    certainty = date_certainty or source.get("date_certainty") or (
        "tentative" if kind == "event" and not published_at else "confirmed"
    )
    if kind=='event' and published_at and date_evidence and not re.search(r'\b20\d{2}\b',date_evidence) and not date_certainty:
        certainty='tentative'
    if kind == "event" and not published_at and certainty == "confirmed":
        certainty = "unconfirmed"
    location = clean_location(location, f"{title} {summary}")
    if not location:
        location = str(source.get("default_location") or "")
    event_type = event_type or str(source.get("event_type") or "")
    tags = []
    if kind == "event":
        tags.append(f"topic:{resolve_topic(source, title, summary)}")
    return NewsItem(
        id=make_id(url, title + ('|' + published_at.isoformat() if kind=='event' and published_at else '')),
        title=title,
        url=url,
        source_id=source["id"],
        source_name=source["name"],
        published_at=published_at,
        date_end=date_end,
        date_evidence=clean_text(date_evidence)[:2000],
        summary=(summary or "")[:1200],
        body="",
        category=source.get("category", "мероприятие" if kind == "event" else "новости"),
        region=source.get("region", "РФ"),
        location=location,
        event_type=event_type or source.get("event_type", ""),
        date_certainty=certainty,
        kind=kind,  # type: ignore[arg-type]
        tags=tags,
    )


def parse_seed_events(source: dict[str, Any]) -> list[NewsItem]:
    path = Path(source.get("path") or SEED_EVENTS_PATH)
    if not path.is_absolute():
        path = ROOT / path
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    items: list[NewsItem] = []
    for row in data.get("events", []):
        title = clean_text(row.get("title", ""))
        url = clean_text(row.get("url") or f"seed://{title}")
        if not title:
            continue
        start = parse_date(row.get("date_start"))
        end = parse_date(row.get("date_end")) or start
        items.append(
            make_item(
                source=source,
                title=title,
                url=url,
                published_at=start,
                date_end=end,
                summary=clean_text(row.get("summary", "")),
                location=clean_text(row.get("location", "")),
                event_type=clean_text(row.get("event_type", "")),
                date_certainty=clean_text(row.get("date_certainty", "")) or "confirmed",
            )
        )
    return items


def text_has_calendar_day(text: str) -> bool:
    """Есть ли в строке день месяца, а не только «август 2026»."""
    raw = str(text or "")
    if re.search(r"\d{4}-\d{2}-\d{2}", raw):
        return True
    if re.search(r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?", raw):
        return True
    months = "|".join(re.escape(name) for name in MONTH_TO_EN)
    if re.search(rf"\d{{1,2}}\s+(?:{months})", raw, re.I):
        return True
    return False


def date_is_grounded(value: Optional[datetime], text: str) -> bool:
    """Дата события должна явно встречаться в тексте, иначе это подстановка «сегодня»."""
    if not value:
        return False
    raw = text or ""
    blob = raw.lower().replace("ё", "е")
    if not blob:
        return False
    iso = value.strftime("%Y-%m-%d")
    if iso in blob or iso in raw:
        return True
    dotted = (
        f"{value.day:02d}.{value.month:02d}.{value.year}",
        f"{value.day}.{value.month}.{value.year}",
        f"{value.day:02d}.{value.month:02d}.{str(value.year)[2:]}",
        f"{value.day:02d}/{value.month:02d}/{value.year}",
        f"{value.day}/{value.month}/{value.year}",
    )
    if any(item in raw for item in dotted):
        return True
    month = MONTHS_RU[value.month - 1]
    day = value.day
    year = value.year
    if re.search(rf"\b{day}\s+{month},?\s+{year}\b", blob):
        return True
    if re.search(rf"\b{day}\s+{month}\b", blob):
        return True
    # «23-24 июля», «8–10 сентября 2026»
    if re.search(rf"\b{day}\s*[.—\-–/]\s*\d{{1,2}}\s+{month}", blob):
        return True
    if re.search(rf"\b\d{{1,2}}\s*[.—\-–/]\s*{day}\s+{month}", blob):
        return True
    if re.search(
        rf"\b{day:02d}\s*[.—\-–]\s*\d{{1,2}}[./]{value.month:02d}[./]{year}",
        raw,
    ):
        return True
    return False


def parse_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "tm_year"):
        try:
            return datetime(*value[:6])
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        try:
            dt = date_parser.parse(text, yearfirst=True, dayfirst=False)
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            return dt
        except Exception:
            pass
    if not text_has_calendar_day(text):
        return None
    lowered = text.lower()
    for ru, en in sorted(MONTH_TO_EN.items(), key=lambda item: -len(item[0])):
        if ru in lowered:
            lowered = lowered.replace(ru, en)
    try:
        dt = date_parser.parse(lowered, dayfirst=True, fuzzy=True)
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        return None


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def default_event_year() -> int:
    year = datetime.now().year
    return year if year >= 2025 else 2026


def normalize_href(href: str, base_url: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    href = href.split()[0]
    if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:")):
        return ""
    if href.startswith("//"):
        href = "https:" + href
    elif re.match(r"^(www\.)", href, re.I) or (
        "://" not in href
        and not href.startswith("/")
        and re.match(r"^[\w.-]+\.[a-z]{2,}(/|$|\?)", href, re.I)
    ):
        href = "https://" + href.lstrip("/")
    return urljoin(base_url, href)


def strip_leading_event_dates(title: str) -> str:
    text = title or ""
    text = re.sub(
        r"^\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?(?:\s*[—\-–]\s*|\s+)\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\s*[—\-–:|]?\s*",
        "",
        text,
    )
    text = re.sub(
        r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}(?:\s*[—\-–]\s*\d{1,2}[./]\d{1,2}[./]\d{2,4})?\s*[—\-–:|]?\s*",
        "",
        text,
    )
    text = re.sub(
        rf"^\d{{1,2}}(?:\s*[—\-–]\s*\d{{1,2}})?\s+(?:{MONTH_GENITIVE})"
        rf"(?:\s*[—\-–]\s*\d{{1,2}}\s+(?:{MONTH_GENITIVE}))?"
        r"(?:\s+\d{4})?(?:\s*г(?:ода|\.)?(?![а-я]))?\s*[—\-–:|]?\s*",
        "",
        text,
        flags=re.I,
    )
    return clean_text(text) or (title or "")


def fragment_url(base_url: str, title: str) -> str:
    slug = re.sub(
        r"[^a-z0-9а-яё]+", "-", strip_leading_event_dates(title).lower()
    ).strip("-")[:48]
    return f"{base_url.rstrip('/')}/#{slug or 'event'}"


def _schema_objects(data: object) -> list[dict]:
    if isinstance(data, list):
        items: list[dict] = []
        for row in data:
            items.extend(_schema_objects(row))
        return items
    if not isinstance(data, dict):
        return []
    if "@graph" in data:
        return _schema_objects(data.get("@graph"))
    return [data]


def _is_schema_event(value: object) -> bool:
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        name = str(row or "").split("/")[-1]
        if name.endswith("Event"):
            return True
    return False


def _schema_location(value: object) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return ", ".join(filter(None, (_schema_location(v) for v in value)))
    if isinstance(value, dict):
        addr = value.get("address")
        parts = [
            value.get("name") or "",
            _schema_location(addr) if not isinstance(addr, str) else addr,
            value.get("addressLocality") or "",
            value.get("streetAddress") or "",
        ]
        return clean_text(", ".join(p for p in parts if p))
    return ""


def parse_jsonld_events(
    source: dict[str, Any], soup: BeautifulSoup, seen_urls: set[str]
) -> list[NewsItem]:
    items: list[NewsItem] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in _schema_objects(data):
            if not _is_schema_event(obj.get("@type")):
                continue
            title = clean_text(str(obj.get("name") or obj.get("headline") or ""))
            url = clean_text(str(obj.get("url") or source.get("url") or ""))
            if url and not url.startswith("http"):
                url = normalize_href(url, source["url"])
            start = parse_date(obj.get("startDate"))
            end = parse_date(obj.get("endDate")) or start
            loc = _schema_location(obj.get("location"))
            desc = clean_text(str(obj.get("description") or ""))[:400]
            if not title:
                continue
            if not url:
                url = fragment_url(source["url"], title)
            _append_event(
                items,
                seen_urls,
                source=source,
                title=title,
                url=url,
                blob=f"{obj.get('startDate', '')} {obj.get('endDate', '')} {title} {desc} {loc}",
                summary=desc or loc,
                location=loc,
                published=start,
                date_end=end,
            )
    return items


def parse_dated_blocks(
    source: dict[str, Any], soup: BeautifulSoup, seen_urls: set[str]
) -> list[NewsItem]:
    """Карточки календаря без ссылки: дата + название, как на apknews.su/expo/."""
    items: list[NewsItem] = []
    nodes = soup.select(
        ".expo-item, .evnt-item, .djev_event, .event-item, .calendar-item"
    )
    for node in nodes:
        title = _first_text(
            node, ".expo-item_title, .evnt-title, .title, h2, h3, h4, a"
        )
        if not title:
            title = clean_text(node.get_text(" ", strip=True))
        title = strip_leading_event_dates(title)
        blob = clean_text(node.get_text(" ", strip=True))
        if not title or not is_eventish_title(title, blob):
            continue
        link = href_from_node(node, source["url"]) or fragment_url(source["url"], title)
        date_text = _first_text(node, ".expo-item_date, .date, time, .evnt-date")
        _append_event(
            items,
            seen_urls,
            source=source,
            title=title,
            url=link,
            blob=f"{date_text} {blob}",
            summary=blob[:400],
        )
    return items


def href_from_node(node, base_url: str) -> str:
    if node is None:
        return ""
    if getattr(node, "name", None) == "a" and node.get("href"):
        return normalize_href(str(node.get("href")), base_url)
    link = node.find("a", href=True) if hasattr(node, "find") else None
    if link:
        return normalize_href(str(link.get("href")), base_url)
    text = clean_text(node.get_text(" ", strip=True) if hasattr(node, "get_text") else str(node))
    if text.lower().startswith("http"):
        return normalize_href(text, base_url)
    if re.match(r"^(www\.)", text, re.I) or re.match(
        r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", text.split()[0] if text else "", re.I
    ):
        return normalize_href(text.split()[0], base_url)
    return ""


def is_eventish_title(title: str, blob: str = "") -> bool:
    text = f"{title} {blob}".lower()
    title_l = (title or "").lower().strip()
    if not title or title_l in SKIP_TITLES or title_l in WEEKDAYS:
        return False
    if re.match(r"^\d{1,2}\.\d{1,2}\s+\d{1,2}\.\d{1,2}\.\d{4}", title_l):
        return False
    if any(
        bit in title_l
        for bit in (
            "завершила работу",
            "завершил работу",
            "прошла конференц",
            "состоялась выстав",
        )
    ):
        return False
    if len(title) > 180:
        return False
    if len(title) < 6:
        # ЦИПР, ПМЭФ, ТИБО: короткая аббревиатура сходит за название, если рядом есть дата
        acronym = re.fullmatch(r"[A-ZА-ЯЁ][A-ZА-ЯЁ0-9.\-]{2,}", (title or "").strip())
        has_day = bool(re.search(rf"\d{{1,2}}\s+(?:{MONTH_GENITIVE})", blob or ""))
        if not (acronym and has_day):
            return False
    generic = {
        "выставка",
        "форум",
        "конференция",
        "конференции",
        "все конференции",
        "мероприятия",
        "события",
        "календарь",
        "анонсы",
    }
    if title_l in generic:
        return False
    junk = (
        "справочник",
        "подписк",
        "авторизац",
        "реклам",
        "правила ",
        "о компании",
        "контакты",
        "каталог товаров",
        "каталог выстав",
        "список участник",
        "забронировать стенд",
        "схема выстав",
        "проезд и проживан",
        "гостиниц",
        "стать экспонент",
        "личный кабинет",
        "информационная поддержка",
        "журнал «агроинвестор»",
        "журнал \"агроинвестор\"",
        "день рождения",
        "о выставке",
        "для прессы",
        "для посетител",
        "стать посетител",
        "стать участник",
        "заказ экспоместа",
    )
    if any(bit in title_l for bit in junk):
        return False
    if guess_event_type_from_text(text):
        return True
    if re.search(r"20(2[5-8])", text):
        return True
    if any(
        word in text
        for word in ("премия", "практикум", "саммит", "сессия", "день поля", "дни ")
    ):
        return True
    if re.search(rf"\d{{1,2}}\s+(?:{MONTH_GENITIVE})", text):
        return True
    return False


async def fetch_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    verify: bool = True,
) -> str:
    # для отдельных госсайтов с проблемным сертификатом
    if not verify:
        async with httpx.AsyncClient(
            timeout=client.timeout,
            headers=dict(client.headers),
            verify=False,
        ) as insecure:
            response = await insecure.get(url, follow_redirects=True)
    else:
        limits = getattr(client, '_parser_host_limits', None)
        if limits is None:
            limits = {}
            client._parser_host_limits = limits
        gate = limits.setdefault(urlparse(url).netloc.removeprefix('www.'), asyncio.Semaphore(2))
        async with gate:
            response = await client.get(url, follow_redirects=True)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def parse_rss(source: dict[str, Any], content: str) -> list[NewsItem]:
    feed = feedparser.parse(content)
    items: list[NewsItem] = []
    for entry in feed.entries:
        title = clean_text(getattr(entry, "title", "") or "")
        link = clean_text(getattr(entry, "link", "") or "")
        if not title or not link:
            continue
        summary = clean_text(
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        )
        # strip HTML from summary
        summary = clean_text(BeautifulSoup(summary, "lxml").get_text(" ", strip=True))
        published = None
        if getattr(entry, "published_parsed", None):
            published = parse_date(entry.published_parsed)
        if not published and getattr(entry, "published", None):
            published = parse_date(entry.published)
        if not published and getattr(entry, "updated_parsed", None):
            published = parse_date(entry.updated_parsed)
        items.append(
            make_item(
                source=source,
                title=title,
                url=link,
                published_at=published,
                summary=summary,
            )
        )
    return items


def _first_text(node, selectors: str) -> str:
    for sel in [s.strip() for s in selectors.split(",") if s.strip()]:
        found = node.select_one(sel)
        if found:
            text = clean_text(found.get_text(" ", strip=True))
            if text:
                return text
    return ""


def _node_href(node, selectors: str, base_url: str) -> str:
    if getattr(node, "name", None) == "a" and node.get("href"):
        href = normalize_href(str(node.get("href") or ""), base_url)
        if href and not href.lower().endswith(".ics"):
            return href
    for sel in [s.strip() for s in selectors.split(",") if s.strip()]:
        found = node.select_one(sel)
        if found and found.has_attr("href"):
            href = normalize_href(str(found.get("href") or ""), base_url)
            if href.lower().endswith(".ics"):
                continue
            if href:
                return href
        if found and found.name == "a" and found.get("href"):
            href = normalize_href(str(found.get("href") or ""), base_url)
            if href.lower().endswith(".ics"):
                continue
            if href:
                return href
    for a in node.find_all("a", href=True):
        href = normalize_href(str(a.get("href") or ""), base_url)
        if href.lower().endswith(".ics"):
            continue
        if href:
            return href
    return href_from_node(node, base_url)


def _first_datetime(node, selectors: str) -> Optional[datetime]:
    for sel in [s.strip() for s in selectors.split(",") if s.strip()]:
        found = node.select_one(sel)
        if not found:
            continue
        for attr in ("datetime", "content", "data-date", "title"):
            if found.has_attr(attr):
                dt = parse_date(found[attr])
                if dt:
                    return dt
        dt = parse_date(found.get_text(" ", strip=True))
        if dt:
            return dt
    return None


MAX_EVENT_DAYS = 16


def _sane_event_range(
    start: Optional[datetime], end: Optional[datetime]
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Отсекает «2006 — 2026» и прочие годы из подвала страницы."""
    if start and end and end < start:
        start, end = end, start
    if start and start.year < 2025 and end and 2025 <= end.year <= 2028:
        try:
            start = start.replace(year=end.year)
        except ValueError:
            start = end
    if end and end.year < 2025 and start and 2025 <= start.year <= 2028:
        end = start
    if start and not (2025 <= start.year <= 2028):
        start = None
    if end and start and not (2025 <= end.year <= 2028):
        end = start
    if start and end and (end - start).days > MAX_EVENT_DAYS:
        end = start
    return start, end or start


def _date_context_weight(text: str, start: int, end: int) -> int:
    around = text[max(0, start - 56) : min(len(text), end + 24)].lower().replace("ё", "е")
    if re.search(r"состоится|пройдет|проходит|церемони|награжден|дата проведения|начало\s+мероприят", around):
        return 5
    if re.search(r"с\s+\d{1,2}\s+по\s+\d{1,2}|по\s+\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)", around):
        return 4
    if re.search(r"учрежден|основан|заявк|прием заяв|\bдо\s+\d{1,2}\s+|дедлайн|срок подач", around):
        return 0
    return 2


def extract_event_dates(text: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """Даты проведения из текста, не дата публикации и не «август 2026» без дня."""
    if not text:
        return None, None
    months = MONTH_GENITIVE
    year_now = str(default_event_year())
    candidates: list[tuple[int, datetime, datetime]] = []

    def add(start: Optional[datetime], end: Optional[datetime], weight: int) -> None:
        if weight <= 0:
            return
        start, end = _sane_event_range(start, end)
        if start:
            candidates.append((weight, start, end or start))

    # Exponet: two lines "23.09 / 25.09.2026", including month/year boundaries.
    for match in re.finditer(
        r"(?<![\d.])(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?"
        r"(?:\s*[—–-]\s*|\s+)(\d{1,2})[./](\d{1,2})[./](\d{4})", text
    ):
        d1, m1, y1, d2, m2, y2 = match.groups()
        first_year = int(y1 or y2) - (1 if not y1 and int(m1) > int(m2) else 0)
        add(parse_date(f"{d1}.{m1}.{first_year}"), parse_date(f"{d2}.{m2}.{y2}"), 7)

    for match in re.finditer(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", text):
        day = parse_date(match.group(1))
        add(day, day, 2)

    for match in re.finditer(
        rf"(\d{{1,2}})\s+({months})\s+(\d{{4}})\s*[—\-–]\s*(\d{{1,2}})\s+({months})\s+(\d{{4}})",
        text,
        re.I,
    ):
        d1, m1, y1, d2, m2, y2 = match.groups()
        add(
            parse_date(f"{d1} {m1} {y1}"),
            parse_date(f"{d2} {m2} {y2}"),
            7,
        )
    for match in re.finditer(
        rf"(\d{{1,2}})\s+({months})\s*[—\-–]\s*(\d{{1,2}})\s+({months})(?:\s+(\d{{4}}))?",
        text,
        re.I,
    ):
        d1, m1, d2, m2, year = match.groups()
        year = year or year_now
        add(parse_date(f"{d1} {m1} {year}"), parse_date(f"{d2} {m2} {year}"), 6)
    for match in re.finditer(
        r"(\d{1,2}[./]\d{1,2}[./]\d{4})\s*[—\-–]\s*(\d{1,2}[./]\d{1,2}[./]\d{4})",
        text,
    ):
        add(parse_date(match.group(1)), parse_date(match.group(2)), _date_context_weight(text, match.start(), match.end()))
    for match in re.finditer(
        rf"(\d{{1,2}})\s*[—\-–]\s*(\d{{1,2}})\s+({months})(?:\s+(\d{{4}}))?",
        text,
        re.I,
    ):
        d1, d2, mon, year = match.groups()
        year = year or year_now
        add(
            parse_date(f"{d1} {mon} {year}"),
            parse_date(f"{d2} {mon} {year}"),
            max(3, _date_context_weight(text, match.start(), match.end())),
        )
    for match in re.finditer(
        rf"с\s+(\d{{1,2}})\s+по\s+(\d{{1,2}})\s+({months})(?:\s+(\d{{4}}))?",
        text,
        re.I,
    ):
        d1, d2, mon, year = match.groups()
        year = year or year_now
        add(
            parse_date(f"{d1} {mon} {year}"),
            parse_date(f"{d2} {mon} {year}"),
            max(4, _date_context_weight(text, match.start(), match.end())),
        )
    for match in re.finditer(
        rf"(?:состоится|пройдет|пройдёт|проходит|церемония[^.!?]{{0,40}}состоится)\s+(\d{{1,2}}\s+(?:{months})(?:\s+\d{{4}})?)",
        text,
        re.I,
    ):
        raw = match.group(1)
        if not re.search(r"\d{4}", raw):
            raw = f"{raw} {year_now}"
        dt = parse_date(raw)
        add(dt, dt, 6)
    for match in re.finditer(rf"(\d{{1,2}}\s+(?:{months}),?\s+\d{{4}})", text, re.I):
        dt = parse_date(match.group(1).replace(",", " "))
        add(dt, dt, _date_context_weight(text, match.start(), match.end()))
    if not candidates:
        for match in re.finditer(rf"(\d{{1,2}}\s+(?:{months}))(?!,?\s+\d{{4}})", text, re.I):
            dt = parse_date(f"{match.group(1)} {year_now}")
            add(dt, dt, _date_context_weight(text, match.start(), match.end()))
    if not candidates:
        for match in re.finditer(r"(\d{1,2}[./]\d{1,2}[./]\d{4})", text):
            dt = parse_date(match.group(1))
            add(dt, dt, _date_context_weight(text, match.start(), match.end()))
    if not candidates:
        return None, None
    # «1 января» без года календари ставят заглушкой, когда даты ещё не объявлены
    if not re.search(r"20\d{2}", text) and all(
        row[1].month == 1 and row[1].day == 1 for row in candidates
    ):
        return None, None
    _weight, start, end = max(
        candidates,
        key=lambda row: (
            row[0],
            2025 <= row[1].year <= 2028,
            min((row[2] - row[1]).days, MAX_EVENT_DAYS),
        ),
    )
    return start, end


def guess_event_type_from_text(text: str) -> str:
    t = (text or "").lower()
    mapping = (
        ("круглый стол", "круглый стол"),
        ("бизнес-завтрак", "бизнес-завтрак"),
        ("бизнес завтрак", "бизнес-завтрак"),
        ("день поля", "день поля"),
        ("дня поля", "день поля"),
        ("вебинар", "вебинар"),
        ("семинар", "семинар"),
        ("конгресс", "форум"),
        ("саммит", "форум"),
        ("форум", "форум"),
        ("конференц", "конференция"),
        ("выставк", "выставка"),
        ("экспо", "выставка"),
        ("премия", "другое"),
        ("практикум", "конференция"),
        ("хакатон", "конференция"),
        ("митап", "конференция"),
    )
    for needle, label in mapping:
        if needle in t:
            return label
    return ""


def event_is_over(
    start: Optional[datetime],
    end: Optional[datetime],
    *,
    today: Optional[datetime] = None,
) -> bool:
    """Мероприятие уже прошло. Без даты не считаем прошедшим: она может быть впереди."""
    last_day = end or start
    if not last_day:
        return False
    now = today or datetime.utcnow()
    return last_day.date() < now.date()


def guess_location(text: str) -> str:
    blob = (text or "").lower()
    best_pos = -1
    best_city = ""
    for city in CITY_HINTS:
        pos = blob.find(city.lower())
        if pos < 0:
            continue
        if best_pos < 0 or pos < best_pos:
            best_pos = pos
            best_city = city
    if not best_city:
        return ""
    return "Санкт-Петербург" if best_city == "Петербург" else best_city


def split_title_meta(title: str) -> tuple[str, str]:
    """Из «16-18 сентября - Kazan Digital Week (Казань)» делает название и город."""
    text = strip_leading_event_dates(title)
    text = re.sub(r'^(?:пройдет|пройдёт|состоится)\s+', '', text, flags=re.I)
    quoted = re.search(r"[«\"“]([^»\"”]{6,90})[»\"”]", text)
    if quoted and re.search(r"пройдет|пройдёт|состоится|завершил", text, re.I):
        text = quoted.group(1)
    text = re.split(r'\s+(?:пройдет|пройдёт|состоится|открыл[аи]?\s+регистрацию)\b', text,
                    maxsplit=1, flags=re.I)[0].rstrip(' :')
    text = re.sub(
        rf"\s+\d{{1,2}}(?:\s*[—\-–]\s*\d{{1,2}})?\s+(?:{MONTH_GENITIVE})"
        r"(?:\s+\d{4})?(?:\s*г\.?)?\s*$",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\s+(?:специализированная|международная)?\s*(?:промышленная\s+)?выставка\s*$",
        "",
        text,
        flags=re.I,
    )
    place = ""
    city_tail = re.search(r"\(г\.\s*([^()]{2,40})\)", text, re.I)
    if city_tail:
        city = guess_location(city_tail.group(1)) or clean_text(city_tail.group(1))
        if city:
            place = city
            text = (text[: city_tail.start()] + text[city_tail.end() :]).strip(" ,;:-–—")
    match = re.search(r"\(([^()]{2,60})\)\s*$", text)
    if match:
        city = guess_location(match.group(1))
        if city:
            place = city
            text = text[: match.start()].strip(" ,;:-–—")
    text = clean_text(text)
    return text or clean_text(title), place


def strip_dates_from_place(value: str) -> str:
    """Убирает даты из строки вида «16.10.2026 | Москва»."""
    text = re.sub(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}", " ", value)
    text = re.sub(
        rf"\d{{1,2}}(?:\s*[—\-–]\s*\d{{1,2}})?\s+(?:{MONTH_GENITIVE})\.?(?:\s+\d{{4}})?",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"[|·•]+", " ", text)
    return clean_text(text).strip(" ,;:-–—")


def clean_location(value: str, fallback: str = "") -> str:
    text = clean_text(value)
    low = text.lower()
    if not text or low in SKIP_TITLES or "заказ экспоместа" in low:
        return guess_location(fallback) or ""
    stripped = strip_dates_from_place(text)
    if stripped != text:
        return stripped or guess_location(fallback) or ""
    return text


def _looks_like_article_url(link: str, base_url: str, *, kind: str = "news") -> bool:
    parsed = urlparse(link)
    path = parsed.path.lower().rstrip("/")
    base_path = urlparse(base_url).path.lower().rstrip("/")
    query = parsed.query.lower()
    host = (parsed.netloc or "").lower()
    base_host = (urlparse(base_url).netloc or "").lower()
    if path.endswith(".ics"):
        return False
    if re.search(r"(?:^|&)(?:id|event_id|e)=\d+", query):
        return True
    skip_exact = {"/login", "/search", "/catalog", "/auth"}
    skip_bits = (
        "/tag/",
        "/tags/",
        "/topic/",
        "/geo/",
        "/docs/",
        "/ministry/faq",
        "/podelites",
        "/accounts/",
        "/headings/",
        "/goods-and-prices/",
        "/order-advertisement",
    )
    if path in skip_exact:
        return False
    if any(bit in path for bit in skip_bits):
        return False
    if kind == "event":
        if host and base_host and host != base_host:
            return True
        if any(hint in path for hint in EVENT_URL_HINTS):
            parts = [p for p in path.split("/") if p]
            if path.endswith("/partners") or path.endswith("/signup"):
                return False
            return len(parts) >= 2 or bool(parsed.fragment)
        parts = [p for p in path.split("/") if p]
        if not path or path == base_path:
            return False
        return len(parts) >= 2
    if not path or path == base_path:
        return False
    hints = ("/news", "/press", "/novosti", "/article", "/rss")
    parts = [p for p in path.split("/") if p]
    if any(h in path for h in hints):
        return len(parts) >= 2
    return len(parts) >= 2


def _append_event(
    items: list[NewsItem],
    seen_urls: set[str],
    *,
    source: dict[str, Any],
    title: str,
    url: str,
    blob: str = "",
    summary: str = "",
    location: str = "",
    event_type: str = "",
    published: Optional[datetime] = None,
    date_end: Optional[datetime] = None,
) -> None:
    title = clean_text(title)
    url = clean_text(url)
    if not title or not url or url in seen_urls:
        return
    if title.lower() in SKIP_TITLES or not is_eventish_title(title, blob):
        return
    if source.get("require_topic_guess") and not guess_topic_from_text(f"{title} {blob} {summary}"):
        return
    raw_title = title
    title, place_from_title = split_title_meta(title)
    blob_dates = blob or f"{raw_title} {summary}"
    start, end = extract_event_dates(blob_dates)
    if start and (not published or not date_is_grounded(published, blob_dates)):
        published = start
    elif published and not date_is_grounded(published, blob_dates):
        published = None
        date_end = None
    if not date_end:
        date_end = end or start
    if not location:
        location = place_from_title or guess_location(raw_title) or guess_location(blob or summary)
    if not event_type:
        event_type = guess_event_type_from_text(f"{title} {blob}")
    seen_urls.add(url)
    items.append(
        make_item(
            source=source,
            title=title,
            url=url,
            published_at=published,
            date_end=date_end,
            summary=summary,
            location=location,
            event_type=event_type,
            date_evidence=blob_dates,
        )
    )


def parse_event_tables(
    source: dict[str, Any], soup: BeautifulSoup, seen_urls: set[str]
) -> list[NewsItem]:
    """Таблицы календарей вроде Ценовика: дата / название / место / сайт."""
    items: list[NewsItem] = []
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 3:
                continue
            date_text = clean_text(cells[0].get_text(" ", strip=True))
            title = clean_text(cells[1].get_text(" ", strip=True))
            place = clean_text(cells[2].get_text(" ", strip=True)) if len(cells) > 2 else ""
            contact = cells[3] if len(cells) > 3 else cells[1]
            if date_text.lower() in WEEKDAYS or title.lower() in WEEKDAYS:
                continue
            if not re.search(r"\d", date_text):
                continue
            if len(title) > 140:
                title = title[:140].rsplit(" ", 1)[0]
            if not is_eventish_title(title, f"{date_text} {place}"):
                continue
            link = href_from_node(contact, source["url"]) or href_from_node(
                cells[1], source["url"]
            )
            if not link:
                slug = re.sub(r"[^a-z0-9а-яё]+", "-", title.lower()).strip("-")[:48]
                link = f"{source['url']}#{slug}"
            # An explicitly dated edition must not be rolled into the current year.
            edition = re.search(r"\b(20\d{2})\b", title)
            year = edition.group(1) if edition else str(default_event_year())
            blob = f"{date_text} {year} {title} {place}"
            _append_event(
                items,
                seen_urls,
                source=source,
                title=title,
                url=link,
                blob=blob,
                summary=place,
                location=guess_location(place) or place[:80],
            )
    return items


def parse_exponet(source: dict[str, Any], soup: BeautifulSoup) -> list[NewsItem]:
    """Exponet: dates / title + city + description / invitation link."""
    items: list[NewsItem] = []
    seen: set[str] = set()
    for row in soup.select("table tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 3:
            continue
        date_text = clean_text(cells[0].get_text(" ", strip=True))
        start, end = extract_event_dates(date_text)
        if not start:
            continue
        heading = cells[1].select_one("a b, b, strong")
        if heading is None:
            continue
        title = clean_text(heading.get_text(" ", strip=True))
        context = clean_text(cells[1].get_text(" ", strip=True))
        city = re.search(r"\(г\.\s*([^()]+)\)", context)
        location = clean_text(city.group(1)) if city else guess_location(context)
        link = cells[1].select_one("a[href*='/exhibitions/by-id/']")
        url = normalize_href(link['href'], source['url']) if link else fragment_url(
            "https://www.exponet.ru/exhibitions/", title)
        summary = context[len(title):].strip()
        summary = re.sub(r"^\(г\.[^()]+\)\s*", "", summary)
        _append_event(items, seen, source=source, title=title, url=url,
                      blob=f"{date_text} {context}", summary=summary,
                      location=location, published=start, date_end=end)
    return items


def parse_ferama(source: dict[str, Any], soup: BeautifulSoup) -> list[NewsItem]:
    items: list[NewsItem] = []
    seen: set[str] = set()
    for node in soup.select('article li'):
        blob = clean_text(node.get_text(' ', strip=True))
        match = re.search(r'(?:состоится|пройдет|пройдёт)\s+(.+?)(?:\s+[—–]\s+|\.\s+Адрес|$)', blob, re.I)
        if not match:
            continue
        title = _first_text(node, 'strong') or match.group(1)
        link = href_from_node(node, source['url']) or fragment_url(source['url'], title)
        _append_event(items, seen, source=source, title=title, url=link,
                      blob=blob, summary=blob, location=guess_location(blob))
    return items


def parse_ict_moscow(source: dict[str, Any], soup: BeautifulSoup) -> list[NewsItem]:
    """The date belongs to the day column, not to the event's title link."""
    by_url: dict[str, NewsItem] = {}
    for day in soup.select('.dayItemsWrapper'):
        header = day.find(recursive=False)
        date_text = clean_text(header.get_text(' ', strip=True)) if header else ''
        start, _ = extract_event_dates(date_text)
        if not start:
            continue
        for block in day.find_all(recursive=False)[1:]:
            links = [a for a in block.select("a[href^='/event/']") if not a.find('address')]
            if not links:
                continue
            link = links[0]
            title = clean_text(link.get_text(' ', strip=True))
            url = normalize_href(link.get('href', ''), source['url'])
            if url in by_url:
                item = by_url[url]
                if (start - item.published_at).days <= MAX_EVENT_DAYS:
                    item.date_end = max(item.date_end or item.published_at, start)
                    item.date_evidence += ' ' + date_text
                continue
            blob = clean_text(block.get_text(' ', strip=True))
            kind = _first_text(block, 'b')
            place = _first_text(block, 'address')
            location = 'Онлайн' if 'онлайн' in kind.lower() else (guess_location(place) or place)
            items: list[NewsItem] = []
            _append_event(items, set(), source=source, title=title, url=url,
                          blob=f'{date_text} {blob}', published=start, date_end=start,
                          location=location, event_type=guess_event_type_from_text(kind))
            if items:
                by_url[url] = items[0]
    return list(by_url.values())


def _conferos_href(node, base_url: str) -> str:
    ranked: list[tuple[int, str]] = []
    for a in node.select("a[href]"):
        href = normalize_href(str(a.get("href") or ""), base_url)
        if not href:
            continue
        low = href.lower()
        if "old.conferos" in low or "#registration" in low or "#report" in low:
            continue
        text = clean_text(a.get_text(" ", strip=True)).lower()
        if text in SKIP_TITLES:
            ranked.append((2, href))
            continue
        if "/event/" in low or "tadviser" in low:
            ranked.append((0 if text and text != "to conference" else 1, href))
        elif text and len(text) >= 6:
            ranked.append((3, href))
    ranked.sort(key=lambda row: row[0])
    return ranked[0][1] if ranked else ""


def parse_conferos(source: dict[str, Any], soup: BeautifulSoup) -> list[NewsItem]:
    """Главная, архив и баннер Conferos: название из заголовка карточки, дата из ячейки."""
    items: list[NewsItem] = []
    seen: set[str] = set()
    for node in soup.select("li.events-card, li.archive-card, .header__index-content"):
        title = _first_text(
            node,
            "h3.events-card__title a, h3.archive-card__title a, h2.content__title, "
            "h3.events-card__title, h3.archive-card__title, .content__title",
        )
        if not title or title.lower() in SKIP_TITLES:
            continue
        if "архивный сайт" in title.lower():
            continue
        date_text = _first_text(
            node, ".events-card__date, .archive-card__date, .header-banner__date"
        )
        kind_text = _first_text(node, ".events-card__name, .archive-card__name")
        url = _conferos_href(node, source["url"])
        if not url:
            continue
        summary = _first_text(node, ".content__description")
        blob = f"{date_text} {title} {kind_text} {summary}"
        location = "Онлайн" if "онлайн" in kind_text.lower() else ""
        _append_event(
            items,
            seen,
            source=source,
            title=title,
            url=url,
            blob=blob,
            summary=summary or kind_text,
            location=location,
            event_type=kind_text.lower(),
        )
    return items


def parse_exhibition_hero(source: dict[str, Any], soup: BeautifulSoup) -> list[NewsItem]:
    title = _first_text(soup, '.brand-name')
    date_text = _first_text(soup, '.brand-date')
    if not title or not date_text:
        return []
    start, end = extract_event_dates(date_text)
    if not start:
        return []
    place = _first_text(soup, '.brand-venue a, .brand-venue')
    summary = _first_text(soup, '.brand-name-full')
    items: list[NewsItem] = []
    _append_event(items, set(), source=source, title=title, url=source['url'],
                  blob=f'{date_text} {title} {summary}', summary=summary, location=place,
                  published=start, date_end=end, event_type='выставка')
    return items


def parse_listicle_events(
    source: dict[str, Any], soup: BeautifulSoup, seen_urls: set[str]
) -> list[NewsItem]:
    """Статьи-списки: «с 21 по 23 января состоится AGRAVIA 2026»."""
    items: list[NewsItem] = []
    article = soup.find("article") or soup.find("main") or soup
    text = article.get_text(" ", strip=True)
    pattern = re.compile(
        rf"с\s+(\d{{1,2}})\s+по\s+(\d{{1,2}})\s+({MONTH_GENITIVE})\s+"
        rf"(?:состоится|пройдет|пройдёт)\s+(.{8,180}?)(?=(?:с\s+\d{{1,2}}\s+по)|\.|$)",
        re.I,
    )
    for match in pattern.finditer(text):
        d1, d2, mon, raw_title = match.groups()
        title = clean_text(raw_title).strip(" —–-")
        title = re.split(r"\s[—–-]\s", title, maxsplit=1)[0].strip()
        if not is_eventish_title(title, raw_title):
            continue
        year = str(default_event_year())
        blob = f"{d1}–{d2} {mon} {year} {title} {raw_title}"
        slug = re.sub(r"[^a-z0-9а-яё]+", "-", title.lower()).strip("-")[:48]
        _append_event(
            items,
            seen_urls,
            source=source,
            title=title,
            url=f"{source['url']}#{slug}",
            blob=blob,
            summary=clean_text(raw_title)[:400],
        )
    return items


def parse_html(source: dict[str, Any], content: str) -> list[NewsItem]:
    soup = BeautifulSoup(content, "lxml")
    from source_adapters import parse_special
    special = parse_special(source, soup)
    if special is not None:
        return special
    if source['id'].startswith('exponet_'):
        return parse_exponet(source, soup)
    if source['id'] == 'ferama_calendar':
        return parse_ferama(source, soup)
    if source['id'] == 'ict_moscow':
        return parse_ict_moscow(source, soup)
    if source['id'] == 'conferos':
        return parse_conferos(source, soup)
    if source['id'] in {'metobr_expo', 'neftegaz_expo', 'elektro_expo', 'sviaz_expo'}:
        return parse_exhibition_hero(source, soup)
    selectors = source.get("selectors") or {}
    item_sel = selectors.get("item") or "article"
    kind = source_kind(source)
    items: list[NewsItem] = []
    seen_urls: set[str] = set()

    nodes = soup.select(item_sel)
    if source.get("fallback_links", True) and (
        not nodes or (kind == "event" and len(nodes) < 4)
    ):
        extra = soup.select(
            "article, .news-item, .news-card, .news-row, .list-item, "
            ".press-list__item, .newsList__item, .views-row, "
            ".upcoming-event, .event-card, .bizon_api_event_item, "
            ".event-cards_item, .item__event, .item, .zevent-list-inner, "
            ".ban-anons, .expo-item, h4.evnt-title, .evnt-title"
        )
        seen_nodes = {id(n) for n in nodes}
        for node in extra:
            if id(node) not in seen_nodes:
                nodes.append(node)
                seen_nodes.add(id(node))

    if kind == "event":
        items.extend(parse_event_tables(source, soup, seen_urls))
        items.extend(parse_jsonld_events(source, soup, seen_urls))
        items.extend(parse_dated_blocks(source, soup, seen_urls))

    for node in nodes:
        title = _first_text(
            node,
            selectors.get(
                "title",
                ".event-title, .item__title, .event-cards_heading, "
                "h1, a, h2, h3, h4, .title, .news-title",
            ),
        )
        if not title:
            title = clean_text(node.get_text(" ", strip=True))
        if kind == "event" and title and len(title) > 140:
            shorter = _first_text(node, "a, h2, h3, h4, .event-title, .title, .title-v2")
            if shorter and len(shorter) < len(title):
                title = shorter
        if kind == "event" and title and len(title) > 180:
            continue
        if title.lower() in SKIP_TITLES or len(title) < 4:
            title = _first_text(node, "h2, h3, h4, .event-title, .title")
        raw_title = title
        place_from_title = ""
        if kind == "event" and title:
            title, place_from_title = split_title_meta(strip_leading_event_dates(title))
        link = _node_href(node, selectors.get("link", "a"), source["url"])
        if not title or not link:
            if kind == "event" and title:
                blob_try = clean_text(node.get_text(" ", strip=True))
                start, _ = extract_event_dates(blob_try or title)
                if start and is_eventish_title(title, blob_try):
                    link = fragment_url(source["url"], title)
        if not title or not link:
            continue
        if title.lower() in SKIP_TITLES:
            continue
        blob_try = clean_text(node.get_text(" ", strip=True)) if kind == "event" else title
        summary = _first_text(node, selectors.get("summary", "p, .announce, .lead, .newsList__preview"))
        if kind == "event" and source.get("require_topic_guess") and not guess_topic_from_text(
            f"{title} {summary}"
        ):
            continue
        if kind == "event" and not is_eventish_title(title, title):
            blob_try = clean_text(node.get_text(" ", strip=True))
            if not is_eventish_title(title, blob_try):
                continue
        if not _looks_like_article_url(link, source["url"], kind=kind):
            if not (kind == "event" and is_eventish_title(title, blob_try)):
                continue
        if link in seen_urls:
            continue
        seen_urls.add(link)
        summary = _first_text(
            node, selectors.get("summary", "p, .announce, .lead, .newsList__preview")
        )
        published = _first_datetime(
            node, selectors.get("date", "time, .date, .news-date, .item__event-date, .event-cards_date")
        )
        date_end = None
        location = ""
        event_type = ""
        if kind == "event":
            blob = clean_text(node.get_text(" ", strip=True))
            date_text = _first_text(node, selectors.get("date", "time, .date"))
            edition = re.search(r'\b(20\d{2})\s*$', title)
            if date_text and not re.search(r'\b20\d{2}\b', date_text) and edition:
                date_text = f'{date_text} {edition.group(1)}'
            # The date cell takes precedence over publication/deadline dates.
            start, end = extract_event_dates(date_text)
            if not start:
                start, end = extract_event_dates(blob)
            if start:
                published = start
            elif published and not date_is_grounded(published, blob):
                published = None
            date_end = end or start or published
            location = (
                clean_location(
                    _first_text(
                        node,
                        selectors.get("location", ".place, .location, .events_profile_tab_adress"),
                    ),
                    f"{title} {blob}",
                )
                or place_from_title
                or guess_location(raw_title)
                or guess_location(blob)
            )
            if source['id'] == 'crocus_expo':
                location = source.get('default_location', '')
            event_type = clean_text(
                _first_text(
                    node,
                    selectors.get(
                        "event_type", ".type, .events_profile_tab_cat"
                    ),
                )
            ).lower()
            if event_type in {"событие", "мероприятие"}:
                event_type = ""
            if not event_type:
                event_type = guess_event_type_from_text(f"{title} {blob}")
        items.append(
            make_item(
                source=source,
                title=title,
                url=link,
                published_at=published,
                date_end=date_end,
                summary=summary,
                location=location,
                event_type=event_type,
                date_evidence=(date_text or blob) if kind == 'event' else '',
            )
        )

    if kind == "event":
        items.extend(parse_listicle_events(source, soup, seen_urls))

    # fallback: прямые ссылки, если селекторы поймали мало или мусор меню
    allow_fallback = source.get("fallback_links", True)
    if allow_fallback and ((kind == "event" and len(items) < 12) or not items):
        min_len = 8 if kind == "event" else 35
        for a in soup.find_all("a", href=True):
            title = clean_text(a.get_text(" ", strip=True))
            link = normalize_href(a["href"], source["url"])
            if not link or len(title) < min_len or title.lower() in SKIP_TITLES:
                continue
            if kind == "event" and not is_eventish_title(title):
                continue
            if not _looks_like_article_url(link, source["url"], kind=kind):
                continue
            if link in seen_urls:
                continue
            seen_urls.add(link)
            parent = a.parent
            blob = clean_text((parent or a).get_text(" ", strip=True)) if parent else title
            published = _first_datetime(parent, "time, .date, .news-date") if parent else None
            date_end = None
            location = ""
            event_type = ""
            if kind == "event":
                start, end = extract_event_dates(blob)
                if start and (not published or not date_is_grounded(published, blob)):
                    published = start
                elif published and not date_is_grounded(published, blob):
                    published = None
                date_end = end or start
                location = guess_location(blob)
                event_type = guess_event_type_from_text(f"{title} {blob}")
            items.append(
                make_item(
                    source=source,
                    title=title,
                    url=link,
                    published_at=published,
                    date_end=date_end,
                    location=location,
                    event_type=event_type,
                    date_evidence=blob if kind == 'event' else '',
                )
            )
            if len(items) >= 80:
                break
    if kind == "event" and not items:
        page_title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        blob = clean_text(soup.get_text(" ", strip=True))[:1800]
        start, end = extract_event_dates(blob)
        if page_title and start and is_eventish_title(page_title, blob):
            _append_event(
                items,
                seen_urls,
                source=source,
                title=page_title,
                url=source["url"],
                blob=blob,
                summary=blob[:400],
                location=guess_location(blob),
                published=start,
                date_end=end,
            )
    return items


async def parse_source(client: httpx.AsyncClient, source: dict[str, Any]) -> tuple[list[NewsItem], ParseResult]:
    result = ParseResult(source_id=source["id"], source_name=source["name"])
    try:
        source_type = source.get("type", "rss")
        if source_type == "seed":
            items = parse_seed_events(source)
            result.fetched = len(items)
            return items, result

        urls = [source["url"], *[u for u in (source.get("extra_urls") or []) if u]]
        if source['id']=='crocus_expo':
            from catalog_core import today as local_today
            now=local_today()
            for offset in range(12):
                absolute=now.year*12+now.month-1+offset
                year,month=divmod(absolute,12)
                urls.append(source['url']+f'?month={month+1:02d}&year={year}')
        items: list[NewsItem] = []
        errors: list[str] = []
        seen: set[str] = set()
        verify = bool(source.get("verify_ssl", True))

        async def fetch_one(url: str) -> tuple[str, Any]:
            for attempt in range(2):
                try:
                    if source.get('transport') == 'curl':
                        from source_transport import curl_text
                        content = await curl_text(client, url)
                    else:
                        content = await fetch_text(client, url, verify=verify)
                    title = re.search(r'<title[^>]*>(.*?)</title>',content,re.I|re.S)
                    if title and re.search(r'^\s*(?:проверка браузера|just a moment|access denied|robot verification)',title.group(1),re.I):
                        raise RuntimeError('source_browser_verification_required')
                    return url, content
                except httpx.HTTPStatusError as exc:
                    if attempt == 0 and exc.response.status_code in {429, 500, 502, 503, 504}:
                        delay = exc.response.headers.get('Retry-After', '1')
                        await asyncio.sleep(min(5, max(1, int(delay) if delay.isdigit() else 1)))
                        continue
                    return url, exc
                except httpx.TransportError as exc:
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    return url, exc
                except Exception as exc:
                    return url, exc

        pages = await asyncio.gather(*[fetch_one(url) for url in urls])
        from source_discovery import page_links, detail_links
        if source.get('pagination') or source.get('discovery_path_pattern'):
            visited=set(urls);max_pages=int(source.get('max_pages',200));cursor=0
            result.coverage='complete_visible_pagination'
            while cursor<len(pages):
                pending=[]
                batch=pages[cursor:];cursor=len(pages)
                for base,html in batch:
                    if isinstance(html,Exception):continue
                    doc=BeautifulSoup(html,'lxml')
                    for href in page_links(doc,base,source):
                        if href in visited:continue
                        if len(visited)>=max_pages:
                            result.coverage='partial_page_limit';continue
                        visited.add(href);pending.append(href)
                if pending:pages.extend(await asyncio.gather(*(fetch_one(u) for u in pending)))
        if source.get('detail_link_selector'):
            candidates=[];visited={u for u,_ in pages}
            for base,html in pages:
                if isinstance(html,Exception):continue
                for href in detail_links(BeautifulSoup(html,'lxml'),base,source):
                    if href not in visited:visited.add(href);candidates.append(href)
            cap=int(source.get('max_detail_pages',300))
            if len(candidates)>cap:result.coverage='partial_detail_limit'
            pages.extend(await asyncio.gather(*(fetch_one(u) for u in candidates[:cap])))
        result.pages_fetched=sum(not isinstance(content,Exception) for _,content in pages)
        result.pages_failed=len(pages)-result.pages_fetched
        if result.pages_failed:result.coverage='partial_fetch_error'
        for url, content in pages:
            if isinstance(content, Exception):
                errors.append(f"{url}: {type(content).__name__}: {content}")
                continue
            page = {**source, "url": url}
            if source_type == "rss":
                chunk = parse_rss(page, content)
                if not chunk and ("<html" in content.lower() or "<!doctype" in content.lower()):
                    chunk = parse_html({**page, "type": "html"}, content)
            else:
                chunk = parse_html(page, content)
            for item in chunk:
                if item.id in seen:
                    continue
                seen.add(item.id)
                items.append(item)
        result.fetched = len(items)
        if errors:
            result.error = errors[0][:500]
        return items, result
    except Exception as exc:  # noqa: BLE001
        result.error = f'{type(exc).__name__}: {exc}'
        return [], result


SOURCE_FIRST = (
    "ict2go",
    "cnews_conferences",
    "conferos",
    "comnews_conferences",
    "digital_economy_calendar",
    "ict_moscow",
    "seed_events_it",
    "globalcio_events",
    "exponet_industry",
    "exponet_all",
    "crocus_expo",
    "seed_events_industry",
    "expoforum_spb",
    "metobr_expo",
    "neftegaz_expo",
    "elektro_expo",
    "sviaz_expo",
)


async def run_parse(
    source_ids: Optional[list[str]] = None,
    kind: Optional[str] = None,
) -> tuple[list[NewsItem], list[ParseResult]]:
    sources = load_sources(kind=kind)
    if source_ids:
        wanted = set(source_ids)
        sources = [s for s in sources if s["id"] in wanted]
    rank = {source_id: index for index, source_id in enumerate(SOURCE_FIRST)}
    sources.sort(key=lambda row: rank.get(row["id"], 50 + (0 if row.get("topic") in {"it", "industry"} else 20)))

    timeout = httpx.Timeout(22.0, connect=8.0)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    sem = asyncio.Semaphore(6)
    all_items: list[NewsItem] = []
    results: list[ParseResult] = []

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        async def one(source: dict[str, Any]) -> tuple[list[NewsItem], ParseResult]:
            async with sem:
                result = await parse_source(client, source)
                import os
                if os.environ.get('PARSER_PROGRESS') == '1':
                    print(json.dumps({'source':source['id'],'fetched':result[1].fetched,'pages':result[1].pages_fetched,'error':result[1].error,'coverage':result[1].coverage},ensure_ascii=False),flush=True)
                return result

        gathered = await asyncio.gather(*[one(source) for source in sources], return_exceptions=True)

    for source, row in zip(sources, gathered):
        if isinstance(row, Exception):
            results.append(ParseResult(source_id=source["id"], source_name=source["name"], error=str(row)))
            continue
        items, result = row
        all_items.extend(items)
        results.append(result)

    unique: dict[str, NewsItem] = {}
    by_source = {r.source_id: r for r in results}
    for item in all_items:
        result = by_source[item.source_id]
        if item.kind == "event" and event_is_over(item.published_at, item.date_end):
            result.past += 1
            continue
        if item.kind == 'event' and not item.source_id.startswith('seed_events'):
            # An undated article cannot establish that an event is still upcoming.
            if not item.published_at:
                result.undated += 1
                continue
            edition = re.search(r"\b(20\d{2})\s*$", item.title)
            if edition:
                title_year = int(edition.group(1))
                date_years = {
                    item.published_at.year,
                    (item.date_end or item.published_at).year,
                }
                # «Day 2026» в марте 2027 на Conferos — это та же конференция, не конфликт.
                if all(abs(title_year - year) > 1 for year in date_years):
                    result.conflicts += 1
                    continue
        result.accepted += 1
        unique[item.id] = item
    return list(unique.values()), results
