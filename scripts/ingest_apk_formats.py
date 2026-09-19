#!/usr/bin/env python3
"""APK × (Бизнес-завтрак | Круглый стол) ingest: TimePad agro keywords + All-Events themes.

Token from /home/box/.secrets/TIMEPAD_TOKEN — never printed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from ingest_round2 import TODAY, make_event, vertical_for, parse_dot_date, strip_tags  # noqa: E402
from ingest_round3 import merge, parse_all_events  # noqa: E402
from enrich_summaries import write_catalog, is_real_summary  # noqa: E402
from ingest_allevents_types import (  # noqa: E402
    parse_ae_with_type, parse_ae_flex_cards, extract_upcoming_chunk,
    upcoming_section_id, page_is_generic, prefer_cis, fetch as ae_fetch,
)

SAMPLES = ROOT / "samples"
JSON_PATH = ROOT / "events_upcoming.json"
TOKEN_PATH = Path("/home/box/.secrets/TIMEPAD_TOKEN")

STRICT_APK = re.compile(
    r"агро|сельхоз|сельск\w*\s+хоз|(?<![а-яёa-z])ферм|молоч|пищев|пищёв|мясн|зерн|ветерин|"
    r"комбикорм|винодел|аквакульт|птице|свиновод|животнов|растениевод|"
    r"урожа|теплич|садовод|рыбовод|кормопроизвод|агропром|агротех|агробиз|"
    r"продмаш|world\s*food|peterfood|food[\s-]?tech|dairy|livestock|poultry",
    re.I,
)
FALSE_APK = re.compile(
    r"предпринимател|партн[её]рск\w*\s+завтрак|недвижим|городск\w*\s+завтрак|"
    r"искусственн\w*\s+интеллект|(?<![а-яёa-z])ии(?![а-яёa-z])|"
    r"ресторан\w*\s+и\s+отел|операционка\s+без\s+вас",
    re.I,
)

AGRO_KEYWORDS = [
    "агро",
    "агропром",
    "сельхоз",
    "ферм",
    "молоч",
    "пище",
    "мясн",
    "зерн",
    "комбикорм",
    "винодел",
    "аквакультур",
]

TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Бизнес-завтрак", re.compile(
        r"бизнес[-\s]?завтрак\w*|завтрак\w*\s+для|деловой\s+завтрак\w*|"
        r"образовательн\w*\s+завтрак\w*|партнёрск\w*\s+завтрак\w*|партнерск\w*\s+завтрак\w*",
        re.I,
    )),
    ("Бизнес-завтрак", re.compile(r"завтрак\w*", re.I)),
    ("Круглый стол", re.compile(r"кругл\w*\s+стол\w*", re.I)),
    ("Бизнес-ужин", re.compile(r"бизнес[-\s]?ужин\w*", re.I)),
    ("Бизнес-ужин", re.compile(r"(?<![а-яё])ужин\w*(?![а-яё])", re.I)),
    ("Митап", re.compile(r"митап\w*|meetup|meet[\s-]?up", re.I)),
    ("Вебинар", re.compile(r"вебинар\w*", re.I)),
    ("Семинар", re.compile(r"семинар\w*", re.I)),
    ("Мастер-класс", re.compile(r"мастер[-\s]?класс\w*", re.I)),
    ("Нетворкинг", re.compile(r"нетворкинг\w*|networking", re.I)),
    ("Форум", re.compile(r"форум\w*", re.I)),
    ("Конференция", re.compile(r"конференц\w*", re.I)),
    ("Выставка", re.compile(r"выставк\w*|expo\b|экспо\b", re.I)),
]

AE_THEME_SLUGS = [
    "agroprom",
    "selhoz",
    "pishchevaya_promyshlennost",
    "food_industry",
]

AE_TYPE_THEME = [
    ("biznes-zavtrak", "Бизнес-завтрак", "agroprom"),
    ("biznes-zavtrak", "Бизнес-завтрак", "food_industry"),
    ("biznes-zavtrak", "Бизнес-завтрак", "pishchevaya_promyshlennost"),
    ("biznes-zavtrak", "Бизнес-завтрак", "selhoz"),
    ("krugliy-stol", "Круглый стол", "agroprom"),
    ("krugliy-stol", "Круглый стол", "food_industry"),
    ("krugliy-stol", "Круглый стол", "pishchevaya_promyshlennost"),
    ("krugliy-stol", "Круглый стол", "selhoz"),
]

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

walls: list[str] = []
stats: dict[str, Any] = {}


def load_token() -> str:
    env = (os.environ.get("TIMEPAD_TOKEN") or "").strip()
    if env:
        return env
    if TOKEN_PATH.is_file():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    raise SystemExit("TIMEPAD_TOKEN missing")


def api_get(token: str, params: dict) -> tuple[int, dict]:
    q = urllib.parse.urlencode(params, doseq=True)
    url = f"https://api.timepad.ru/v1/events.json?{q}"
    r = subprocess.run(
        [
            "curl", "-sS", "-w", "\n%{http_code}",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Accept: application/json",
            "-H", "User-Agent: EventsCalendarBot/1.0",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr[:200]}")
    out = r.stdout or ""
    if "\n" in out:
        body, _, code_s = out.rpartition("\n")
    else:
        body, code_s = out, "0"
    try:
        status = int(code_s.strip())
    except ValueError:
        status = 0
        body = out
    if status == 429:
        raise RuntimeError("HTTP 429 rate limit")
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:160]}")
    return status, json.loads(body)


def classify_type(name: str) -> str:
    title = unescape(name or "")
    for etype, pat in TYPE_PATTERNS:
        if pat.search(title):
            return etype
    return "Мероприятие"


def parse_starts(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        s2 = s.replace("+0300", "+03:00").replace("+0000", "+00:00")
        if len(s2) >= 10:
            return date.fromisoformat(s2[:10])
    except Exception:
        return None
    return None


def strip_html(t: str) -> str:
    t = unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_strict_apk(title: str, desc: str = "") -> bool:
    title = title or ""
    if FALSE_APK.search(title):
        return False
    return bool(STRICT_APK.search(f"{title} {desc or ''}"))


def event_from_tp_apk(raw: dict) -> Optional[dict]:
    """Keep only STRICT APK title/desc hits; type from title; vertical=apk."""
    name = strip_html(raw.get("name") or "")
    if not name:
        return None
    etype = classify_type(name)
    starts_d = parse_starts(raw.get("starts_at") or "")
    if not starts_d or starts_d < TODAY:
        return None
    if starts_d.year > 2027:
        return None
    ends_d = parse_starts(raw.get("ends_at") or "") or starts_d
    url = (raw.get("url") or "").strip()
    if not url:
        return None
    loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    city = (loc.get("city") or raw.get("city") or "").strip()
    if city.lower() in {"спб", "питер", "петербург"}:
        city = "Санкт-Петербург"
    if city.lower() in {"online", "remote"} or "онлайн" in city.lower():
        city = "Онлайн"
    if re.search(r"london|paris|berlin|warsaw|киев|kyiv|алматы|дубай|dubai", city, re.I):
        return None
    desc = strip_html(raw.get("description_short") or raw.get("description") or "")
    cats = " ".join(c.get("name") or "" for c in (raw.get("categories") or []))
    if not is_strict_apk(name, f"{cats} {desc}"):
        return None
    blob = f"{name} {cats} {desc} агро сельхоз"
    # STRICT hit → vertical apk
    ev = make_event(
        name, starts_d, ends_d, url, "timepad",
        city=city or "", country="Россия", etype=etype,
        extra_vert=blob, description=desc[:500],
    )
    if not ev:
        # soft formats may be dropped by vertical_for — force apk via synthetic
        # lifestyle title still excluded
        from ingest_round2 import LIFESTYLE_RE
        if LIFESTYLE_RE.search(name) and not re.search(r"бизнес[-\s]?(завтрак|ужин)", name, re.I):
            return None
        ev = {
            "title": name,
            "starts_at": starts_d.isoformat(),
            "ends_at": ends_d.isoformat(),
            "city": city or "",
            "country": "Россия",
            "vertical": "apk",
            "type": etype,
            "date_status": "confirmed",
            "organizer_url": url,
            "source": "timepad",
            "description": desc[:200],
        }
    ev["vertical"] = "apk"
    ev["type"] = etype
    return ev


def fetch_keyword(token: str, keyword: str, limit_per_page: int = 100) -> list[dict]:
    out: list[dict] = []
    skip = 0
    total = None
    pages = 0
    while True:
        try:
            _st, data = api_get(
                token,
                {
                    "limit": limit_per_page,
                    "skip": skip,
                    "keywords": keyword,
                    "starts_at_min": TODAY.isoformat(),
                    "starts_at_max": "2027-12-31",
                    "sort": "+starts_at",
                    "fields": ["location", "description_short", "organization"],
                },
            )
        except RuntimeError as e:
            if "429" in str(e):
                time.sleep(5.0)
                _st, data = api_get(
                    token,
                    {
                        "limit": limit_per_page,
                        "skip": skip,
                        "keywords": keyword,
                        "starts_at_min": TODAY.isoformat(),
                        "starts_at_max": "2027-12-31",
                        "sort": "+starts_at",
                        "fields": ["location", "description_short", "organization"],
                    },
                )
            else:
                raise
        if total is None:
            total = int(data.get("total") or 0)
        vals = data.get("values") or []
        if not vals:
            break
        out.extend(vals)
        skip += len(vals)
        pages += 1
        if skip >= total or len(vals) < limit_per_page or pages >= 15:
            break
        time.sleep(1.2)
    return out, total if total is not None else len(out)


def count_apk_formats(events: list[dict]) -> tuple[int, int, int]:
    apk = [e for e in events if (e.get("vertical") or "") == "apk"]
    bf = sum(1 for e in apk if (e.get("type") or "").lower() == "бизнес-завтрак")
    st = sum(1 for e in apk if (e.get("type") or "").lower() == "круглый стол")
    return len(apk), bf, st


def force_apk_on_events(evs: list[dict]) -> list[dict]:
    out = []
    for e in evs:
        e = dict(e)
        e["vertical"] = "apk"
        out.append(e)
    return out


def ingest_allevents_apk() -> list[dict]:
    collected: list[dict] = []
    by_url: dict[str, dict] = {}

    def take(evs: list[dict], etype: Optional[str] = None) -> int:
        n = 0
        for e in evs:
            e = dict(e)
            e["vertical"] = "apk"
            if etype:
                e["type"] = etype
            if not prefer_cis(e):
                continue
            k = e.get("organizer_url") or e.get("title") or ""
            if k in by_url:
                # prefer typed soft formats
                old = by_url[k]
                if (old.get("type") or "") in {"Мероприятие", "Конференция", "Выставка"} and etype in {
                    "Бизнес-завтрак", "Круглый стол", "Бизнес-ужин"
                }:
                    by_url[k] = e
                continue
            by_url[k] = e
            n += 1
        return n

    # Theme-only pages
    for theme in AE_THEME_SLUGS:
        url = f"https://all-events.ru/events/calendar/theme-is-{theme}/"
        html, err = ae_fetch(url)
        time.sleep(0.5)
        if err or not html:
            walls.append(f"AE theme-is-{theme}: {err or 'empty'}")
            print(f"  WALL AE theme-is-{theme}: {err or 'empty'}", flush=True)
            continue
        (SAMPLES / f"ae_apk_theme-is-{theme}.html").write_text(html, encoding="utf-8")
        if page_is_generic(html):
            walls.append(f"AE theme-is-{theme}: generic title — empty/broken filter")
            print(f"  WALL AE theme-is-{theme}: generic", flush=True)
            continue
        # theme pages: classify type from title via parse then retag
        part = parse_ae_with_type(html, "Конференция")
        # retag types from titles
        retagged = []
        for e in part:
            e = dict(e)
            e["type"] = classify_type(e.get("title") or "")
            e["vertical"] = "apk"
            retagged.append(e)
        n = take(retagged)
        sid = upcoming_section_id(html)
        print(f"  AE theme-is-{theme}: parsed={len(part)} taken={n} sid={sid}", flush=True)
        stats[f"ae_theme_{theme}"] = len(part)
        # pagination
        if sid and not page_is_generic(html):
            for page in range(2, 6):
                lm = f"{url}?PAGEN_1={page}&load_more={sid}"
                h2, err2 = ae_fetch(lm)
                time.sleep(0.4)
                if err2 or not h2:
                    break
                part2 = parse_ae_with_type(h2, "Конференция")
                if not part2:
                    part2 = parse_ae_flex_cards(h2, "Конференция")
                for e in part2:
                    e["type"] = classify_type(e.get("title") or "")
                    e["vertical"] = "apk"
                nn = take(part2)
                print(f"    PAGEN_1={page}: +{nn}", flush=True)
                if nn == 0:
                    break

    # Type × theme intersections
    for tslug, etype, theme in AE_TYPE_THEME:
        url = f"https://all-events.ru/events/calendar/type-is-{tslug}/theme-is-{theme}/"
        html, err = ae_fetch(url)
        time.sleep(0.5)
        if err or not html:
            walls.append(f"AE {tslug}/{theme}: {err or 'empty'}")
            print(f"  WALL AE type-is-{tslug}/theme-is-{theme}: {err or 'empty'}", flush=True)
            continue
        safe = f"ae_apk_type-{tslug}_theme-{theme}.html"
        (SAMPLES / safe).write_text(html, encoding="utf-8")
        if page_is_generic(html):
            walls.append(f"AE type-is-{tslug}/theme-is-{theme}: generic — empty filter")
            print(f"  WALL AE type×theme {tslug}/{theme}: generic", flush=True)
            continue
        part = parse_ae_with_type(html, etype)
        for e in part:
            e["vertical"] = "apk"
            e["type"] = etype
        n = take(part, etype)
        print(f"  AE type-is-{tslug}/theme-is-{theme}: parsed={len(part)} taken={n}", flush=True)
        stats[f"ae_{tslug}_{theme}"] = len(part)

    return list(by_url.values())


def optional_html_sites() -> list[dict]:
    """Quick HTML pulls from milknews / foodsmi / agroinvestor if they parse."""
    out: list[dict] = []

    # milknews events calendar
    urls = [
        ("https://event.milknews.ru/", "milknews"),
        ("https://foodsmi.com/events", "foodsmi"),
        ("https://agroinvestor.ru/afisha/", "agroinvestor"),
        ("https://www.agroinvestor.ru/afisha/", "agroinvestor"),
    ]
    for url, source in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
            with urllib.request.urlopen(req, timeout=25) as r:
                html = r.read().decode("utf-8", errors="replace")
        except Exception as e:
            walls.append(f"{source} {url}: {type(e).__name__}: {e}")
            print(f"  WALL optional {source}: {type(e).__name__}", flush=True)
            continue
        (SAMPLES / f"opt_{source}.html").write_text(html[:500000], encoding="utf-8")
        found = 0
        # generic: DD.MM.YYYY + title link patterns
        for m in re.finditer(
            r'(\d{2}\.\d{2}\.20\d{2}).{0,200}?<a[^>]+href="([^"]+)"[^>]*>([^<]{8,160})</a>',
            html, re.S,
        ):
            d = parse_dot_date(m.group(1))
            if not d or d < TODAY:
                continue
            title = unescape(strip_tags(m.group(3))).strip()
            href = m.group(2)
            if href.startswith("/"):
                from urllib.parse import urljoin
                href = urljoin(url, href)
            etype = classify_type(title)
            ev = make_event(
                title, d, d, href, source, city="", etype=etype,
                extra_vert="агро пище сельхоз " + title,
            )
            if ev:
                ev["vertical"] = "apk"
                out.append(ev)
                found += 1
        # also look for roundtable/breakfast in plain text blocks with dates
        if found == 0:
            for m in re.finditer(
                r'<a[^>]+href="([^"]+)"[^>]*>([^<]*(?:завтрак|кругл\w*\s+стол|форум|конференц)[^<]*)</a>',
                html, re.I,
            ):
                title = unescape(strip_tags(m.group(2))).strip()
                href = m.group(1)
                if href.startswith("/"):
                    from urllib.parse import urljoin
                    href = urljoin(url, href)
                # date nearby
                nearby = html[max(0, m.start() - 400): m.end() + 200]
                dm = re.search(r"(\d{2}\.\d{2}\.20\d{2})", nearby)
                if not dm:
                    continue
                d = parse_dot_date(dm.group(1))
                if not d or d < TODAY:
                    continue
                etype = classify_type(title)
                ev = make_event(
                    title, d, d, href, source, city="", etype=etype,
                    extra_vert="агро " + title,
                )
                if ev:
                    ev["vertical"] = "apk"
                    out.append(ev)
                    found += 1
        print(f"  optional {source}: found={found}", flush=True)
        stats[f"opt_{source}"] = found
        time.sleep(0.4)
    return out


def ensure_gorodskoy_it(events: list[dict]) -> int:
    fixed = 0
    for e in events:
        t = (e.get("title") or "")
        if "городск" in t.lower() and "завтрак" in t.lower() and ("оон" in t.lower() or "ии" in t.lower() or "ai" in t.lower()):
            if e.get("vertical") != "it":
                e["vertical"] = "it"
                fixed += 1
    return fixed


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    existing = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    before_n = len(existing)
    before_apk, before_bf, before_st = count_apk_formats(existing)
    print(f"BEFORE N={before_n} apk={before_apk} breakfast={before_bf} stol={before_st}", flush=True)

    token = load_token()
    # Smoke
    try:
        st, smoke = api_get(token, {
            "limit": 1,
            "starts_at_min": TODAY.isoformat(),
            "starts_at_max": "2027-12-31",
            "sort": "+starts_at",
        })
        print(f"SMOKE HTTP={st} total_window={smoke.get('total')}", flush=True)
        stats["smoke_http"] = st
        stats["smoke_total"] = smoke.get("total")
    except Exception as e:
        walls.append(f"TimePad smoke: {e}")
        print(f"SMOKE FAIL: {e}", flush=True)
        raise SystemExit(1)
    time.sleep(1.2)

    by_id: dict[int, dict] = {}
    kw_totals: dict[str, int] = {}
    kw_api_totals: dict[str, int] = {}
    for kw in AGRO_KEYWORDS:
        try:
            rows, api_total = fetch_keyword(token, kw)
            kw_totals[kw] = len(rows)
            kw_api_totals[kw] = api_total
            for r in rows:
                rid = r.get("id")
                if rid is not None:
                    by_id[int(rid)] = r
            print(f"kw {kw!r}: fetched={len(rows)} api_total={api_total}", flush=True)
        except Exception as e:
            walls.append(f"TimePad kw {kw}: {type(e).__name__}: {e}")
            print(f"WALL kw {kw}: {e}", flush=True)
        time.sleep(1.2)

    stats["keyword_fetched"] = kw_totals
    stats["keyword_api_totals"] = kw_api_totals
    stats["unique_raw"] = len(by_id)

    if sum(kw_api_totals.values()) == 0 and len(by_id) == 0:
        walls.append(
            f"WALL TimePad agro keywords≈0 upcoming (starts_at_min={TODAY.isoformat()}). "
            f"api_totals={kw_api_totals}"
        )

    new_tp: list[dict] = []
    type_c: Counter = Counter()
    for raw in by_id.values():
        ev = event_from_tp_apk(raw)
        if not ev:
            continue
        new_tp.append(ev)
        type_c[ev.get("type") or ""] += 1
    stats["tp_classified"] = len(new_tp)
    stats["tp_types"] = dict(type_c)
    print(f"TimePad classified={len(new_tp)} types={dict(type_c)}", flush=True)
    print(f"  focus: breakfast={type_c.get('Бизнес-завтрак',0)} stol={type_c.get('Круглый стол',0)}", flush=True)

    print("\n=== All-Events APK themes ===", flush=True)
    new_ae = ingest_allevents_apk()
    ae_types = Counter(e.get("type") for e in new_ae)
    print(f"AE collected={len(new_ae)} types={dict(ae_types)}", flush=True)
    stats["ae_collected"] = len(new_ae)
    stats["ae_types"] = dict(ae_types)

    print("\n=== Optional agro HTML ===", flush=True)
    new_opt = optional_html_sites()
    stats["opt_collected"] = len(new_opt)

    # Upgrade existing soft matches (type/vertical) before merge
    soft_map = {}
    for e in existing:
        soft_map[((e.get("title") or "").strip().lower(), e.get("starts_at") or "")] = e

    upgraded = 0
    for ev in new_tp + new_ae + new_opt:
        k = ((ev.get("title") or "").strip().lower(), ev.get("starts_at") or "")
        old = soft_map.get(k)
        if not old:
            continue
        if (old.get("vertical") or "") != "apk" and ev.get("vertical") == "apk":
            # only force apk if agro signal in title/desc or from agro source
            blob = f"{old.get('title','')} {old.get('description','')} {ev.get('description','')}"
            if re.search(r"агро|сельхоз|пище|ферм|молоч|мясн|зерн|винодел|аквакультур|комбикорм", blob, re.I):
                old["vertical"] = "apk"
                upgraded += 1
        if (old.get("type") or "") in {"Мероприятие", "Прочее", "Конференция", "Выставка", ""}:
            if ev.get("type") in {"Бизнес-завтрак", "Круглый стол", "Бизнес-ужин", "Митап", "Семинар", "Вебинар"}:
                old["type"] = ev["type"]
                upgraded += 1
        if not is_real_summary(old.get("description"), old.get("title")):
            if is_real_summary(ev.get("description"), ev.get("title")):
                old["description"] = ev["description"]
                upgraded += 1

    existing = merge(existing, new_tp, "timepad_apk")
    existing = merge(existing, new_ae, "all_events_apk")
    existing = merge(existing, new_opt, "opt_apk")

    fixed_it = ensure_gorodskoy_it(existing)
    stats["gorodskoy_fixed"] = fixed_it
    stats["upgraded"] = upgraded

    meta = write_catalog(existing)
    after_n = len(existing)
    after_apk, after_bf, after_st = count_apk_formats(existing)

    # sample titles for report
    samples_bf = [
        f"{e.get('starts_at')} | {e.get('title','')[:70]}"
        for e in existing
        if e.get("vertical") == "apk" and (e.get("type") or "").lower() == "бизнес-завтрак"
    ][:12]
    samples_st = [
        f"{e.get('starts_at')} | {e.get('title','')[:70]}"
        for e in existing
        if e.get("vertical") == "apk" and (e.get("type") or "").lower() == "круглый стол"
    ][:12]

    bf_lines = ["  " + s for s in samples_bf] or ["  (none)"]
    st_lines = ["  " + s for s in samples_st] or ["  (none)"]
    wall_lines = ["  " + w for w in walls] or ["  (none)"]
    report_lines = [
        f"APK FORMATS INGEST REPORT {datetime.now().strftime('%Y-%m-%d %H:%M')} Europe/Moscow",
        f"BEFORE: N={before_n} apk={before_apk} АПК×Бизнес-завтрак={before_bf} АПК×Круглый стол={before_st}",
        f"AFTER:  N={after_n} apk={after_apk} АПК×Бизнес-завтрак={after_bf} АПК×Круглый стол={after_st}",
        f"DELTA:  N {after_n-before_n:+d} breakfast {after_bf-before_bf:+d} stol {after_st-before_st:+d}",
        f"SUCCESS target ≥5 (stretch 15): breakfast+stol = {after_bf + after_st}",
        f"TimePad smoke_http={stats.get('smoke_http')} unique_raw={stats.get('unique_raw')} classified={stats.get('tp_classified')}",
        f"TimePad keyword api_totals: {json.dumps(kw_api_totals, ensure_ascii=False)}",
        f"TimePad keyword fetched: {json.dumps(kw_totals, ensure_ascii=False)}",
        f"TimePad types: {json.dumps(stats.get('tp_types'), ensure_ascii=False)}",
        f"All-Events collected={stats.get('ae_collected')} types={json.dumps(stats.get('ae_types'), ensure_ascii=False)}",
        f"Optional collected={stats.get('opt_collected')}",
        f"upgraded={upgraded} gorodskoy_it_fixed={fixed_it}",
        f"write_catalog meta={meta}",
        "index.html NOT touched",
        "",
        "SAMPLES apk breakfast:",
        *bf_lines,
        "SAMPLES apk stol:",
        *st_lines,
        "",
        "WALLS:",
        *wall_lines,
    ]
    report = "\n".join(report_lines) + "\n"
    (SAMPLES / "ingest_report_apk_formats.txt").write_text(report, encoding="utf-8")
    (SAMPLES / "ingest_walls_apk_formats.txt").write_text(
        "\n".join(walls) or "(none)", encoding="utf-8"
    )
    # also dump new events json for parent
    (SAMPLES / "apk_formats_new_events.json").write_text(
        json.dumps({
            "timepad": new_tp[:200],
            "allevents": new_ae[:200],
            "optional": new_opt[:100],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + report, flush=True)
    if after_bf + after_st < 5:
        print("HONEST WALL: apk breakfast+stol still <5 after ingest", flush=True)
    elif after_bf + after_st < 15:
        print(f"OK ≥5 but below stretch 15: sum={after_bf+after_st}", flush=True)
    else:
        print(f"STRETCH OK: sum={after_bf+after_st}", flush=True)


if __name__ == "__main__":
    main()
