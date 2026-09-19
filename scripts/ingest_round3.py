#!/usr/bin/env python3
"""Events Calendar ingest round3 — Exponet cities/topics, workevent 2027+cities, APK package, all-events themes."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse, urlunparse, parse_qs, urlencode

# Reuse helpers from round2
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_round2 import (  # noqa: E402
    TODAY, MONTHS_RU, APK_RE, IT_RE,
    strip_tags, vertical_for, parse_dot_date, parse_ru_date_range, month_num,
    parse_exponet_date_cell, make_event, parse_workevent, parse_exponet_topic,
    parse_zivot_seed, parse_single_event_site, load_html, SAMPLES, ROOT,
)

added_by_source: Counter = Counter()
stats: dict[str, Any] = {}
walls: list[str] = []


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
        # strip tracking erid etc
        qs = parse_qs(p.query)
        for k in list(qs):
            if k.lower() in {"erid", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "yclid"}:
                qs.pop(k, None)
        q = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True) if qs else ""
        path = p.path.rstrip("/") + ("/" if p.path.endswith("/") or not p.path else "")
        # keep path without forcing trailing for non-all-events
        path = p.path.rstrip("/")
        return urlunparse((p.scheme, p.netloc.lower(), path, "", q, "")).lower()
    except Exception:
        return url.strip().lower()


def fuzzy_title(t: str) -> str:
    t = (t or "").lower().replace("ё", "е")
    t = re.sub(r"[«»\"'„“”]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*[-–—]\s*20\d{2}\s*$", "", t)
    t = re.sub(r"\s+20\d{2}\s*$", "", t)
    return t[:80]


def dedupe_key(e: dict) -> str:
    return normalize_url(e.get("organizer_url") or "") + "|" + fuzzy_title(e.get("title") or "")


def soft_key(e: dict) -> tuple:
    return (fuzzy_title(e.get("title") or ""), e.get("starts_at") or "")


def merge(existing: list[dict], new_events: list[dict], source_label: str) -> list[dict]:
    seen = {dedupe_key(e) for e in existing}
    soft = {soft_key(e) for e in existing}
    # also near-date soft: same fuzzy title within ±1 day
    title_dates: dict[str, set[str]] = {}
    for e in existing:
        title_dates.setdefault(fuzzy_title(e["title"]), set()).add(e["starts_at"])
    added = 0
    for e in new_events:
        if not e:
            continue
        if e.get("ends_at", "") < TODAY.isoformat():
            continue
        k = dedupe_key(e)
        if k in seen:
            continue
        sk = soft_key(e)
        if sk in soft:
            continue
        ft = fuzzy_title(e["title"])
        # fuzzy: same title, start within 1 day
        near = False
        for ds in title_dates.get(ft, ()):
            try:
                d0 = date.fromisoformat(ds)
                d1 = date.fromisoformat(e["starts_at"])
                if abs((d0 - d1).days) <= 1:
                    near = True
                    break
            except ValueError:
                pass
        if near:
            continue
        existing.append(e)
        seen.add(k)
        soft.add(sk)
        title_dates.setdefault(ft, set()).add(e["starts_at"])
        added += 1
        added_by_source[source_label] += 1
    stats[f"added_{source_label}"] = added
    return existing


def write_outputs(events: list[dict]) -> None:
    events = [e for e in events if e.get("ends_at", "") >= TODAY.isoformat()]
    events.sort(key=lambda e: (e.get("starts_at") or "", e.get("title") or ""))
    (ROOT / "events_upcoming.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    js_events = []
    for i, e in enumerate(events, 1):
        js_events.append({
            "id": i,
            "title": e["title"],
            "vertical": e.get("vertical") or "industry",
            "type": e.get("type") or "Мероприятие",
            "city": e.get("city") or "",
            "country": e.get("country") or "Россия",
            "date": e.get("starts_at"),
            "endDate": e.get("ends_at"),
            "status": e.get("date_status") or "confirmed",
            "url": e.get("organizer_url") or "",
            "place": e.get("city") or "—",
            "source": e.get("source") or "",
        })
    js = "window.EVENTS_DATA = " + json.dumps(js_events, ensure_ascii=False, indent=2) + ";\n"
    (ROOT / "events-data.js").write_text(js, encoding="utf-8")


# ---------- parsers ----------

def parse_all_events(html: str, source: str = "all_events") -> list[dict]:
    out = []
    parts = re.split(r'<div class="event-wrapper"[^>]*itemscope[^>]*Event[^>]*>', html)
    for p in parts[1:]:
        block = p[:5000]
        sd = re.search(r'itemprop="startDate"[^>]*content="([^"]+)"', block)
        ed = re.search(r'itemprop="endDate"[^>]*content="([^"]+)"', block)
        name = re.search(r'class="event-title"[^>]*itemprop="name"[^>]*>([^<]+)', block)
        if not name:
            name = re.search(r'itemprop="name">([^<]+)', block)
        urlm = re.search(r'itemprop="url"[^>]*href="([^"]+)"', block) or re.search(
            r'href="(/events/[^"?]+)', block
        )
        citym = re.search(r'itemprop="addressLocality">([^<]+)', block)
        if not (sd and name):
            continue
        try:
            starts = date.fromisoformat(sd.group(1)[:10])
            ends = date.fromisoformat(ed.group(1)[:10]) if ed else starts
        except ValueError:
            continue
        title = unescape(name.group(1)).strip()
        href = urlm.group(1) if urlm else ""
        if href and not href.startswith("http"):
            href = "https://all-events.ru" + href
        # strip query
        href = href.split("?")[0]
        city = citym.group(1).strip() if citym else ""
        if city.lower() in {"онлайн-трансляция", "не определено", "онлайн"}:
            city = "Онлайн"
        elif "," in city:
            city = city.split(",")[0].strip()
        ev = make_event(title, starts, ends, href or "https://all-events.ru/events/", source,
                        city=city, etype="Конференция", extra_vert=title)
        if ev:
            out.append(ev)
    return out


def parse_crocus(html: str, source: str = "crocus") -> list[dict]:
    out = []
    parts = re.split(r'<div class="list-item"', html)
    for p in parts[1:]:
        block = p[:8000]
        # title from span inside leftimg-exhibition-about
        tm = re.search(
            r'class="leftimg-exhibition-about"[^>]*>\s*<span>([^<]+)</span>',
            block, re.S,
        )
        if not tm:
            tm = re.search(r'alt="([^"]+)"[^>]*title="', block)
        if not tm:
            continue
        title = unescape(tm.group(1)).strip()
        # Russian date in header: 21 Января 2026 — 23 Января 2026
        hdr = re.search(
            r'(\d{1,2}\s+[А-Яа-яЁё]+\s+2026\s*[—–\-]+\s*\d{1,2}\s+[А-Яа-яЁё]+\s+2026)',
            block,
        )
        rng = None
        if hdr:
            rng = parse_ru_date_range(hdr.group(1).lower(), 2026)
        if not rng:
            dots = re.findall(r"(\d{2}\.\d{2}\.2026)", block)
            if dots:
                ds = [parse_dot_date(d) for d in dots]
                ds = [d for d in ds if d]
                if ds:
                    rng = (min(ds), max(ds))
        if not rng:
            continue
        # themes for vertical
        themes = " ".join(re.findall(r'detail_theme\.php[^>]*>([^<]+)</a>', block))
        desc = ""
        em = re.search(r'<em>([^<]+)</em>', block)
        if em:
            desc = em.group(1).strip()
        url = "https://www.crocus-expo.ru/exhibition/?year=2026"
        # try detail link
        link = re.search(r'href="(detail\.php\?ELEMENT_ID=\d+)"', block)
        if link:
            url = "https://www.crocus-expo.ru/exhibition/" + link.group(1)
        ev = make_event(
            title, rng[0], rng[1], url, source, city="Москва",
            extra_vert=f"{themes} {desc}", description=desc[:120],
        )
        if ev:
            out.append(ev)
    return out


def parse_agroprodmash(html: str, source: str = "agroprodmash") -> list[dict]:
    out = []
    # CONTENT / yellow date: 28 сентября – 1 октября 2026
    for m in re.finditer(
        r"(\d{1,2}\s+[а-яё]+\s*[–—\-]+\s*\d{1,2}\s+[а-яё]+\s+2026)",
        html, re.I,
    ):
        rng = parse_ru_date_range(m.group(1), 2026)
        if not rng:
            continue
        ev = make_event(
            "АГРОПРОДМАШ-2026",
            rng[0], rng[1],
            "https://www.agroprodmash-expo.ru/",
            source, city="Москва", extra_vert="агропродмаш пищев агро",
        )
        if ev:
            out.append(ev)
            break
    return out


def parse_sibagroweek(html: str, source: str = "sibagroweek") -> list[dict]:
    out = []
    # 10 - 12 ноября / 12 ноября 2026
    rng = None
    m = re.search(r"(\d{1,2}\s*[-–—]\s*\d{1,2}\s+[а-яё]+\s+2026)", html, re.I)
    if m:
        rng = parse_ru_date_range(m.group(1), 2026)
    if not rng:
        m2 = re.search(r"(10\s*[-–—]\s*12\s+ноября)", html, re.I)
        m3 = re.search(r"12\s+ноября\s+2026", html, re.I)
        if m2:
            rng = parse_ru_date_range(m2.group(1) + " 2026", 2026)
        elif m3:
            rng = parse_ru_date_range("10-12 ноября 2026", 2026)
    if rng:
        ev = make_event(
            "Сибирская аграрная неделя 2026",
            rng[0], rng[1],
            "https://sibagroweek.ru/",
            source, city="Новосибирск", extra_vert="агро сельхоз",
        )
        if ev:
            out.append(ev)
    return out


def parse_agros_expo(html: str, source: str = "agravia") -> list[dict]:
    """AGRAVIA 2026 — look for Jan dates or seed from known window if only title year."""
    out = []
    # try datetime / ru ranges
    times = re.findall(r'datetime="(\d{4}-\d{2}-\d{2})', html)
    if times:
        dates = sorted({date.fromisoformat(t) for t in times if t.startswith("202")})
        dates = [d for d in dates if d.year >= 2026]
        if dates:
            ev = make_event(
                "AGRAVIA 2026", dates[0], dates[-1],
                "https://www.agros-expo.com/", source, city="Москва", extra_vert="агро",
            )
            if ev:
                out.append(ev)
                return out
    for m in re.finditer(
        r"(\d{1,2}\s*[–\-]\s*\d{1,2}\s+[а-яё]+\s+2026|\d{1,2}\s+[а-яё]+\s+2026)",
        html, re.I,
    ):
        rng = parse_ru_date_range(m.group(1), 2026)
        if rng and rng[1] >= TODAY:
            ev = make_event(
                "AGRAVIA 2026", rng[0], rng[1],
                "https://www.agros-expo.com/", source, city="Москва", extra_vert="агро",
            )
            if ev:
                out.append(ev)
                return out
    # Crocus list already has AGRAVIA Jan 2026 (past). No upcoming on home — wall.
    return out


def parse_zivot_full(html: str, source: str = "zivot") -> list[dict]:
    out = []
    text = strip_tags(html)
    # Name ... Дата: ... Место: ...
    for m in re.finditer(
        r"([A-Za-zА-Яа-яЁё0-9 «»\"\-\.]{3,80}?)\s*"
        r"Дата:\s*([^М]{5,50}?)\s*Место:\s*([^С\n]{3,80}?)\s*Суть:",
        text,
    ):
        title, date_s, place = m.group(1).strip(" :.-"), m.group(2).strip(), m.group(3).strip()
        # clean title: take last line-ish token
        title = re.split(r"\s{2,}|\n", title)[-1].strip()
        if len(title) < 3:
            continue
        rng = parse_ru_date_range(date_s, 2026)
        if not rng:
            continue
        city = place.split(",")[0].strip()
        ev = make_event(
            title, rng[0], rng[1],
            "https://zivotnovodstvo.ru/agrokalendar-2026",
            source, city=city, extra_vert="агро животновод",
        )
        if ev:
            out.append(ev)
    # also seed known named events if mentioned with 2026 dates elsewhere
    seeds = [
        (r"ЮгАгро|YugAgro", "ЮгАгро 2026", "Краснодар", "https://www.yugagro.org/"),
        (r"Агросалон|Agrosalon", "Агросалон 2026", "Москва", "https://www.agrosalon.ru/"),
        (r"Агропродмаш", "Агропродмаш 2026", "Москва", "https://www.agroprodmash-expo.ru/"),
    ]
    for pat, title, city, url in seeds:
        if not re.search(pat, text, re.I):
            continue
        # look for date near mention
        for m in re.finditer(pat, text, re.I):
            chunk = text[m.start() : m.start() + 200]
            rng = parse_ru_date_range(chunk, 2026)
            if rng and rng[1] >= TODAY:
                ev = make_event(title, rng[0], rng[1], url, source, city=city, extra_vert="агро")
                if ev:
                    out.append(ev)
                break
    # fall back to round2 seed parser
    out.extend(parse_zivot_seed(html, source))
    return out


def parse_agrosalon_seed(html: str, source: str = "agrosalon") -> list[dict]:
    """If no dates in HTML, skip (past season often). Try extract."""
    return parse_single_event_site(html, "Агросалон 2026", "https://www.agrosalon.ru/", source, "агро")


def main() -> None:
    existing = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    before_n = len(existing)
    before_vert = Counter(e.get("vertical") for e in existing)
    before_src = Counter(e.get("source") for e in existing)
    print(f"BEFORE N={before_n} vert={dict(before_vert)}")

    # 1) Exponet topics (all)
    for p in sorted(SAMPLES.glob("exponet_topic_*.html")):
        topic = p.stem.replace("exponet_topic_", "")
        html = p.read_text(encoding="utf-8", errors="replace")
        part = parse_exponet_topic(html, "exponet_future")
        print(f"  exponet topic {topic}: {len(part)}")
        existing = merge(existing, part, f"exponet_topic:{topic}")

    # 2) Exponet city futures
    for p in sorted(SAMPLES.glob("exponet_city_*.html")):
        city = p.stem.replace("exponet_city_", "")
        html = p.read_text(encoding="utf-8", errors="replace")
        part = parse_exponet_topic(html, "exponet_future")
        print(f"  exponet city {city}: {len(part)}")
        existing = merge(existing, part, f"exponet_city:{city}")

    # 3) Workevent 2027 + cities + it + apk
    for fn, label in [
        ("workevent_2027.html", "workevent"),
        ("workevent_city_moskva-1.html", "workevent"),
        ("workevent_city_sankt-peterburg-5.html", "workevent"),
        ("workevent_city_ekaterinburg-17.html", "workevent"),
        ("workevent_city_novosibirsk-12.html", "workevent"),
        ("workevent_city_kazan-21.html", "workevent"),
        ("workevent_city_krasnodar-16.html", "workevent"),
        ("workevent_city_sochi-4.html", "workevent"),
        ("workevent_city_ufa-20.html", "workevent"),
        ("workevent_it.html", "workevent"),
        ("workevent_apk.html", "workevent"),
    ]:
        html = load_html(fn)
        if not html:
            print(f"  {fn}: missing")
            continue
        part = parse_workevent(html, "workevent")
        print(f"  {fn}: raw_parsed={len(part)}")
        existing = merge(existing, part, f"workevent:{fn}")

    # 4) All-events themes / pages
    for fn in [
        "ae_page1.html", "ae_theme_it.html", "ae_theme_ai.html",
        "ae_theme_marketing.html", "ae_type_forum.html", "ae_datefrom.html",
        "ae_cal_202610.html",
    ]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_all_events(html, "all_events")
        print(f"  {fn}: {len(part)}")
        existing = merge(existing, part, f"all_events:{fn}")

    # 5) APK package
    # meatindustry — expect wall
    html = load_html("meatindustry.html")
    if html:
        years = sorted(set(re.findall(r"\d{2}\.\d{2}\.(20\d{2})", html)))
        up = [d for d in re.findall(r"\d{2}\.\d{2}\.20\d{2}", html)
              if (parse_dot_date(d) or date(1900, 1, 1)) >= TODAY]
        walls.append(
            f"meatindustry.ru: {len(re.findall(r'\\d{{2}}\\.\\d{{2}}\\.20\\d{{2}}', html))} DD.MM.YYYY "
            f"but years={years}; upcoming>=today={len(up)}. Archive 2024 only — WALL."
        )
        print("  meatindustry: WALL", years)

    html = load_html("agroprodmash.html")
    part = parse_agroprodmash(html) if html else []
    print(f"  agroprodmash: {len(part)}")
    existing = merge(existing, part, "agroprodmash")

    html = load_html("sibagroweek.html")
    part = parse_sibagroweek(html) if html else []
    print(f"  sibagroweek: {len(part)}")
    existing = merge(existing, part, "sibagroweek")

    html = load_html("agros_expo.html")
    part = parse_agros_expo(html) if html else []
    print(f"  agros_expo/agravia: {len(part)}")
    if not part:
        walls.append(
            "agros-expo.com (AGRAVIA): title has 2026 but no upcoming ISO/ru-range in static HTML "
            "(only old 2022 schema dates). Jan 2026 edition already past vs today=2026-09-19. WALL for new dates."
        )
    existing = merge(existing, part, "agravia")

    html = load_html("crocus_2026.html")
    part = parse_crocus(html) if html else []
    print(f"  crocus: {len(part)}")
    existing = merge(existing, part, "crocus")

    html = load_html("zivot_agrokalendar.html") or load_html("zivot_agrokalendar2.html")
    part = parse_zivot_full(html) if html else []
    print(f"  zivot: {len(part)}")
    existing = merge(existing, part, "zivot")

    html = load_html("agrosalon.html")
    part = parse_agrosalon_seed(html) if html else []
    print(f"  agrosalon: {len(part)}")
    if not part:
        walls.append(
            "agrosalon.ru: static HTML has no DD.MM.YYYY / ISO / ru-month 2026 upcoming dates "
            "(spa or images). Seed skipped. WALL without browser."
        )
    existing = merge(existing, part, "agrosalon")

    walls.append(
        "exponet moscow/topics/agriculture/future: HTTP 404 "
        "(path .../cities/moscow/topics/agriculture/dates/future/). "
        "Used city/moscow future + topic/agriculture instead."
    )
    walls.append(
        "yugagro.org: urllib timeout. No HTML harvested this round."
    )
    walls.append(
        "KudaBiz page=N: NOT used (fake duplicate 24). Categories not expanded this round."
    )
    walls.append(
        "ExpoCalendar: SKIPPED per steering."
    )
    walls.append(
        "all-events ?page=2..5: same 20 schema events as page1 (no real pagination). "
        "Theme filters (IT/AI/marketing/forum) yield distinct sets — used those."
    )

    write_outputs(existing)
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    vert = Counter(e.get("vertical") for e in final)
    src = Counter(e.get("source") for e in final)

    report = []
    report.append(f"INGEST ROUND3 REPORT {TODAY.isoformat()} (Europe/Moscow)")
    report.append("")
    report.append(f"BEFORE: N={before_n} apk={before_vert.get('apk',0)} industry={before_vert.get('industry',0)} it={before_vert.get('it',0)}")
    report.append(f"AFTER:  N={len(final)} apk={vert.get('apk',0)} industry={vert.get('industry',0)} it={vert.get('it',0)}")
    report.append(f"DELTA: N {len(final)-before_n:+d}, apk {vert.get('apk',0)-before_vert.get('apk',0):+d}, it {vert.get('it',0)-before_vert.get('it',0):+d}")
    report.append("")
    report.append("ADDED BY SOURCE LABEL:")
    for k, v in sorted(added_by_source.items(), key=lambda x: -x[1]):
        if v:
            report.append(f"  {k}: +{v}")
    report.append("")
    report.append("BY SOURCE (catalog):")
    for k, v in src.most_common():
        report.append(f"  {k}: {v} (was {before_src.get(k,0)})")
    report.append("")
    report.append("WALLS:")
    for w in walls:
        report.append(f"- {w}")
    report_text = "\n".join(report) + "\n"
    (SAMPLES / "ingest_report_round3.txt").write_text(report_text, encoding="utf-8")
    (SAMPLES / "ingest_walls_round3.txt").write_text("\n".join(walls) + "\n", encoding="utf-8")
    print("\n==== RESULT ====")
    print(report_text)


if __name__ == "__main__":
    main()
