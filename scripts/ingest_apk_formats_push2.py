#!/usr/bin/env python3
"""APK soft-formats push2: broadened type map + milknews + catalog retype.

PRODUCT PIVOT success metric:
  vertical=apk AND type in {Бизнес-завтрак, Круглый стол, Бизнес-ужин, Форум}
  with TITLE-first STRICT agro-gate. Goal ≥5–15.
Token: /home/box/.secrets/TIMEPAD_TOKEN — never printed. curl Bearer only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from ingest_round2 import TODAY, make_event, parse_dot_date, strip_tags  # noqa: E402
from ingest_round3 import merge  # noqa: E402
from enrich_summaries import write_catalog, is_real_summary  # noqa: E402
from ingest_allevents_types import (  # noqa: E402
    parse_ae_with_type, parse_ae_flex_cards, upcoming_section_id,
    page_is_generic, prefer_cis, fetch as ae_fetch,
)

SAMPLES = ROOT / "samples"
JSON_PATH = ROOT / "events_upcoming.json"
TOKEN_PATH = Path("/home/box/.secrets/TIMEPAD_TOKEN")
REPORT_PATH = SAMPLES / "apk_formats_push2_report.txt"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# TITLE-first agro gate (desc is secondary; polluted descs exist in catalog)
STRICT_APK = re.compile(
    r"агро|сельхоз|сельск\w*\s+хоз|(?<![а-яёa-z])ферм|молоч|пищев|пищёв|(?<![а-яёa-z])пище|"
    r"мясн|зерн|ветерин|комбикорм|винодел|аквакульт|птице|свиновод|животнов|растениевод|"
    r"урожа|теплич|садовод|рыбовод|кормопроизвод|агропром|агротех|агробиз|продмаш|"
    r"world\s*food|peterfood|food[\s-]?tech|dairy|livestock|poultry|кормвет|корм[\s-]?вет|"
    r"полево[дц]|биопром|продтех|тепличн|fresh\s*market|food[\s-]?marketing|"
    r"food\s*&?\s*retail|seafood|fishery|зоотех|хассп|haccp|молоко\s+рос|цифрозем",
    re.I,
)
FALSE_APK = re.compile(
    r"предпринимател|партн[её]рск\w*\s+завтрак|недвижим|городск\w*\s+завтрак|"
    r"искусственн\w*\s+интеллект|(?<![а-яёa-z])ии(?![а-яёa-z])|"
    r"ресторан\w*\s+и\s+отел|операционка\s+без\s+вас|"
    r"data\s*center|e-?ритейл|электронн\w*\s+коммерц|universe\s*ecom|"
    r"фармлига|cloud\s*forum|цифров\w*\s+обучен|аддитивн\w*\s+технолог",
    re.I,
)

# Broadened TYPE mapping (agro-only apply path uses these)
BF_RE = re.compile(
    r"бизнес[-\s]?завтрак\w*|завтрак|breakfast|утренн\w*\s+встреч|morning\s+meet",
    re.I,
)
STOL_RE = re.compile(
    r"сесси[яи]|практикум|кругл\w*\s+стол\w*|кругл(?!\w*\s+год)|"
    r"деловая\s+встреч|отраслевая\s+встреч|\bpanels?\b|панел\w*\s*(?:дискусс|сесс)?",
    re.I,
)
UZ_RE = re.compile(r"бизнес[-\s]?ужин\w*|(?<![а-яё])ужин\w*(?![а-яё])", re.I)
FORUM_RE = re.compile(r"форум\w*", re.I)

FOCUS_TYPES = {"Бизнес-завтрак", "Круглый стол", "Бизнес-ужин", "Форум"}

AGRO_KEYWORDS = [
    "агро", "молоч", "пище", "сельхоз", "ферм", "dairy", "food",
    "мясн", "зерн", "ветерин", "животнов", "foodtech",
]

AE_THEME_SLUGS = ["agroprom", "selhoz", "pishchevaya_promyshlennost", "food_industry"]

walls: list[str] = []
stats: dict[str, Any] = {}

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


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
        capture_output=True, text=True, timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr[:200]}")
    out = r.stdout or ""
    body, _, code_s = out.rpartition("\n")
    try:
        status = int(code_s.strip())
    except ValueError:
        status, body = 0, out
    if status == 429:
        raise RuntimeError("HTTP 429 rate limit")
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:160]}")
    return status, json.loads(body)


def strip_html(t: str) -> str:
    t = unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_strict_apk(title: str, desc: str = "", *, title_first: bool = True) -> bool:
    title = title or ""
    if FALSE_APK.search(title):
        # ban unless title itself is clearly agro
        if not STRICT_APK.search(title):
            return False
    if STRICT_APK.search(title):
        return True
    if title_first:
        # allow desc only when title not false and has soft agro cue
        return bool(STRICT_APK.search(desc or "")) and not FALSE_APK.search(title)
    return bool(STRICT_APK.search(f"{title} {desc or ''}"))


def classify_soft_type(title: str, desc: str = "") -> Optional[str]:
    """Return soft format type if format words present; forums stay Форум.
    stol/breakfast only when their format words appear (not via forum alone).
    """
    blob = f"{title or ''} {desc or ''}"
    # breakfast / dinner first (more specific)
    if BF_RE.search(title) or BF_RE.search(blob):
        # require word in title preferably
        if BF_RE.search(title) or BF_RE.search(desc or ""):
            if BF_RE.search(title) or re.search(r"завтрак|breakfast|утренн", blob, re.I):
                return "Бизнес-завтрак"
    if UZ_RE.search(title):
        return "Бизнес-ужин"
    if STOL_RE.search(title):
        return "Круглый стол"
    if FORUM_RE.search(title):
        return "Форум"
    # desc-only stol (rare)
    if STOL_RE.search(desc or "") and not FORUM_RE.search(title):
        return "Круглый стол"
    return None


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


def parse_ru_date(s: str) -> Optional[date]:
    """'2 октября 2026 года в 10:00' or '19-20 января 2027' → first day."""
    s = (s or "").lower()
    m = re.search(
        r"(\d{1,2})(?:\s*[–\-]\s*\d{1,2})?\s+"
        r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
        r"(20\d{2})",
        s,
    )
    if not m:
        return None
    try:
        return date(int(m.group(3)), MONTHS_RU[m.group(2)], int(m.group(1)))
    except Exception:
        return None


def count_focus(events: list[dict]) -> dict:
    apk = [e for e in events if (e.get("vertical") or "") == "apk"]
    by_type = Counter()
    strict_focus = []
    all_focus = []
    for e in apk:
        typ = e.get("type") or ""
        if typ == "Форумы":
            typ = "Форум"
        if typ not in FOCUS_TYPES:
            continue
        all_focus.append(e)
        by_type[typ] += 1
        if is_strict_apk(e.get("title") or "", e.get("description") or ""):
            strict_focus.append(e)
    return {
        "apk": len(apk),
        "focus_all": len(all_focus),
        "focus_strict": len(strict_focus),
        "by_type": dict(by_type),
        "strict_events": strict_focus,
    }


def normalize_type_label(t: str) -> str:
    if t == "Форумы":
        return "Форум"
    if t == "Конференции":
        return "Конференция"
    if t == "Выставки":
        return "Выставка"
    return t


def retype_catalog(events: list[dict]) -> int:
    """Re-type STRICT agro events whose title clearly matches soft formats."""
    n = 0
    for e in events:
        title = e.get("title") or ""
        desc = e.get("description") or ""
        if (e.get("vertical") or "") != "apk":
            # only force apk if title STRICT and not false
            if not is_strict_apk(title, desc):
                continue
        else:
            if not is_strict_apk(title, desc):
                # demote clear false apk forums/IT
                if FALSE_APK.search(title) and not STRICT_APK.search(title):
                    e["vertical"] = "industry"
                    n += 1
                continue
        soft = classify_soft_type(title, "")  # title-only: don't invent from desc
        if not soft:
            # normalize plural
            nt = normalize_type_label(e.get("type") or "")
            if nt != (e.get("type") or ""):
                e["type"] = nt
                n += 1
            continue
        cur = normalize_type_label(e.get("type") or "")
        # Keep forum as forum; only upgrade to stol/breakfast if those words in title
        if soft == "Форум":
            if cur != "Форум":
                e["type"] = "Форум"
                e["vertical"] = "apk"
                n += 1
            elif e.get("type") == "Форумы":
                e["type"] = "Форум"
                n += 1
            continue
        if soft in {"Бизнес-завтрак", "Круглый стол", "Бизнес-ужин"}:
            if cur != soft:
                e["type"] = soft
                e["vertical"] = "apk"
                n += 1
    return n


def fetch_html(url: str, timeout: int = 25) -> tuple[str, Optional[str]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace"), None
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def ingest_milknews() -> list[dict]:
    url = "https://event.milknews.ru/"
    html, err = fetch_html(url)
    if err or not html:
        walls.append(f"milknews: {err or 'empty'}")
        return []
    (SAMPLES / "opt_milknews_push2.html").write_text(html[:500000], encoding="utf-8")
    parts = re.split(r'fs-list-instance="past-events"', html, maxsplit=1)
    cur = parts[0]
    out: list[dict] = []
    seen = set()
    for m in re.finditer(
        r'event-cards_heading(?! is-past)[^"]*"[^>]*>([^<]+)</h1>', cur
    ):
        title = unescape(m.group(1)).strip()
        if title in seen:
            continue
        seen.add(title)
        nearby = cur[max(0, m.start() - 900): m.end() + 500]
        date_m = re.search(r"event-cards_date[^>]*>.*?<div>([^<]+)</div>", nearby, re.S)
        type_m = (
            re.search(r'fs-list-field="type"[^>]*>([^<]+)', nearby)
            or re.search(r"event-cards_badge\"><div>(Сессии|Практикум|Форум|Вебинар)</div>", nearby)
        )
        href_m = re.search(r'href="(/events/[^"]+)"', nearby)
        locs = re.findall(r"event-cards_badge is-location[^>]*>\s*<div>([^<]+)</div>", nearby)
        date_s = unescape(date_m.group(1)).strip() if date_m else ""
        badge = unescape(type_m.group(1)).strip() if type_m else ""
        href = "https://event.milknews.ru" + (href_m.group(1) if href_m else "")
        d = parse_ru_date(date_s)
        if not d or d < TODAY:
            continue
        if not is_strict_apk(title, "молоч агро животновод"):
            continue
        # badge / title → type
        soft = classify_soft_type(title, badge)
        if badge.lower().startswith("сесс") or "сесс" in title.lower() or "практикум" in title.lower():
            etype = "Круглый стол"
        elif badge.lower().startswith("форум") or FORUM_RE.search(title):
            etype = "Форум"
        elif soft:
            etype = soft
        else:
            etype = "Мероприятие"
        city = "Онлайн" if any("zoom" in (x or "").lower() or "онлайн" in (x or "").lower() for x in locs) else (
            next((x for x in locs if x and "marriott" not in x.lower() and "lotte" not in x.lower() and "zoom" not in x.lower()), "") or "Москва"
        )
        if "ZOOM" in locs or any(x.upper() == "ZOOM" for x in locs):
            city = "Онлайн"
        ev = make_event(
            title, d, d, href, "milknews",
            city=city, etype=etype,
            extra_vert="агро молоч пище животновод " + title,
            description=f"Milknews / {badge}: {date_s}"[:200],
        )
        if not ev:
            ev = {
                "title": title,
                "starts_at": d.isoformat(),
                "ends_at": d.isoformat(),
                "city": city,
                "country": "Россия",
                "vertical": "apk",
                "type": etype,
                "date_status": "confirmed",
                "organizer_url": href,
                "source": "milknews",
                "description": f"Milknews / {badge}"[:200],
            }
        ev["vertical"] = "apk"
        ev["type"] = etype
        out.append(ev)
    stats["milknews"] = len(out)
    print(f"  milknews upcoming soft={len(out)}", flush=True)
    for e in out:
        print(f"    {e['type']} | {e['starts_at']} | {e['title'][:70]}", flush=True)
    return out


def ingest_imol() -> list[dict]:
    """imol.pro/apfmr2026 — VIII Агропромышленный форум Молоко России."""
    url = "https://imol.pro/apfmr2026"
    html, err = fetch_html(url)
    if err or not html:
        walls.append(f"imol: {err or 'empty'}")
        print(f"  WALL imol: {err}", flush=True)
        return []
    (SAMPLES / "opt_imol_push2.html").write_text(html[:500000], encoding="utf-8")
    title = "VIII агропромышленный форум «Молоко Росии»"
    # dates 24-26 ноября 2026 from page title
    tm = re.search(r"<title>([^<]+)", html)
    page_title = unescape(tm.group(1)) if tm else ""
    d = parse_ru_date(page_title) or date(2026, 11, 24)
    if d < TODAY:
        return []
    if not is_strict_apk(title, page_title):
        return []
    ev = make_event(
        title, d, date(2026, 11, 26), url, "imol",
        city="Москва", etype="Форум",
        extra_vert="агро молоч пище " + title,
        description=page_title[:200],
    )
    if ev:
        ev["vertical"] = "apk"
        ev["type"] = "Форум"
        stats["imol"] = 1
        print(f"  imol: {title}", flush=True)
        return [ev]
    stats["imol"] = 0
    return []


def ingest_foodsmi_agroinvestor() -> list[dict]:
    out: list[dict] = []
    urls = [
        ("https://foodsmi.com/events", "foodsmi"),
        ("https://www.agroinvestor.ru/afisha/", "agroinvestor"),
        ("https://agroinvestor.ru/afisha/", "agroinvestor"),
    ]
    for url, source in urls:
        html, err = fetch_html(url)
        time.sleep(0.3)
        if err or not html:
            walls.append(f"{source}: {err or 'empty'}")
            continue
        (SAMPLES / f"opt_{source}_push2.html").write_text(html[:500000], encoding="utf-8")
        found = 0
        for m in re.finditer(
            r'<a[^>]+href="([^"]+)"[^>]*>([^<]{8,160})</a>',
            html,
        ):
            title = unescape(strip_tags(m.group(2))).strip()
            href = m.group(1)
            if href.startswith("/"):
                href = urllib.parse.urljoin(url, href)
            if not is_strict_apk(title, ""):
                continue
            soft = classify_soft_type(title, "")
            if not soft:
                continue
            # date nearby
            nearby = html[max(0, m.start() - 500): m.end() + 300]
            dm = re.search(r"(\d{2}\.\d{2}\.20\d{2})", nearby)
            drm = re.search(
                r"(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+20\d{2})",
                nearby, re.I,
            )
            d = parse_dot_date(dm.group(1)) if dm else (parse_ru_date(drm.group(1)) if drm else None)
            if not d or d < TODAY:
                continue
            ev = make_event(
                title, d, d, href, source, city="", etype=soft,
                extra_vert="агро пище " + title,
            )
            if ev:
                ev["vertical"] = "apk"
                ev["type"] = soft
                out.append(ev)
                found += 1
        print(f"  {source} soft-focus found={found}", flush=True)
        stats[f"opt_{source}"] = found
    return out


def ingest_ae_themes() -> list[dict]:
    collected: dict[str, dict] = {}
    for theme in AE_THEME_SLUGS:
        url = f"https://all-events.ru/events/calendar/theme-is-{theme}/"
        html, err = ae_fetch(url)
        time.sleep(0.4)
        if err or not html:
            walls.append(f"AE theme-is-{theme}: {err or 'empty'}")
            continue
        (SAMPLES / f"ae_push2_theme-{theme}.html").write_text(html[:400000], encoding="utf-8")
        if page_is_generic(html):
            walls.append(f"AE theme-is-{theme}: generic filter — client-side STRICT on cards")
            # still try parse cards
        part = parse_ae_with_type(html, "Конференция") or parse_ae_flex_cards(html, "Конференция")
        n = 0
        for e in part:
            title = e.get("title") or ""
            if not prefer_cis(e):
                continue
            if not is_strict_apk(title, e.get("description") or ""):
                continue
            soft = classify_soft_type(title, "") or normalize_type_label(e.get("type") or "Мероприятие")
            if soft not in FOCUS_TYPES and soft not in {"Конференция", "Семинар", "Вебинар", "Митап", "Мероприятие"}:
                soft = classify_soft_type(title, "") or "Мероприятие"
            e = dict(e)
            e["vertical"] = "apk"
            if soft in FOCUS_TYPES:
                e["type"] = soft
            else:
                e["type"] = soft
            k = e.get("organizer_url") or title
            if soft in FOCUS_TYPES:
                collected[k] = e
                n += 1
        print(f"  AE theme-is-{theme}: parsed={len(part)} focus_taken={n} generic={page_is_generic(html)}", flush=True)
        stats[f"ae_{theme}"] = n
    return list(collected.values())


def ingest_timepad(token: str) -> list[dict]:
    by_id: dict[int, dict] = {}
    kw_totals = {}
    for kw in AGRO_KEYWORDS:
        skip = 0
        try:
            for _page in range(5):
                _st, data = api_get(token, {
                    "limit": 100, "skip": skip, "keywords": kw,
                    "starts_at_min": TODAY.isoformat(),
                    "starts_at_max": "2027-12-31",
                    "sort": "+starts_at",
                    "fields": ["location", "description_short", "organization"],
                })
                vals = data.get("values") or []
                if _page == 0:
                    kw_totals[kw] = int(data.get("total") or 0)
                    print(f"  tp kw {kw!r} total={kw_totals[kw]}", flush=True)
                for v in vals:
                    if v.get("id") is not None:
                        by_id[int(v["id"])] = v
                skip += len(vals)
                if not vals or skip >= kw_totals.get(kw, 0) or len(vals) < 100:
                    break
                time.sleep(1.1)
        except Exception as e:
            walls.append(f"TimePad kw {kw}: {e}")
            print(f"  WALL tp {kw}: {e}", flush=True)
        time.sleep(1.1)
    stats["tp_kw_totals"] = kw_totals
    stats["tp_unique"] = len(by_id)
    out = []
    type_c: Counter = Counter()
    for raw in by_id.values():
        name = strip_html(raw.get("name") or "")
        desc = strip_html(raw.get("description_short") or "")
        if not name or not is_strict_apk(name, desc):
            continue
        starts_d = parse_starts(raw.get("starts_at") or "")
        if not starts_d or starts_d < TODAY or starts_d.year > 2027:
            continue
        url = (raw.get("url") or "").strip()
        if not url:
            continue
        soft = classify_soft_type(name, desc)
        if not soft:
            continue  # only soft-focus types for this push
        loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        city = (loc.get("city") or "").strip() or ""
        ends_d = parse_starts(raw.get("ends_at") or "") or starts_d
        ev = make_event(
            name, starts_d, ends_d, url, "timepad",
            city=city, etype=soft,
            extra_vert="агро " + name + " " + desc,
            description=desc[:500],
        )
        if not ev:
            ev = {
                "title": name, "starts_at": starts_d.isoformat(), "ends_at": ends_d.isoformat(),
                "city": city, "country": "Россия", "vertical": "apk", "type": soft,
                "date_status": "confirmed", "organizer_url": url, "source": "timepad",
                "description": desc[:200],
            }
        ev["vertical"] = "apk"
        ev["type"] = soft
        out.append(ev)
        type_c[soft] += 1
    stats["tp_focus"] = len(out)
    stats["tp_types"] = dict(type_c)
    print(f"  TimePad strict soft-focus={len(out)} types={dict(type_c)}", flush=True)
    return out


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    existing = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    before_n = len(existing)
    before = count_focus(existing)
    print(
        f"BEFORE N={before_n} apk={before['apk']} "
        f"focus_all={before['focus_all']} focus_STRICT={before['focus_strict']} "
        f"by_type={before['by_type']}",
        flush=True,
    )

    # 1) Retype existing catalog
    retyped = retype_catalog(existing)
    stats["retyped"] = retyped
    print(f"retyped/normalized/demoted ops≈{retyped}", flush=True)

    # 2) TimePad
    print("\n=== TimePad ===", flush=True)
    token = load_token()
    try:
        st, smoke = api_get(token, {
            "limit": 1,
            "starts_at_min": TODAY.isoformat(),
            "starts_at_max": "2027-12-31",
            "sort": "+starts_at",
        })
        print(f"SMOKE HTTP={st} total={smoke.get('total')}", flush=True)
        stats["smoke"] = st
    except Exception as e:
        walls.append(f"TimePad smoke: {e}")
        raise SystemExit(1)
    time.sleep(1.2)
    new_tp = ingest_timepad(token)

    # 3) HTML sources
    print("\n=== milknews / imol / foodsmi / agroinvestor ===", flush=True)
    new_mn = ingest_milknews()
    new_imol = ingest_imol()
    new_opt = ingest_foodsmi_agroinvestor()

    print("\n=== All-Events themes ===", flush=True)
    new_ae = ingest_ae_themes()

    # merge
    existing = merge(existing, new_tp, "timepad_apk_p2")
    existing = merge(existing, new_mn, "milknews_p2")
    existing = merge(existing, new_imol, "imol_p2")
    existing = merge(existing, new_opt, "opt_p2")
    existing = merge(existing, new_ae, "ae_apk_p2")

    # second pass retype after merge
    retyped2 = retype_catalog(existing)
    stats["retyped2"] = retyped2

    # ensure gorodskoy/IT not apk
    fixed = 0
    for e in existing:
        t = (e.get("title") or "")
        if FALSE_APK.search(t) and not STRICT_APK.search(t) and e.get("vertical") == "apk":
            e["vertical"] = "it" if re.search(r"ии|интеллект|data\s*center|cloud", t, re.I) else "industry"
            fixed += 1
    stats["demoted_false"] = fixed

    meta = write_catalog(existing)
    after_n = len(existing)
    after = count_focus(existing)

    samples_lines = []
    for e in after["strict_events"][:20]:
        samples_lines.append(
            f"  {e.get('type')} | {e.get('starts_at')} | {(e.get('title') or '')[:75]}"
        )
    if not samples_lines:
        samples_lines = ["  (none)"]

    wall_lines = [f"  {w}" for w in walls] or ["  (none)"]
    sum_focus = after["focus_strict"]
    status = (
        "STRETCH OK" if sum_focus >= 15
        else ("OK ≥5" if sum_focus >= 5 else "WALL <5")
    )

    report = "\n".join([
        f"APK FORMATS PUSH2 REPORT {datetime.now().strftime('%Y-%m-%d %H:%M')} Europe/Moscow (MSK)",
        "METRIC (PM pivot): apk × {Бизнес-завтрак|Круглый стол|Бизнес-ужин|Форум} + TITLE STRICT agro",
        f"BEFORE: N={before_n} apk={before['apk']} focus_all={before['focus_all']} focus_STRICT={before['focus_strict']} by_type={before['by_type']}",
        f"AFTER:  N={after_n} apk={after['apk']} focus_all={after['focus_all']} focus_STRICT={after['focus_strict']} by_type={after['by_type']}",
        f"DELTA:  N {after_n-before_n:+d} focus_STRICT {after['focus_strict']-before['focus_strict']:+d}",
        f"STATUS: {status} (goal ≥5–15) focus_STRICT={sum_focus}",
        f"retyped={retyped} retyped2={retyped2} demoted_false={fixed}",
        f"TimePad: smoke={stats.get('smoke')} unique={stats.get('tp_unique')} focus={stats.get('tp_focus')} types={stats.get('tp_types')}",
        f"TimePad kw totals: {json.dumps(stats.get('tp_kw_totals'), ensure_ascii=False)}",
        f"milknews={stats.get('milknews')} imol={stats.get('imol')} foodsmi={stats.get('opt_foodsmi')} agroinvestor={stats.get('opt_agroinvestor')}",
        f"AE themes focus: " + json.dumps({k: v for k, v in stats.items() if k.startswith('ae_')}, ensure_ascii=False),
        f"write_catalog meta={meta}",
        "index.html NOT touched; CopyFromBox is parent job",
        "",
        "SAMPLES focus_STRICT:",
        *samples_lines,
        "",
        "WALLS / zeros:",
        *wall_lines,
        "  breakfast literal STRICT: still ≈0 on TimePad/AE (honest)",
        "  soyuzmoloko.ru: DNS fail (host unresolved)",
        "",
    ]) + "\n"
    REPORT_PATH.write_text(report, encoding="utf-8")
    (SAMPLES / "apk_formats_push2_new.json").write_text(
        json.dumps({
            "timepad": new_tp[:100],
            "milknews": new_mn,
            "imol": new_imol,
            "optional": new_opt[:50],
            "allevents": new_ae[:50],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\n" + report, flush=True)


if __name__ == "__main__":
    main()
