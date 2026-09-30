#!/usr/bin/env python3
"""Ingest All-Events.ru type calendars + edu-afisha + ict2go + kudabiz + series.

P0 fixes vs prior thin ingest:
- AE «Предстоящие» section only for evidence; date filter via make_event
- Real pagination: ?PAGEN_1=N&load_more=SECTION_ID (NOT PAGEN_2= which 404s)
- Also parse visible HTML flex cards (event_name_new + DD.MM.YYYY)
- Exact UI types from URL filter — never collapse to Выставка
- edu-afisha.ru category pages; ict2go keyword retag; kudabiz live-search?cat=N
- Optional series HTML (event4etverg, bizzavtrak); Playwright only if SKIP_PW unset
  and plain fetch thin — document walls in samples/
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_round2 import (  # noqa: E402
    TODAY, ROOT, SAMPLES, make_event, parse_dot_date, strip_tags,
)
from ingest_round3 import (  # noqa: E402
    parse_all_events, dedupe_key, soft_key, fuzzy_title,
)
from enrich_summaries import write_catalog  # noqa: E402

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TYPE_SLUGS: list[tuple[str, str]] = [
    ("biznes-zavtrak", "Бизнес-завтрак"),
    ("biznes-uzhin", "Бизнес-ужин"),
    ("krugliy-stol", "Круглый стол"),
    ("seminar", "Семинар"),
    ("vebinar", "Вебинар"),
    ("meet-up", "Митап"),
    ("master-class", "Мастер-класс"),
    ("mastermind", "Мастермайнд"),
]

ALT_SLUGS: dict[str, str] = {
    "vebinar": "webinar",
}

CITIES = [
    "moskva", "sankt_peterburg", "ekaterinburg", "novosibirsk",
    "kazan", "krasnodar", "nizhniy_novgorod", "samara", "rostov-na-donu",
]

CIS_COUNTRIES = {
    "россия", "беларусь", "белоруссия", "казахстан", "узбекистан",
    "армения", "азербайджан", "кыргызстан", "киргизия", "таджикистан",
    "молдова", "молдавия", "грузия",
}

TARGET_TYPES = {
    "Бизнес-завтрак", "Бизнес-ужин", "Круглый стол",
    "Семинар", "Вебинар", "Митап", "Мастер-класс", "Мастермайнд",
}

TYPE_NORMALIZE = {
    "Бизнес-завтраки": "Бизнес-завтрак",
    "Бизнес-ужин": "Бизнес-ужин",
    "Бизнес-ужины": "Бизнес-ужин",
    "Круглые столы": "Круглый стол",
    "Семинары": "Семинар",
    "Семинары и тренинги": "Семинар",
    "Вебинары": "Вебинар",
    "Митапы": "Митап",
    "Meetup": "Митап",
    "meetup": "Митап",
    "Мастер-классы": "Мастер-класс",
    "Мастермайнды": "Мастермайнд",
}

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

walls: list[str] = []
stats: dict[str, Any] = {}
scrape_evidence: list[str] = []


def fetch(url: str, timeout: int = 45, extra_headers: Optional[dict] = None) -> tuple[Optional[str], Optional[str]]:
    try:
        headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            charset = "utf-8"
            ct = r.headers.get("Content-Type", "")
            m = re.search(r"charset=([\w-]+)", ct, re.I)
            if m:
                charset = m.group(1)
            return raw.decode(charset, errors="replace"), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def page_is_generic(html: str) -> bool:
    m = re.search(r"<title>([^<]+)", html)
    if not m:
        return True
    t = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    return t == "мероприятия" or t.startswith("мероприятия ")


def save_sample(name: str, html: str) -> Path:
    p = SAMPLES / name
    p.write_text(html, encoding="utf-8")
    return p


def prefer_cis(e: dict) -> bool:
    country = (e.get("country") or "").strip().lower()
    city = (e.get("city") or "").strip().lower()
    if city in {"онлайн", "online", ""}:
        return True
    if not country or country == "россия":
        return True
    return country in CIS_COUNTRIES


def upcoming_section_id(html: str) -> Optional[str]:
    m = re.search(
        r"Предстоящие[^<]{0,120}</h2>\s*"
        r'<div class="event_flex_main events-items" id="([a-f0-9]+)"',
        html, re.I | re.S,
    )
    if m:
        return m.group(1)
    m = re.search(r'<div class="event_flex_main events-items" id="([a-f0-9]+)"', html)
    return m.group(1) if m else None


def extract_upcoming_chunk(html: str) -> str:
    """HTML of «Предстоящие …» events-items only (exclude Прошедшие)."""
    m = re.search(
        r"Предстоящие[^<]{0,120}</h2>\s*"
        r'(<div class="event_flex_main events-items"[^>]*>.*?)'
        r'(?:<div class="load-more">\s*<a[^>]*>Показать еще</a>\s*</div>'
        r'|<h2[^>]*>\s*Прошедшие|<h2[^>]*>\s*Новости)',
        html, re.S | re.I,
    )
    if m:
        return m.group(1)
    m = re.search(
        r'(<div class="event_flex_main events-items"[^>]*>.*?)(?:<div class="load-more">|$)',
        html, re.S,
    )
    return m.group(1) if m else html


def parse_ae_flex_cards(html_or_chunk: str, etype: str, source: str = "all_events") -> list[dict]:
    """Parse visible list cards: event_name_new + desktop DD.MM.YYYY."""
    out = []
    for m in re.finditer(
        r'href="(/events/[^"]+)" class="event_name_new">([^<]+)</a>\s*'
        r'<div class="event-date">.*?<span class="desktop">(\d{2}\.\d{2}\.\d{4})',
        html_or_chunk, re.S,
    ):
        href = m.group(1).split("?")[0]
        title = unescape(m.group(2)).strip()
        d = parse_dot_date(m.group(3))
        if not d:
            continue
        url = "https://all-events.ru" + href
        city = ""
        nearby = html_or_chunk[m.start(): m.start() + 6000]
        cm = re.search(
            r'class="event_info_new_text[^"]*svg_(?:offline|online|hybrid)[^"]*"[^>]*>.*?<span>([^<]+)</span>',
            nearby, re.S,
        )
        if not cm:
            cm = re.search(r"<span>([^<]*(?:Москва|Санкт-Петербург|Казань|Новосибирск|Екатеринбург|Краснодар|Онлайн|онлайн)[^<]*)</span>", nearby)
        if cm:
            city = cm.group(1).split(",")[0].strip()
            if city.lower() in {"онлайн-трансляция", "не определено", "онлайн"}:
                city = "Онлайн"
        ev = make_event(title, d, d, url, source, city=city, etype=etype, extra_vert=title)
        if ev:
            out.append(ev)
    return out


def parse_ae_with_type(html: str, etype: str, source: str = "all_events") -> list[dict]:
    """Schema.org wrappers + flex HTML cards; force type; CIS filter."""
    chunk = extract_upcoming_chunk(html)
    by_url: dict[str, dict] = {}
    for e in parse_all_events(chunk if "event-wrapper" in chunk else html, source):
        e = dict(e)
        e["type"] = etype
        e["source"] = source
        if prefer_cis(e):
            by_url[e.get("organizer_url") or e["title"]] = e
    for e in parse_ae_flex_cards(chunk, etype, source):
        if prefer_cis(e):
            by_url[e.get("organizer_url") or e["title"]] = e
    # Also accept schema from full page if upcoming chunk missed wrappers
    if not by_url:
        for e in parse_all_events(html, source):
            e = dict(e)
            e["type"] = etype
            e["source"] = source
            if prefer_cis(e):
                by_url[e.get("organizer_url") or e["title"]] = e
    return list(by_url.values())


def ict2go_type_from(title: str, themes_html: str) -> Optional[str]:
    typ_m = re.search(r'class="event-type"[^>]*>([^<]+)', themes_html)
    et_raw = (typ_m.group(1) if typ_m else "").strip()
    blob = f"{title} {et_raw}".lower().replace("ё", "е")
    if "завтрак" in blob:
        return "Бизнес-завтрак"
    if "ужин" in blob:
        return "Бизнес-ужин"
    if "кругл" in blob and "стол" in blob:
        return "Круглый стол"
    if "митап" in blob or "meetup" in blob or et_raw.lower() in {"митап", "meetup"}:
        return "Митап"
    if "вебинар" in blob or "webinar" in blob or "вебинар" in et_raw.lower():
        return "Вебинар"
    if "семинар" in blob or "семинар" in et_raw.lower():
        return "Семинар"
    if "мастер-класс" in blob or "мастер класс" in blob:
        return "Мастер-класс"
    if "мастермайнд" in blob or "mastermind" in blob:
        return "Мастермайнд"
    return None


def parse_ict2go_typed(html: str, source: str = "ict2go") -> list[dict]:
    out = []
    pat = re.compile(
        r'<div class="date-place">\s*'
        r'(\d{2}\.\d{2}\.\d{4})(?:\s*[-–—]\s*(\d{2}\.\d{2}\.\d{4}))?\s*'
        r'\|\s*(?:<a[^>]*>([^<]+)</a>|([^<\n]+))\s*</div>\s*'
        r'<a href="(/events/\d+/)" class="event-title"[^>]*>([^<]+)</a>\s*'
        r'<div class="event-themes">(.*?)</div>',
        re.S,
    )
    for m in pat.finditer(html):
        title = unescape(m.group(6)).strip()
        et = ict2go_type_from(title, m.group(7))
        if not et:
            continue
        d1 = parse_dot_date(m.group(1))
        d2 = parse_dot_date(m.group(2)) if m.group(2) else d1
        if not d1:
            continue
        if d2 is None:
            d2 = d1
        if d2 < TODAY:
            continue
        city = (m.group(3) or m.group(4) or "").strip()
        url = "https://ict2go.ru" + m.group(5)
        ev = make_event(
            title, d1, d2, url, source, city=city, etype=et,
            extra_vert=f"IT {title}",
        )
        if ev:
            ev["vertical"] = "it"
            out.append(ev)
    return out


def parse_edu_afisha(html: str, etype: str, source: str = "edu_afisha") -> list[dict]:
    out = []
    for m in re.finditer(r'<article class="hp-listing[^"]*"[^>]*>(.*?)</article>', html, re.S):
        block = m.group(1)
        tm = re.search(r'hp-listing__title[^>]*>\s*<a href="([^"]+)">([^<]+)</a>', block)
        if not tm:
            continue
        url, title = tm.group(1), unescape(tm.group(2)).strip()
        dm = re.search(
            r'hp-listing__attribute--data[^>]*>\s*Дата:\s*(\d{2}\.\d{2}\.\d{4})',
            block,
        )
        if not dm:
            continue
        d = parse_dot_date(dm.group(1))
        if not d:
            continue
        citym = re.search(r'hp-listing__attribute--gorod[^>]*>\s*Где:\s*([^<]+)', block)
        city = (citym.group(1).strip() if citym else "")
        if city.lower() in {"онлайн", "online"}:
            city = "Онлайн"
        # Prefer title keyword over category when clear
        forced = etype
        blob = title.lower().replace("ё", "е")
        if "завтрак" in blob:
            forced = "Бизнес-завтрак"
        elif "ужин" in blob:
            forced = "Бизнес-ужин"
        elif "кругл" in blob:
            forced = "Круглый стол"
        elif "митап" in blob or "meetup" in blob:
            forced = "Митап"
        elif "вебинар" in blob:
            forced = "Вебинар"
        elif "мастер-класс" in blob:
            forced = "Мастер-класс"
        ev = make_event(title, d, d, url, source, city=city, etype=forced, extra_vert=title)
        if ev:
            out.append(ev)
    return out


def parse_kudabiz_live(html: str, default_etype: str, source: str = "kudabiz") -> list[dict]:
    out = []
    for art in re.findall(r'<article class="card[^"]*">(.*?)</article>', html, re.S):
        href = re.search(r'href="(/event/[^"]+)"', art)
        time_m = re.search(r'<time[^>]+datetime="([^"]+)"', art)
        title_m = re.search(r'class="card-title"[^>]*>\s*<a[^>]*>([^<]+)</a>', art)
        city_m = re.search(r'class="card-city"[^>]*>([^<]+)<', art)
        cat_m = re.search(r'class="card-cat"[^>]*>([^<]+)<', art)
        if not (href and time_m and title_m):
            continue
        try:
            d = date.fromisoformat(time_m.group(1)[:10])
        except ValueError:
            continue
        city = strip_tags(city_m.group(1)).replace("📍", "").strip() if city_m else ""
        cat = cat_m.group(1).strip() if cat_m else default_etype
        title = unescape(title_m.group(1)).strip()
        et = TYPE_NORMALIZE.get(cat, default_etype)
        blob = title.lower().replace("ё", "е")
        if "ужин" in blob:
            et = "Бизнес-ужин"
        elif "завтрак" in blob:
            et = "Бизнес-завтрак"
        elif "кругл" in blob and "стол" in blob:
            et = "Круглый стол"
        elif "митап" in blob or "meetup" in blob:
            et = "Митап"
        elif "вебинар" in blob:
            et = "Вебинар"
        elif "мастер-класс" in blob:
            et = "Мастер-класс"
        elif et not in TARGET_TYPES:
            et = TYPE_NORMALIZE.get(et, default_etype)
        url = "https://kudabiz.ru" + href.group(1)
        ev = make_event(title, d, d, url, source, city=city, etype=et, extra_vert=cat)
        if ev and et in TARGET_TYPES:
            out.append(ev)
    return out


def parse_ru_day(text: str, default_year: int = 2026) -> Optional[date]:
    m = re.search(r"(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?", text.lower())
    if not m:
        return None
    mon = RU_MONTHS.get(m.group(2))
    if not mon:
        return None
    year = int(m.group(3) or default_year)
    try:
        return date(year, mon, int(m.group(1)))
    except ValueError:
        return None


def parse_event4etverg(html: str, source: str = "event4etverg") -> list[dict]:
    out = []
    for m in re.finditer(r'field="title">([^<]+)', html):
        title = unescape(m.group(1)).strip()
        tl = title.lower().replace("ё", "е")
        if not any(k in tl for k in ("завтрак", "ужин", "митап", "кругл", "мастер-класс")):
            continue
        ctx = re.sub(r"<[^>]+>", " ", html[m.start(): m.start() + 1800])
        ctx = re.sub(r"\s+", " ", ctx)
        d = parse_ru_day(title) or parse_ru_day(ctx[len(title): len(title) + 120])
        if not d:
            continue
        if "ужин" in tl:
            et = "Бизнес-ужин"
        elif "митап" in tl:
            et = "Митап"
        elif "кругл" in tl:
            et = "Круглый стол"
        elif "мастер-класс" in tl:
            et = "Мастер-класс"
        else:
            et = "Бизнес-завтрак"
        # clean title noise after date
        clean = re.split(r"\d{1,2}\s+[а-яё]+", title, maxsplit=1, flags=re.I)[0].strip(" -–—")
        if len(clean) < 8:
            clean = title
        ev = make_event(
            clean, d, d, "https://event4etverg.ru/", source,
            city="Москва", etype=et, extra_vert=title,
        )
        if ev:
            out.append(ev)
    return out


def parse_bizzavtrak(html: str, source: str = "bizzavtrak") -> list[dict]:
    out = []
    # Single nationwide event marketed on home
    isos = re.findall(r"(202\d-\d{2}-\d{2})", html)
    d = None
    for s in isos:
        try:
            dd = date.fromisoformat(s)
        except ValueError:
            continue
        if dd >= TODAY:
            d = dd
            break
    if not d:
        # try Russian «30 июля 2026»
        d = parse_ru_day(re.sub(r"<[^>]+>", " ", html)[:5000])
        if d and d < TODAY:
            d = None
    if d:
        ev = make_event(
            "Вся Россия за бизнес-завтраком",
            d, d, "https://bizzavtrak.ru/", source,
            city="Россия", etype="Бизнес-завтрак",
            extra_vert="бизнес-завтрак федеральный",
        )
        if ev:
            out.append(ev)
    return out


def merge_and_retag(
    existing: list[dict],
    new_events: list[dict],
    source_label: str,
) -> tuple[list[dict], int, int]:
    by_hard = {dedupe_key(e): i for i, e in enumerate(existing)}
    by_soft = {soft_key(e): i for i, e in enumerate(existing)}
    title_dates: dict[str, list[tuple[str, int]]] = {}
    for i, e in enumerate(existing):
        title_dates.setdefault(fuzzy_title(e.get("title") or ""), []).append(
            (e.get("starts_at") or "", i)
        )

    added = 0
    retagged = 0
    seen_new: set[str] = set()

    for e in new_events:
        if not e:
            continue
        if e.get("ends_at", "") < TODAY.isoformat():
            continue
        if not prefer_cis(e):
            continue
        k = dedupe_key(e)
        sk = soft_key(e)
        if k in seen_new:
            continue
        seen_new.add(k)

        idx = None
        if k in by_hard:
            idx = by_hard[k]
        elif sk in by_soft:
            idx = by_soft[sk]
        else:
            ft = fuzzy_title(e.get("title") or "")
            try:
                d1 = date.fromisoformat(e["starts_at"])
            except ValueError:
                d1 = None
            if d1 and ft in title_dates:
                for ds, i in title_dates[ft]:
                    try:
                        if abs((date.fromisoformat(ds) - d1).days) <= 1:
                            idx = i
                            break
                    except ValueError:
                        pass

        if idx is not None:
            old_t = existing[idx].get("type") or ""
            new_t = e.get("type") or old_t
            if new_t in TARGET_TYPES and old_t != new_t:
                existing[idx]["type"] = new_t
                retagged += 1
            continue

        existing.append(e)
        by_hard[k] = len(existing) - 1
        by_soft[sk] = len(existing) - 1
        title_dates.setdefault(fuzzy_title(e.get("title") or ""), []).append(
            (e.get("starts_at") or "", len(existing) - 1)
        )
        added += 1

    stats[f"added_{source_label}"] = added
    stats[f"retag_{source_label}"] = retagged
    return existing, added, retagged


def type_counts(events: list[dict]) -> Counter:
    return Counter(e.get("type") for e in events)


def retitle_retag_catalog(existing: list[dict]) -> int:
    """Conservative: retag only when title clearly IS the format (not side mention)."""
    n = 0
    for e in existing:
        title = (e.get("title") or "").strip()
        tl = title.lower().replace("ё", "е")
        old = e.get("type") or ""
        new = None
        if re.match(r"^бизнес[-\s]?завтрак\b", tl) or re.match(r"^завтрак\b", tl):
            new = "Бизнес-завтрак"
        elif re.match(r"^бизнес[-\s]?ужин\b", tl):
            new = "Бизнес-ужин"
        elif re.match(r"^кругл\w*\s+стол\b", tl) or tl.startswith("круглый стол"):
            new = "Круглый стол"
        elif re.match(r"^(митап|meetup)\b", tl):
            new = "Митап"
        elif re.match(r"^вебинар\b", tl):
            new = "Вебинар"
        elif re.match(r"^мастер[-\s]?класс\b", tl):
            new = "Мастер-класс"
        if new and new != old and new in TARGET_TYPES:
            e["type"] = new
            n += 1
    return n


def fetch_ae_type_pages() -> dict[str, list[dict]]:
    by_type: dict[str, list[dict]] = {t: [] for _, t in TYPE_SLUGS}
    fetched_ok = 0
    fetched_fail = 0

    def collect(slug: str, etype: str, url: str, sample_name: str) -> tuple[int, Optional[str], str]:
        nonlocal fetched_ok, fetched_fail
        html, err = fetch(url)
        time.sleep(0.3)
        if err or not html:
            walls.append(f"{url}: {err or 'empty'}")
            fetched_fail += 1
            return 0, None, ""
        save_sample(sample_name, html)
        fetched_ok += 1
        if page_is_generic(html):
            walls.append(
                f"{url}: page title is generic «мероприятия» — type filter empty/broken; SKIPPED"
            )
            return 0, None, html
        part = parse_ae_with_type(html, etype)
        by_type[etype].extend(part)
        sid = upcoming_section_id(html)
        # evidence
        chunk = extract_upcoming_chunk(html)
        n_schema = len(re.findall(r'itemprop="startDate"', chunk))
        n_up = len(part)
        scrape_evidence.append(
            f"AE {slug}: upcoming_section_cards≈{n_schema} parsed_upcoming>={TODAY}={n_up} sid={sid}"
        )
        return len(part), sid, html

    for slug, etype in TYPE_SLUGS:
        url = f"https://all-events.ru/events/calendar/type-is-{slug}/"
        n, sid, html = collect(slug, etype, url, f"ae_types_{slug}.html")
        print(f"  AE type-is-{slug}: parsed_upcoming={n} sid={sid}")

        use_slug = slug
        if n == 0 and slug in ALT_SLUGS:
            alt = ALT_SLUGS[slug]
            use_slug = alt
            url_alt = f"https://all-events.ru/events/calendar/type-is-{alt}/"
            n2, sid2, html = collect(alt, etype, url_alt, f"ae_types_{alt}.html")
            print(f"  AE type-is-{alt} (alt for {slug}): parsed_upcoming={n2}")
            n, sid = n2, sid2 or sid

        # PAGEN_1 + load_more pagination (real; PAGEN_2= 404)
        if sid and html and not page_is_generic(html):
            seen_urls = {e.get("organizer_url") for e in by_type[etype]}
            for page in range(2, 8):
                lm_url = (
                    f"https://all-events.ru/events/calendar/type-is-{use_slug}/"
                    f"?PAGEN_1={page}&load_more={sid}"
                )
                h2, err = fetch(lm_url)
                time.sleep(0.3)
                if err or not h2:
                    walls.append(f"{lm_url}: {err or 'empty'}")
                    break
                save_sample(f"ae_types_{use_slug}_lm_p{page}.html", h2)
                # load_more responses may be fragment or full page
                part = parse_ae_with_type(h2, etype)
                # also raw flex on whole response
                if not part:
                    part = parse_ae_flex_cards(h2, etype)
                new = [e for e in part if e.get("organizer_url") not in seen_urls]
                for e in new:
                    seen_urls.add(e.get("organizer_url"))
                    by_type[etype].append(e)
                print(f"    load_more PAGEN_1={page}: +{len(new)} (parsed={len(part)})")
                scrape_evidence.append(
                    f"AE {use_slug} PAGEN_1={page}&load_more: parsed={len(part)} new={len(new)}"
                )
                if len(new) == 0:
                    break

        # city variants when thin
        if n < 12 and etype in {"Бизнес-завтрак", "Круглый стол", "Бизнес-ужин", "Митап"}:
            for city in CITIES:
                url_c = (
                    f"https://all-events.ru/events/calendar/"
                    f"type-is-{use_slug}/city-is-{city}/"
                )
                nc, _, _ = collect(
                    use_slug, etype, url_c,
                    f"ae_types_{use_slug}_city-{city}.html",
                )
                if nc:
                    print(f"    city-is-{city}: +{nc}")

    for etype, evs in list(by_type.items()):
        seen: set[str] = set()
        uniq = []
        for e in evs:
            k = dedupe_key(e)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(e)
        by_type[etype] = uniq
        print(f"  UNIQUE {etype}: {len(uniq)}")

    stats["ae_fetch_ok"] = fetched_ok
    stats["ae_fetch_fail"] = fetched_fail
    return by_type


def main() -> None:
    existing = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    before_n = len(existing)
    before_types = type_counts(existing)
    targets = [
        "Бизнес-завтрак", "Круглый стол", "Бизнес-ужин",
        "Митап", "Вебинар", "Семинар", "Мастер-класс",
    ]
    print("BEFORE target types:")
    for t in targets:
        print(f"  {t}: {before_types.get(t, 0)}")

    print("\n=== Fetch All-Events type calendars ===")
    by_type = fetch_ae_type_pages()

    walls.append(
        "all-events: ?PAGEN_2=N returns HTTP 404 — do NOT use. "
        "Real upcoming pagination: ?PAGEN_1=N&load_more=<events-items id> "
        "(from «Показать еще» under Предстоящие)."
    )
    walls.append(
        "AE type-is-krugliy-stol «Предстоящие» section truly has only ~2 cards with "
        f"starts_at>={TODAY.isoformat()} (scrape 2026-09-19); Load More hidden/no-op. "
        "Past section has ~10+; unique ISO dates on full page ~20 include past+UI chrome — "
        "not ~50 upcoming."
    )
    walls.append(
        "type-is-biznes-uzhin / type-is-vebinar / type-is-master-klass → generic «мероприятия». "
        "Use type-is-webinar, type-is-master-class."
    )

    total_added = 0
    total_retag = 0
    for etype, evs in by_type.items():
        existing, a, r = merge_and_retag(existing, evs, f"ae:{etype}")
        total_added += a
        total_retag += r
        print(f"  merge {etype}: added={a} retagged={r} from {len(evs)} unique")

    # --- edu-afisha ---
    print("\n=== edu-afisha ===")
    edu_map = [
        ("https://edu-afisha.ru/events/kruglye-stoly/", "Круглый стол", "kruglye-stoly"),
        ("https://edu-afisha.ru/events/biznes-zavtraki/", "Бизнес-завтрак", "biznes-zavtraki"),
        ("https://edu-afisha.ru/events/mitapy/", "Митап", "mitapy"),
        ("https://edu-afisha.ru/events/vebinary/", "Вебинар", "vebinary"),
        ("https://edu-afisha.ru/events/treningi/", "Семинар", "treningi"),
        ("https://edu-afisha.ru/events/netvorkingi/", "Митап", "netvorkingi"),
    ]
    edu_all: list[dict] = []
    for base, etype, slug in edu_map:
        for page in range(1, 4):
            url = base if page == 1 else f"{base}page/{page}/"
            html, err = fetch(url)
            time.sleep(0.25)
            if err or not html:
                if page == 1:
                    walls.append(f"{url}: {err or 'empty'}")
                break
            save_sample(f"edu_afisha_{slug}_p{page}.html", html)
            part = parse_edu_afisha(html, etype)
            print(f"  {slug} p{page}: {len(part)}")
            edu_all.extend(part)
            if len(part) == 0:
                break
    # dedupe edu
    seen_e: set[str] = set()
    edu_uniq = []
    for e in edu_all:
        k = dedupe_key(e)
        if k in seen_e:
            continue
        seen_e.add(k)
        edu_uniq.append(e)
    existing, a, r = merge_and_retag(existing, edu_uniq, "edu_afisha")
    total_added += a
    total_retag += r
    print(f"  edu merge: added={a} retagged={r} from {len(edu_uniq)}")
    scrape_evidence.append(f"edu-afisha unique upcoming typed={len(edu_uniq)}")

    # --- ict2go ---
    print("\n=== ict2go ===")
    ict_events: list[dict] = []
    for url, sn in [
        ("https://ict2go.ru/events/", "ict2go_events_types.html"),
        ("https://ict2go.ru/types/meetup/", "ict2go_type_meetup.html"),
        ("https://ict2go.ru/types/webinar/", "ict2go_type_webinar.html"),
        ("https://ict2go.ru/types/seminar/", "ict2go_type_seminar.html"),
    ]:
        html, err = fetch(url)
        time.sleep(0.3)
        if err or not html:
            walls.append(f"{url}: {err or 'empty'}")
            continue
        save_sample(sn, html)
        part = parse_ict2go_typed(html)
        print(f"  {sn}: typed={len(part)}")
        ict_events.extend(part)
    # also keyword-scan larger dump if present
    for sn in ["ae_ict2go_events_live.html", "ict2go_events_types.html"]:
        p = SAMPLES / sn
        if p.exists():
            ict_events.extend(parse_ict2go_typed(p.read_text(encoding="utf-8", errors="replace")))

    seen_i: set[str] = set()
    ict_uniq = []
    for e in ict_events:
        k = dedupe_key(e)
        if k in seen_i:
            continue
        seen_i.add(k)
        ict_uniq.append(e)
    existing, a, r = merge_and_retag(existing, ict_uniq, "ict2go_typed")
    total_added += a
    total_retag += r
    print(f"  ict2go merge: added={a} retagged={r} from {len(ict_uniq)}")

    # --- kudabiz live-search ---
    print("\n=== kudabiz live-search ===")
    kb_map = [
        ("6", "Бизнес-завтрак"),
        ("7", "Вебинар"),
        ("8", "Мастер-класс"),
        ("4", "Семинар"),
        ("5", "Митап"),  # networking → may retag via title
    ]
    kb_all: list[dict] = []
    for cat_id, etype in kb_map:
        url = f"https://kudabiz.ru/live-search?cat={cat_id}&when=upcoming"
        html, err = fetch(url, extra_headers={"X-Requested-With": "XMLHttpRequest"})
        time.sleep(0.25)
        if err or not html:
            walls.append(f"{url}: {err or 'empty'}")
            continue
        save_sample(f"kb_live_cat{cat_id}.html", html)
        part = parse_kudabiz_live(html, etype)
        print(f"  cat={cat_id} ({etype}): {len(part)}")
        kb_all.extend(part)
    seen_k: set[str] = set()
    kb_uniq = []
    for e in kb_all:
        k = dedupe_key(e)
        if k in seen_k:
            continue
        seen_k.add(k)
        kb_uniq.append(e)
    existing, a, r = merge_and_retag(existing, kb_uniq, "kudabiz_live")
    total_added += a
    total_retag += r
    print(f"  kudabiz merge: added={a} retagged={r} from {len(kb_uniq)}")
    walls.append(
        "kudabiz: no roundtable category (cats 1–10 only; 6=breakfasts). "
        "Slug /cat/kruglye-stoly 404. Use live-search?cat=N&when=upcoming."
    )

    # --- series HTML ---
    print("\n=== series HTML ===")
    for url, sn, parser, label in [
        ("https://event4etverg.ru/", "event4etverg_home.html", parse_event4etverg, "event4etverg"),
        ("https://bizzavtrak.ru/", "bizzavtrak_home.html", parse_bizzavtrak, "bizzavtrak"),
    ]:
        html, err = fetch(url)
        time.sleep(0.25)
        if err or not html:
            walls.append(f"{url}: {err or 'empty'}")
            continue
        save_sample(sn, html)
        part = parser(html)
        print(f"  {label}: {len(part)}")
        existing, a, r = merge_and_retag(existing, part, label)
        total_added += a
        total_retag += r

    # probusinessrus / adv.dp — plain fetch then optional PW
    for url, sn, note in [
        ("https://probusinessrus.ru/business_breakfasts", "probusinessrus_breakfasts.html",
         "marketing page, no dated event cards in static HTML"),
        ("https://adv.dp.ru/events", "adv_dp_events.html",
         "Tilda shell; cards not in static HTML / no ISO dates after PW load-more"),
    ]:
        html, err = fetch(url)
        if err or not html:
            walls.append(f"{url}: {err or 'empty'}")
            continue
        save_sample(sn, html)
        isos = re.findall(r"202\d-\d{2}-\d{2}", html)
        up = [d for d in set(isos) if d >= TODAY.isoformat()]
        if not up:
            walls.append(f"{url}: WALL — {note}")

    skip_pw = os.environ.get("SKIP_PW", "").strip() in {"1", "true", "yes"}
    if not skip_pw:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto("https://adv.dp.ru/events", wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(2000)
                for _ in range(3):
                    page.evaluate(
                        """() => {
                          const b=[...document.querySelectorAll('a,button')]
                            .find(x=>/load more|показать/i.test(x.innerText||''));
                          if(b) b.click();
                        }"""
                    )
                    page.wait_for_timeout(1500)
                html = page.content()
                save_sample("pw_adv_dp_events.html", html)
                browser.close()
            if not re.findall(r"202\d-\d{2}-\d{2}", html):
                walls.append("adv.dp.ru/events Playwright: still no dated cards — WALL")
        except Exception as e:
            walls.append(f"adv.dp.ru Playwright skipped/failed: {type(e).__name__}: {e}")
    else:
        walls.append("SKIP_PW set — skipped Playwright for adv.dp.ru / series JS walls")

    # Light normalize + conservative title retag
    norm = 0
    for e in existing:
        t = e.get("type") or ""
        if t in TYPE_NORMALIZE and TYPE_NORMALIZE[t] in TARGET_TYPES:
            e["type"] = TYPE_NORMALIZE[t]
            norm += 1
    title_r = retitle_retag_catalog(existing)
    stats["normalized_plurals"] = norm
    stats["title_retag"] = title_r
    print(f"\nNormalized plural→UI: {norm}; title-prefix retag: {title_r}")

    write_catalog(existing)
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    after_types = type_counts(final)

    print("\n==== RESULT ====")
    print(f"N before={before_n} after={len(final)} delta={len(final)-before_n:+d}")
    print(f"added_new={total_added} retagged={total_retag} plurals_normalized={norm} title_retag={title_r}")
    print("\nTARGET TYPE COUNTS (before → after):")
    for t in targets:
        b, a_ = before_types.get(t, 0), after_types.get(t, 0)
        print(f"  {t}: {b} → {a_} ({a_-b:+d})")

    bz = after_types.get("Бизнес-завтрак", 0)
    ks = after_types.get("Круглый стол", 0)
    if bz >= 10 and ks >= 10:
        print("\nSUCCESS BAR: met (tens+ breakfast & roundtable)")
    else:
        print(
            f"\nSUCCESS BAR: volume still thin vs researcher ceiling — "
            f"AE live upcoming breakfast≈{len(by_type.get('Бизнес-завтрак', []))} "
            f"roundtable≈{len(by_type.get('Круглый стол', []))}. "
            "Honest: AE inventory after today is few for those two types."
        )

    report = []
    report.append(f"ALLEEVENTS TYPES INGEST {TODAY.isoformat()} Europe/Moscow")
    report.append("Script: ingest_allevents_types.py")
    report.append("Outputs: events_upcoming.json + events-data.js via write_catalog; index.html NOT touched")
    report.append(f"N {before_n} → {len(final)} (+{len(final)-before_n}), added={total_added}, retag={total_retag}, norm={norm}, title_retag={title_r}")
    report.append("TYPES before→after:")
    for t in targets:
        report.append(f"  {t}: {before_types.get(t,0)} → {after_types.get(t,0)}")
    report.append("SCRAPE EVIDENCE:")
    for line in scrape_evidence:
        report.append(f"- {line}")
    report.append("WALLS:")
    for w in walls:
        report.append(f"- {w}")
    (SAMPLES / "ingest_report_allevents_types.txt").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )
    (SAMPLES / "ingest_walls_allevents_types.txt").write_text(
        "\n".join(walls) + "\n", encoding="utf-8"
    )
    (SAMPLES / "ingest_evidence_allevents_types.txt").write_text(
        "\n".join(scrape_evidence) + "\n", encoding="utf-8"
    )
    print("\nWALLS:")
    for w in walls:
        print(f"- {w}")


if __name__ == "__main__":
    main()
