#!/usr/bin/env python3
"""Events Calendar ingest round4 — exponet foreign/cities, crocus 2027, APK seeds, jugru, foodsmi, AE extras."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_round2 import (  # noqa: E402
    TODAY, APK_RE, IT_RE,
    strip_tags, vertical_for, parse_dot_date, parse_ru_date_range, month_num,
    make_event, parse_workevent, parse_exponet_topic,
    parse_single_event_site, load_html, SAMPLES, ROOT,
)
from ingest_round3 import (  # noqa: E402
    normalize_url, fuzzy_title, dedupe_key, soft_key, merge, write_outputs,
    parse_all_events, parse_agroprodmash, parse_sibagroweek, parse_zivot_full,
    added_by_source, stats, walls,
)

# reset shared counters from round3 import
added_by_source.clear()
stats.clear()
walls.clear()


def parse_crocus_any(html: str, source: str = "crocus") -> list[dict]:
    """Crocus exhibition list — handles &mdash; and zero-padded days; 2026+2027."""
    html = unescape(html)
    out = []
    parts = re.split(r'<div class="list-item"', html)
    for p in parts[1:]:
        block = p[:9000]
        tm = re.search(
            r'class="leftimg-exhibition-about"[^>]*>\s*<span>([^<]+)</span>',
            block, re.S,
        )
        if not tm:
            tm = re.search(r'alt="([^"]+)"', block)
        if not tm:
            continue
        title = tm.group(1).strip()
        hdr = re.search(
            r'(\d{1,2}\s+[А-Яа-яЁё]+\s+202[67]\s*[—–\-]+\s*\d{1,2}\s+[А-Яа-яЁё]+\s+202[67])',
            block,
        )
        rng = None
        if hdr:
            s = re.sub(r"\b0(\d)\b", r"\1", hdr.group(1).lower())
            yr = 2027 if "2027" in s else 2026
            rng = parse_ru_date_range(s, yr)
        if not rng:
            ds = re.findall(r"(\d{1,2})\s+([А-Яа-яЁё]+)\s+(202[67])", block)
            if len(ds) >= 2:
                s = f"{int(ds[0][0])} {ds[0][1]} {ds[0][2]} - {int(ds[1][0])} {ds[1][1]} {ds[1][2]}"
                rng = parse_ru_date_range(s.lower(), int(ds[0][2]))
        if not rng:
            continue
        themes = " ".join(re.findall(r"detail_theme\.php[^>]*>([^<]+)</a>", block))
        em = re.search(r"<em>([^<]+)</em>", block)
        desc = em.group(1).strip() if em else ""
        link = re.search(r'href="(detail\.php\?ELEMENT_ID=\d+)"', block)
        yr = rng[0].year
        url = (
            "https://www.crocus-expo.ru/exhibition/" + link.group(1)
            if link
            else f"https://www.crocus-expo.ru/exhibition/?year={yr}"
        )
        ev = make_event(
            title, rng[0], rng[1], url, source, city="Москва",
            extra_vert=f"{themes} {desc}", description=desc[:120],
        )
        if ev:
            out.append(ev)
    return out


def parse_jugru(html: str, source: str = "jugru") -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out = []
    pp = (data.get("props") or {}).get("pageProps") or {}
    seasons = []
    if pp.get("currentSeason"):
        seasons.append(pp["currentSeason"])
    seasons.extend(pp.get("futureSeasons") or [])
    for season in seasons:
        for c in season.get("conferences") or []:
            proj = c.get("project") or {}
            ver = c.get("version") or {}
            title_obj = proj.get("title") or ver.get("title") or {}
            if isinstance(title_obj, dict):
                title = title_obj.get("ru") or title_obj.get("en") or ""
            else:
                title = str(title_obj or "")
            if not title:
                continue
            # append year from season
            yr = season.get("year") or 2026
            if str(yr) not in title:
                title = f"{title} {yr}"
            dates = ver.get("dates") or {}
            sd = (dates.get("startDate") or "")[:10]
            ed = (dates.get("endDate") or sd)[:10]
            if not sd:
                continue
            try:
                starts = date.fromisoformat(sd)
                ends = date.fromisoformat(ed)
            except ValueError:
                continue
            url_obj = proj.get("url") or ver.get("url") or {}
            if isinstance(url_obj, dict):
                url = url_obj.get("ru") or url_obj.get("en") or "https://jugru.org/"
            else:
                url = str(url_obj or "https://jugru.org/")
            venue = (ver.get("venue") or {}).get("ru") or {}
            city = venue.get("city") or ""
            ev = make_event(
                title, starts, ends, url, source, city=city,
                etype="Конференция", extra_vert="IT software conference jugru",
            )
            if ev:
                out.append(ev)
    return out


def parse_foodsmi(html: str, source: str = "foodsmi") -> list[dict]:
    """Plain-text calendar lines: '15-18 сентября 2026 Москва, WorldFood Moscow 2026'."""
    text = strip_tags(html)
    out = []
    # date range or single + city/title
    pat = re.compile(
        r"(\d{1,2}(?:\s*[-–—]\s*\d{1,2})?\s+"
        r"(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
        r"\s+202[67])\s+"
        r"([^.]{5,120}?)(?=\s+\d{1,2}(?:\s*[-–—]\s*\d{1,2})?\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)|$)",
        re.I,
    )
    for m in pat.finditer(text):
        date_s, rest = m.group(1), m.group(2).strip(" ,;—–-")
        # skip livestream/webinar noise without venue weight if too generic
        if rest.lower().startswith("прямой эфир"):
            continue
        yr = 2027 if "2027" in date_s else 2026
        rng = parse_ru_date_range(date_s, yr)
        if not rng:
            continue
        # rest often "Город, Title" or "City (Country), Title"
        city = ""
        title = rest
        if "," in rest:
            left, right = rest.split(",", 1)
            # if left looks like city
            if len(left) < 40 and not re.search(r"\d{4}", left):
                city = left.strip()
                title = right.strip()
        title = re.sub(r"\s+", " ", title).strip(" ,.—")
        if len(title) < 4:
            continue
        # online
        if "онлайн" in city.lower() or "онлайн" in title.lower():
            city = "Онлайн"
        ev = make_event(
            title, rng[0], rng[1], "https://foodsmi.com/events", source,
            city=city, extra_vert=f"food {title}", etype="Выставка",
        )
        if ev:
            out.append(ev)
    return out


def parse_agrobvk(html: str, source: str = "agrobvk") -> list[dict]:
    text = strip_tags(html)
    out = []
    # look for named event near dates
    for m in re.finditer(
        r"(АгроБВК|AGROBVK|агробвк)[^\d]{0,40}(\d{1,2}\s+[А-Яа-яЁё]+\s+202[67])",
        text, re.I,
    ):
        rng = parse_ru_date_range(m.group(2), 2026)
        if rng and rng[1] >= TODAY:
            ev = make_event(
                f"АгроБВК {rng[0].year}", rng[0], rng[1],
                "https://agrobvk.ru/", source, city="Уфа", extra_vert="агро",
            )
            if ev:
                out.append(ev)
            break
    # also standalone upcoming dates with title context
    for m in re.finditer(r"(\d{1,2}\s+[А-Яа-яЁё]+\s+202[67])", text):
        rng = parse_ru_date_range(m.group(1), 2026)
        if not rng or rng[1] < TODAY:
            continue
        chunk = text[max(0, m.start() - 80) : m.end() + 80]
        if re.search(r"агро|бвк|выставк", chunk, re.I):
            ev = make_event(
                f"АгроБВК {rng[0].year}", rng[0], rng[1],
                "https://agrobvk.ru/", source, city="Уфа", extra_vert="агро",
            )
            if ev:
                out.append(ev)
            break
    return out


def parse_agros_2027(html: str, source: str = "agravia") -> list[dict]:
    out = []
    for m in re.finditer(
        r"(\d{1,2}\s*[–\-—]?\s*\d{0,2}\s*[А-Яа-яЁё]*\s*2027|\d{1,2}\s+[А-Яа-яЁё]+\s+2027)",
        html, re.I,
    ):
        rng = parse_ru_date_range(m.group(1), 2027)
        if rng and rng[1] >= TODAY:
            # try expand to range if nearby second date
            chunk = html[m.start() : m.start() + 80]
            rng2 = parse_ru_date_range(unescape(chunk), 2027)
            if rng2:
                rng = rng2
            ev = make_event(
                "AGRAVIA 2027", rng[0], rng[1],
                "https://www.agros-expo.com/", source, city="Москва", extra_vert="агро",
            )
            if ev:
                out.append(ev)
                return out
    return out


def parse_niva(html: str, source: str = "niva") -> list[dict]:
    out = []
    # prefer upcoming 2027
    for m in re.finditer(r"(\d{1,2}\s+[а-яё]+\s+2027)", html, re.I):
        rng = parse_ru_date_range(m.group(1), 2027)
        if rng and rng[1] >= TODAY:
            # look for end date nearby
            chunk = html[m.start() : m.start() + 60]
            rng2 = parse_ru_date_range(unescape(strip_tags(chunk)), 2027) or rng
            ev = make_event(
                "Золотая Нива 2027", rng2[0], rng2[1],
                "https://niva-expo.ru/", source, city="Усть-Лабинск", extra_vert="агро нива",
            )
            if ev:
                out.append(ev)
                return out
    isos = sorted({d for d in re.findall(r"(202[67]-\d{2}-\d{2})", html) if d >= TODAY.isoformat()})
    if isos:
        # only if clearly niva dates in future - may be past 2026 field day
        fut = [date.fromisoformat(d) for d in isos if d >= "2027-01-01"]
        if fut:
            ev = make_event(
                "Золотая Нива 2027", min(fut), max(fut),
                "https://niva-expo.ru/", source, city="Усть-Лабинск", extra_vert="агро",
            )
            if ev:
                out.append(ev)
    return out


def parse_interagromash(html: str, source: str = "interagromash") -> list[dict]:
    out = []
    isos = sorted(set(re.findall(r"(2027-\d{2}-\d{2})", html)))
    if isos:
        ds = [date.fromisoformat(d) for d in isos]
        ev = make_event(
            "Интерагромаш 2027", min(ds), max(ds),
            "https://www.interagromash.net/", source, city="Ростов-на-Дону", extra_vert="агро",
        )
        if ev:
            out.append(ev)
            return out
    for m in re.finditer(r"(\d{1,2}\s+[а-яё]+\s+2027)", html, re.I):
        rng = parse_ru_date_range(m.group(1), 2027)
        if rng:
            ev = make_event(
                "Интерагромаш 2027", rng[0], rng[1],
                "https://www.interagromash.net/", source, city="Ростов-на-Дону", extra_vert="агро",
            )
            if ev:
                out.append(ev)
                break
    return out


def parse_it_single(html: str, title: str, url: str, source: str = "it_site") -> list[dict]:
    """Extract upcoming date range from a single-conference site."""
    out = []
    # ISO first
    isos = sorted({d for d in re.findall(r"(202[67]-\d{2}-\d{2})", html) if d >= TODAY.isoformat()})
    if isos:
        # take cluster near mode month
        ds = [date.fromisoformat(d) for d in isos]
        # prefer dates mentioned with title year
        starts, ends = min(ds), max(ds)
        # if span > 14 days, take densest week
        if (ends - starts).days > 14:
            from collections import Counter
            months = Counter((d.year, d.month) for d in ds)
            ym = months.most_common(1)[0][0]
            ds2 = [d for d in ds if (d.year, d.month) == ym]
            starts, ends = min(ds2), max(ds2)
        ev = make_event(title, starts, ends, url, source, etype="Конференция", extra_vert="IT")
        if ev:
            out.append(ev)
            return out
    for m in re.finditer(
        r"(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+202[67]|\d{1,2}\s+[а-яё]+\s+202[67])",
        html, re.I,
    ):
        yr = 2027 if "2027" in m.group(1) else 2026
        rng = parse_ru_date_range(m.group(1), yr)
        if rng and rng[1] >= TODAY:
            ev = make_event(title, rng[0], rng[1], url, source, etype="Конференция", extra_vert="IT")
            if ev:
                out.append(ev)
                return out
    return out


def parse_agbz_events(html: str, source: str = "agbz") -> list[dict]:
    text = strip_tags(html)
    out = []
    # lines with date + title-ish
    for m in re.finditer(
        r"(\d{1,2}(?:\s*[-–—]\s*\d{1,2})?\s+[а-яё]+\s+202[67])\s*[:\-–—]?\s*([A-Za-zА-Яа-яЁё0-9 «»\"\-\.]{5,80})",
        text, re.I,
    ):
        yr = 2027 if "2027" in m.group(1) else 2026
        rng = parse_ru_date_range(m.group(1), yr)
        if not rng or rng[1] < TODAY:
            continue
        title = m.group(2).strip(" :.-")
        if len(title) < 5:
            continue
        ev = make_event(
            title, rng[0], rng[1], "https://agbz.ru/events/", source,
            extra_vert="агро", etype="Мероприятие",
        )
        if ev:
            out.append(ev)
    return out


def main() -> None:
    existing = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    before_n = len(existing)
    before_vert = Counter(e.get("vertical") for e in existing)
    before_src = Counter(e.get("source") for e in existing)
    print(f"BEFORE N={before_n} vert={dict(before_vert)}")

    prev_cities = {
        "chelyabinsk", "ekaterinburg", "irkutsk", "kaliningrad", "kazan", "krasnodar",
        "krasnoyarsk", "moscow", "msk2", "nizhny", "nn", "novosibirsk", "omsk", "perm",
        "rostov", "samara", "sochi", "spb", "spb2", "tula", "tyumen", "ufa", "vladivostok",
        "volgograd", "voronezh",
    }

    # 1) Exponet — new RU cities + foreign capitals
    for p in sorted(SAMPLES.glob("exponet_city_*.html")):
        city = p.stem.replace("exponet_city_", "")
        if city in prev_cities:
            continue
        if p.stat().st_size < 5000:
            continue
        raw = p.read_bytes()
        try:
            html = raw.decode("cp1251")
        except Exception:
            html = raw.decode("utf-8", "replace")
        part = parse_exponet_topic(html, "exponet_future")
        if part:
            print(f"  exponet city {city}: {len(part)}")
            existing = merge(existing, part, f"exponet_city:{city}")

    foreign_globs = [
        "exponet_blr_*.html", "exponet_kaz_*.html", "exponet_uzb_*.html",
        "exponet_aze_*.html", "exponet_geo_*.html", "exponet_arm_*.html",
        "exponet_chn_*.html", "exponet_deu_*.html", "exponet_tur_*.html",
        "exponet_are_*.html", "exponet_pol_*.html", "exponet_ita_*.html",
        "exponet_ind_*.html", "exponet_fra_*.html", "exponet_usa_*.html",
    ]
    for g in foreign_globs:
        for p in sorted(SAMPLES.glob(g)):
            if p.stat().st_size < 8000:
                continue
            # skip country aggregate empty pages (<22k often directory-only)
            raw = p.read_bytes()
            try:
                html = raw.decode("cp1251")
            except Exception:
                html = raw.decode("utf-8", "replace")
            part = parse_exponet_topic(html, "exponet_future")
            if part:
                print(f"  exponet {p.stem}: {len(part)}")
                existing = merge(existing, part, f"exponet_foreign:{p.stem}")

    # 2) Workevent remaining schedules/industries (expect mostly dupes)
    for p in sorted(SAMPLES.glob("workevent_sch_*.html")) + sorted(SAMPLES.glob("workevent_industry_*.html")):
        if p.stat().st_size < 5000:
            continue
        part = parse_workevent(p.read_text(errors="replace"), "workevent")
        if part:
            existing = merge(existing, part, f"workevent:{p.stem}")

    # 3) Crocus 2026+2027
    for fn in ["apk_crocus_2027.html", "apk_crocus_2026.html", "crocus_2026.html"]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_crocus_any(html, "crocus")
        print(f"  crocus {fn}: {len(part)}")
        existing = merge(existing, part, f"crocus:{fn}")

    # 4) APK package
    html = load_html("apk_agros_home.html") or load_html("agros_expo.html")
    part = parse_agros_2027(html) if html else []
    print(f"  agros/agravia 2027: {len(part)}")
    if not part:
        walls.append(
            "agros-expo.com: AGRAVIA 2027 date found in HTML (22 ЯНВАРЯ 2027) — "
            "if parse failed check; else Jan 2026 past already noted round3."
        )
    existing = merge(existing, part, "agravia")

    html = load_html("apk_agroprodmash_home.html") or load_html("agroprodmash.html")
    part = parse_agroprodmash(html) if html else []
    print(f"  agroprodmash: {len(part)}")
    existing = merge(existing, part, "agroprodmash")

    html = load_html("apk_niva_home.html") or load_html("niva.html")
    part = parse_niva(html) if html else []
    print(f"  niva: {len(part)}")
    existing = merge(existing, part, "niva")

    html = load_html("apk_rfd_home.html") or load_html("rfd.html")
    if html:
        # Russian Field Day — July 2026 is PAST vs 2026-09-19
        part = parse_single_event_site(html, "Всероссийский день поля 2026", "https://russian-field-day.ru/", "rfd", "агро поле")
        print(f"  rfd: {len(part)} (expect 0 — July past)")
        if not part:
            walls.append("russian-field-day.ru: dates in July 2026 are past vs TODAY=2026-09-19. WALL for upcoming.")
        existing = merge(existing, part, "rfd")

    html = load_html("apk_minvody_home.html") or load_html("minvody.html")
    if html:
        part = parse_single_event_site(html, "МинводыАгро 2026", "https://minvodyagro.ru/", "minvody", "агро")
        print(f"  minvody: {len(part)} (July likely past)")
        if not part:
            walls.append("minvodyagro.ru: July 2026 date past vs TODAY. WALL.")
        existing = merge(existing, part, "minvody")

    html = load_html("apk_agrobvk_home.html")
    part = parse_agrobvk(html) if html else []
    print(f"  agrobvk: {len(part)}")
    existing = merge(existing, part, "agrobvk")

    html = load_html("apk_interagromash.html") or load_html("interagromash.html")
    part = parse_interagromash(html) if html else []
    print(f"  interagromash: {len(part)}")
    existing = merge(existing, part, "interagromash")

    html = load_html("apk_zivot_2026.html") or load_html("zivot_agrokalendar.html")
    part = parse_zivot_full(html) if html else []
    print(f"  zivot: {len(part)}")
    existing = merge(existing, part, "zivot")

    html = load_html("apk_agbz_events.html") or load_html("apk_agbz2.html") or load_html("agbz.html")
    part = parse_agbz_events(html) if html else []
    print(f"  agbz: {len(part)}")
    existing = merge(existing, part, "agbz")

    html = load_html("apk_foodsmi.html")
    part = parse_foodsmi(html) if html else []
    print(f"  foodsmi: {len(part)}")
    existing = merge(existing, part, "foodsmi")

    # agrosalon — still no dates
    html = load_html("apk_agrosalon_home.html") or load_html("agrosalon.html")
    part = parse_single_event_site(html, "Агросалон 2026", "https://www.agrosalon.ru/", "agrosalon", "агро") if html else []
    print(f"  agrosalon: {len(part)}")
    if not part:
        walls.append(
            "agrosalon.ru: visitors/about/home HTML still lack DD.MM/ISO/ru-month upcoming dates "
            "(SPA/images). Already covered via exponet/crocus/workevent. WALL without browser."
        )
    existing = merge(existing, part, "agrosalon")

    # feedvet SSL wall
    walls.append(
        "feedvet.ru: SSL UNEXPECTED_EOF / protocol error on fetch. Prior sample feedvet.html kept if any."
    )
    html = load_html("feedvet.html")
    if html:
        part = parse_single_event_site(html, "КормВет 2026", "https://feedvet.ru/", "feedvet", "агро корм")
        existing = merge(existing, part, "feedvet")

    # yugagro timeout again
    walls.append(
        "yugagro.org: timeout again (90s) on www and non-www; /ru-RU/ 404. Already in catalog elsewhere. WALL."
    )

    # 5) IT static
    html = load_html("it_jugru.html")
    part = parse_jugru(html) if html else []
    print(f"  jugru: {len(part)}")
    existing = merge(existing, part, "jugru")

    it_singles = [
        ("it_highload.html", "HighLoad++ 2026", "https://highload.ru/"),
        ("it_teamlead.html", "TeamLead Conf 2026", "https://teamleadconf.ru/"),
        ("it_heisenbug.html", "Heisenbug 2026", "https://heisenbug.ru/"),
        ("it_mobius.html", "Mobius 2026", "https://mobiusconf.com/"),
        ("it_jpoint.html", "JPoint 2026", "https://jpoint.ru/"),
        ("it_cppconf.html", "C++ Russia 2026", "https://cppconf.ru/"),
        ("it_holyjs.html", "HolyJS 2026", "https://holyjs.ru/"),
        ("it_devfest.html", "DevFest 2026", "https://devfest.ru/"),
        ("it_backendconf.html", "Backend Conf 2026", "https://backendconf.ru/"),
        ("it_devops.html", "DevOpsConf 2026", "https://devopsconf.ru/"),
        ("it_codefest.html", "CodeFest 2026", "https://codefest.ru/"),
        ("it_ontico.html", "Ontico Calendar", "https://ontico.ru/"),
    ]
    for fn, title, url in it_singles:
        html = load_html(fn)
        if not html:
            continue
        part = parse_it_single(html, title, url, "it_site")
        if part:
            print(f"  {fn}: {part[0]['starts_at']} {part[0]['title']}")
        existing = merge(existing, part, f"it_site:{fn}")

    # 6) All-events new calendar pages
    for p in sorted(SAMPLES.glob("ae_r4_*.html")):
        if p.stat().st_size < 50000:
            continue
        part = parse_all_events(p.read_text(errors="replace"), "all_events")
        existing = merge(existing, part, f"all_events:{p.stem}")

    # permanent walls
    walls.append("meatindustry.ru: 2024-only archive — skipped per steering.")
    walls.append("ExpoCalendar: skipped per steering (JS).")
    walls.append("KudaBiz page=N fake dupes — not used.")
    walls.append(
        "exponet moscow/topics/agriculture/future: 404; country-level */cities/dates/future/ "
        "pages are directories without date rows (no <tr> dates). City-level pages used instead."
    )
    walls.append(
        "all-events theme/city filters via /events/calendar/* often return overlapping default sets; "
        "honest soft-dedupe applied. Trailing paths without /calendar/ returned 404 this round."
    )
    walls.append(
        "expomap.ru year pages: SPA/filter shell — ISO strings are checkbox ids not event dates. No extract."
    )
    walls.append(
        "it-events.com: CSS-module cards without ISO/schema in static HTML; titles like #96. Not inventing dates."
    )

    write_outputs(existing)
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    vert = Counter(e.get("vertical") for e in final)
    src = Counter(e.get("source") for e in final)

    report = []
    report.append(f"INGEST ROUND4 FINAL REPORT {TODAY.isoformat()} (Europe/Moscow)")
    report.append("")
    report.append(f"BASELINE (round3): N={before_n}  apk={before_vert.get('apk',0)}  industry={before_vert.get('industry',0)}  it={before_vert.get('it',0)}")
    report.append(f"FINAL:             N={len(final)}  apk={vert.get('apk',0)}  industry={vert.get('industry',0)}  it={vert.get('it',0)}")
    report.append(
        f"DELTA vs baseline: N {len(final)-before_n:+d}  apk {vert.get('apk',0)-before_vert.get('apk',0):+d}  "
        f"industry {vert.get('industry',0)-before_vert.get('industry',0):+d}  it {vert.get('it',0)-before_vert.get('it',0):+d}"
    )
    report.append("")
    report.append("BY SOURCE (final):")
    for k, v in src.most_common():
        report.append(f"  {k}: {v} (was {before_src.get(k, 0)})")
    report.append("")
    report.append("HARVEST THIS ROUND (net after honest dedupe):")
    for k, v in sorted(added_by_source.items(), key=lambda x: -x[1]):
        if v:
            report.append(f"  {k}: +{v}")
    report.append("")
    report.append(f"SUCCESS: N>=1000 = {len(final) >= 1000}")
    report.append("")
    report.append("WALLS (evidence in samples/):")
    for i, w in enumerate(walls, 1):
        report.append(f"{i}. {w}")
    report.append("")
    report.append("FILES: events_upcoming.json, events-data.js, ingest_round4.py, samples/ingest_report_round4.txt, samples/ingest_walls_round4.txt")
    report_text = "\n".join(report) + "\n"
    (SAMPLES / "ingest_report_round4.txt").write_text(report_text, encoding="utf-8")
    (SAMPLES / "ingest_walls_round4.txt").write_text("\n".join(f"{i}. {w}" for i, w in enumerate(walls, 1)) + "\n", encoding="utf-8")
    print("\n==== RESULT ====")
    print(report_text)


if __name__ == "__main__":
    main()
