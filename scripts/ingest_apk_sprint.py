#!/usr/bin/env python3
"""APK growth sprint: tsenovik Russia-2026 block, agroinvestor+ICS, agrozentr,
foodsmi HTML/RSS/details, zivot seed, flagship enrich. SKIP_PW=1 (no Playwright)."""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import unquote
import urllib.request

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

# SKIP_PW no longer defaulted; set SKIP_PW=1 explicitly to disable Playwright

from ingest_round2 import (  # noqa: E402
    TODAY, APK_RE, IT_RE, UA, strip_tags, vertical_for, make_event,
    parse_ru_date_range, parse_agrozentr, parse_zivot_seed,
)
from ingest_round3 import dedupe_key, parse_zivot_full  # noqa: E402
from ingest_round4 import parse_foodsmi  # noqa: E402
from ingest_round5 import parse_agroinvestor  # noqa: E402

SOFT_APK_RE = re.compile(
    r"агро|сельхоз|сельск|пищев|пищ[её]|food\b|horeca|ресторан|напит|выпечк|зож|"
    r"рыбн|мясн|молоч|зерн|ферм|урожа|птице|ветерин|комбикорм|seafood|fishery|"
    r"ритейл|retail|охот|рыбац|пропротеин|кормвет|worldfood|агропрод|югагро|"
    r"интекпром|megustro|bioprom|биопром|готово[йя]\s+ед|санитарн|cleanexpo|"
    r"anfash|anfa[şs]|пахот|полевод|пестицид|агрорус|маслож|растениевод|"
    r"агрохолдинг|день\s+поля|нива|аграрн|корм\w*\s*вет|минводыагро|агроволга|"
    r"feedvet|sibagro|agravia|iagri|interagromash|интерагромаш|прокрахмал|"
    r"digital4food|пищёвк|пищевк",
    re.I,
)


def fetch_url(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json,text/calendar,*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_text(url: str, timeout: int = 40) -> str:
    data = fetch_url(url, timeout)
    for e in ("utf-8", "cp1251"):
        try:
            return data.decode(e)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def load_existing() -> list[dict]:
    path = ROOT / "events_upcoming.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def merge_events(existing: list[dict], new_events: list[dict]) -> tuple[list[dict], int]:
    seen = {dedupe_key(e) for e in existing}
    url_title = {
        ((e.get("organizer_url") or "").rstrip("/").lower(), (e.get("title") or "").strip().lower())
        for e in existing
    }
    soft = {((e.get("title") or "").strip().lower(), e.get("starts_at") or "") for e in existing}
    added = 0
    for e in new_events:
        k = dedupe_key(e)
        ut = ((e.get("organizer_url") or "").rstrip("/").lower(), (e.get("title") or "").strip().lower())
        sk = ((e.get("title") or "").strip().lower(), e.get("starts_at") or "")
        if k in seen or ut in url_title or sk in soft:
            continue
        existing.append(e)
        seen.add(k)
        url_title.add(ut)
        soft.add(sk)
        added += 1
    return existing, added


def unwrap_bitrix(url: str) -> str:
    m = re.search(r"[?&]url=([^&]+)", url)
    if m:
        return unquote(m.group(1))
    return url


# ---------------------------------------------------------------------------
# 1. Tsenovik — block «Выставки России 2026» (NOT NewsCalNews grid)
# ---------------------------------------------------------------------------
def extract_tsenovik_russia_2026_table(html: str) -> str:
    idx = html.find("Выставки России 2026")
    if idx < 0:
        # try without year
        idx = html.find("Выставки России")
    if idx < 0:
        return ""
    chunk = html[idx:]
    # stop at next h2 after the heading
    nxt = re.search(r"<h2[\s>]", chunk[80:], re.I)
    if nxt:
        chunk = chunk[: 80 + nxt.start()]
    tm = re.search(r"<table[^>]*>(.*?)</table>", chunk, re.S | re.I)
    if not tm:
        return ""
    return "<table>" + tm.group(1) + "</table>"


def parse_tsenovik_russia_2026(html: str, source: str = "tsenovik") -> list[dict]:
    """Parse dated rows under heading «Выставки России 2026»."""
    table = extract_tsenovik_russia_2026_table(html)
    if not table:
        return []
    out: list[dict] = []
    for tr in re.findall(r"<tr>(.*?)</tr>", table, re.S | re.I):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        if len(cells) < 2:
            continue
        date_s = strip_tags(cells[0])
        title = strip_tags(cells[1])
        if not re.search(r"\d", date_s):
            continue
        if len(title) < 4:
            continue
        if title.lower() in {"название", "мероприятие"} or "мероп" in date_s.lower():
            continue
        city_raw = strip_tags(cells[2]) if len(cells) > 2 else ""
        city = city_raw.split(",")[0].replace("г.", "").strip()
        # contacts cell may hold link
        links = re.findall(r'href="(https?://[^"]+)"', tr)
        url = unwrap_bitrix(links[0]) if links else "https://www.tsenovik.ru/vystavki/"
        # force year 2026 for this named block (fix ЮгАгро 2025 typo → 2026)
        year = 2026
        ym = re.search(r"20(\d{2})", title)
        if ym and int("20" + ym.group(1)) >= 2027:
            year = int("20" + ym.group(1))
        rng = parse_ru_date_range(date_s + f" {year}", default_year=year)
        if not rng:
            rng = parse_ru_date_range(date_s, default_year=year)
        if not rng:
            continue
        starts, ends = rng
        # rewrite past-year label in 2026 block
        if "2025" in title and starts.year == 2026:
            title = re.sub(r"2025", "2026", title)
        desc = f"{title}. {city_raw}".strip()[:200]
        ev = make_event(
            title, starts, ends, url, source,
            city=city[:60], extra_vert="агро сельхоз выставка россии",
            etype="Выставка", description=desc,
        )
        if ev:
            ev["vertical"] = "apk"
            out.append(ev)
    return out


def ingest_tsenovik() -> list[dict]:
    html = ""
    live = SAMPLES / "tsenovik_2026_block.html"
    try:
        html = fetch_text("https://www.tsenovik.ru/vystavki/?month=01&year=2026")
        live.write_text(html, encoding="utf-8")
    except Exception as e:
        print(f"  tsenovik live: {e}")
        if live.exists():
            html = live.read_text(encoding="utf-8", errors="replace")
        else:
            for name in ("tsenovik_vystavki_live.html", "tsenovik.html", "apk_tsenovik_vyst.html"):
                p = SAMPLES / name
                if p.exists():
                    html = p.read_text(encoding="utf-8", errors="replace")
                    break
    if not html:
        return []
    return parse_tsenovik_russia_2026(html)


# ---------------------------------------------------------------------------
# 2. Agroinvestor afisha + calendar.ics DESCRIPTION → summary
# ---------------------------------------------------------------------------
def unfold_ics_text(s: str) -> str:
    """Unfold ICS folded lines and unescape."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\n[ \t]", "", s)
    s = s.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";")
    return s


def parse_ics_events(text: str, fallback_url: str = "", source: str = "agroinvestor") -> list[dict]:
    text = unfold_ics_text(text)
    out: list[dict] = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        def field(name: str) -> str:
            m = re.search(rf"^{name}[;:](.*)$", block, re.M)
            return (m.group(1).strip() if m else "")

        summary = field("SUMMARY")
        desc = field("DESCRIPTION")
        loc = field("LOCATION")
        url = field("URL") or fallback_url
        url = re.sub(r"^http://https://", "https://", url)
        # DTSTART / DTEND — DATE or DATETIME
        ds = re.search(r"^DTSTART[^:]*:(\d{8})", block, re.M)
        de = re.search(r"^DTEND[^:]*:(\d{8})", block, re.M)
        if not ds:
            continue
        try:
            starts = date(int(ds.group(1)[:4]), int(ds.group(1)[4:6]), int(ds.group(1)[6:8]))
            if de:
                ends = date(int(de.group(1)[:4]), int(de.group(1)[4:6]), int(de.group(1)[6:8]))
                # all-day DTEND is exclusive in ICS
                if "VALUE=DATE" in block and ends > starts:
                    from datetime import timedelta
                    ends = ends - timedelta(days=1)
            else:
                ends = starts
        except ValueError:
            continue
        city = loc.split(",")[0].split(";")[0].strip() if loc else ""
        ev = make_event(
            summary, starts, ends, url or "https://www.agroinvestor.ru/afisha/",
            source, city=city, extra_vert="агро " + desc,
            etype="Мероприятие", description=desc[:300],
        )
        if ev:
            ev["vertical"] = "apk"
            # keep fuller description for drawer (make_event truncates to 200)
            if desc:
                ev["description"] = desc[:400]
            out.append(ev)
    return out


def parse_ai_dot_range(s: str) -> Optional[tuple[date, date]]:
    s = s.replace("—", "-").replace("–", "-")
    m = re.search(
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+\d{1,2}:\d{2})?\s*-\s*"
        r"(?:(\d{1,2})\.(\d{1,2})\.(\d{4})|(\d{1,2}):(\d{2}))",
        s,
    )
    if m:
        d1, mo1, y1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            starts = date(y1, mo1, d1)
        except ValueError:
            return None
        if m.group(4):
            try:
                ends = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
            except ValueError:
                ends = starts
        else:
            ends = starts
        return starts, ends
    m2 = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m2:
        try:
            d = date(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
            return d, d
        except ValueError:
            return None
    return None


def parse_agroinvestor_cards(html: str, source: str = "agroinvestor") -> list[dict]:
    """Parse listing + any /afisha/2026/... cards embedded."""
    out = parse_agroinvestor(html, source)
    # also scrape card HTML files later
    return out


def parse_agroinvestor_card_html(html: str, page_url: str, source: str = "agroinvestor") -> list[dict]:
    out: list[dict] = []
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if not title_m:
        return out
    title = strip_tags(title_m.group(1))
    date_s = re.search(r'event-card__details-item date">([^<]+)', html)
    place = re.search(r'event-card__details-item place">([^<]+)', html)
    typ = re.search(r'event-card__details-item type">([^<]+)', html)
    if not date_s:
        return out
    rng = parse_ai_dot_range(unescape(date_s.group(1)))
    if not rng:
        return out
    city = unescape(place.group(1)).split(",")[0].strip() if place else ""
    et = typ.group(1).strip() if typ else "Мероприятие"
    # description from meta or body lead
    desc = ""
    md = re.search(r'<meta name="description" content="([^"]+)"', html)
    if md:
        desc = unescape(md.group(1))
    # ICS link
    ics = re.search(r'href="(/afisha/202[67]/[^"]+calendar\.ics)"', html)
    ev = make_event(
        title, rng[0], rng[1], page_url, source,
        city=city, etype=et, extra_vert="агро " + title + " " + desc,
        description=desc[:300],
    )
    if ev:
        ev["vertical"] = "apk"
        if desc:
            ev["description"] = desc[:400]
        out.append(ev)
    if ics:
        ics_url = "https://www.agroinvestor.ru" + ics.group(1)
        try:
            ics_text = fetch_text(ics_url, timeout=25)
            slug = ics.group(1).strip("/").replace("/", "_")[:80]
            (SAMPLES / f"ai_{slug}.ics").write_text(ics_text, encoding="utf-8")
            for ie in parse_ics_events(ics_text, page_url, source):
                # prefer ICS description
                if ie.get("description"):
                    if out:
                        out[0]["description"] = ie["description"]
                    else:
                        out.append(ie)
                elif not out:
                    out.append(ie)
        except Exception as e:
            print(f"  ics fail {ics_url}: {e}")
    return out


def ingest_agroinvestor() -> list[dict]:
    events: list[dict] = []
    html = ""
    live = SAMPLES / "agroinvestor_afisha_live.html"
    try:
        html = fetch_text("https://agroinvestor.ru/afisha/")
        live.write_text(html, encoding="utf-8")
    except Exception as e:
        print(f"  agroinvestor live: {e}")
        if live.exists():
            html = live.read_text(encoding="utf-8", errors="replace")
    if html:
        events.extend(parse_agroinvestor(html, "agroinvestor"))
        # force apk for agroinvestor listing
        for e in events:
            e["vertical"] = "apk"

    # card pages (cached from sprint fetch)
    for p in sorted(SAMPLES.glob("ai_card_*.html")):
        slug = p.name.replace("ai_card_", "").replace(".html", "")
        url = f"https://www.agroinvestor.ru/afisha/2026/{slug}/"
        # try to recover real path from href inside
        raw = p.read_text(encoding="utf-8", errors="replace")
        hm = re.search(r'canonical" href="(https://[^"]+/afisha/202[67]/[^"]+/)"', raw)
        if hm:
            url = hm.group(1)
        part = parse_agroinvestor_card_html(raw, url)
        events.extend(part)

    # cached ICS files
    for p in SAMPLES.glob("ai_*.ics*"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if "BEGIN:VEVENT" not in text:
                continue
            events.extend(parse_ics_events(text, source="agroinvestor"))
        except Exception:
            continue

    # dedupe
    seen = set()
    uniq = []
    for e in events:
        e["vertical"] = "apk"
        k = dedupe_key(e)
        if k in seen:
            # keep longer description
            for u in uniq:
                if dedupe_key(u) == k:
                    if len(e.get("description") or "") > len(u.get("description") or ""):
                        u["description"] = e["description"]
                    break
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# ---------------------------------------------------------------------------
# 3. Agrozentr calendar table
# ---------------------------------------------------------------------------
def parse_agrozentr_improved(html: str, source: str = "agrozentr") -> list[dict]:
    out = parse_agrozentr(html, source)
    # also plain table rows with YYYY in date cell
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        if "month-row" in tr:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        if len(cells) < 2:
            continue
        date_s = strip_tags(cells[0])
        if not re.search(r"\d{1,2}", date_s):
            continue
        if not re.search(
            r"январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр",
            date_s, re.I,
        ):
            continue
        bm = re.search(r"<b>([^<]+)</b>", cells[1])
        title = bm.group(1).strip() if bm else strip_tags(re.sub(r"<img[^>]*>", "", cells[1]))
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 3 or title.lower() in {"название", "событие"}:
            continue
        yr = 2026
        ym = re.search(r"(202[6-9])", date_s)
        if ym:
            yr = int(ym.group(1))
        rng = parse_ru_date_range(date_s, yr)
        if not rng:
            continue
        href = re.search(r'href="(https?://[^"]+)"', tr)
        # site link often in cell 2 as plain text domain
        url = href.group(1) if href else "https://www.agrozentr.ru/info/kalendar-meropriyatiy/"
        if not href and len(cells) > 2:
            dom = strip_tags(cells[2]).strip()
            if re.match(r"[\w.-]+\.\w{2,}", dom) and " " not in dom:
                url = "https://" + dom if not dom.startswith("http") else dom
        city = ""
        if len(cells) > 3:
            city = strip_tags(cells[3]).split(",")[0].replace("г.", "").strip()[:60]
        elif len(cells) > 2 and "http" not in strip_tags(cells[2]).lower() and "." not in strip_tags(cells[2])[:15]:
            city = strip_tags(cells[2])[:60]
        ev = make_event(
            title, rng[0], rng[1], url, source,
            city=city, extra_vert="агро", etype="Выставка",
            description=f"{title}. {strip_tags(cells[3]) if len(cells)>3 else ''}"[:200],
        )
        if ev:
            ev["vertical"] = "apk"
            out.append(ev)
    seen = set()
    uniq = []
    for e in out:
        e["vertical"] = "apk"
        k = (e["title"].strip().lower(), e.get("starts_at"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def ingest_agrozentr() -> list[dict]:
    html = ""
    live = SAMPLES / "agrozentr_kalendar_live.html"
    try:
        html = fetch_text("https://www.agrozentr.ru/info/kalendar-meropriyatiy/")
        live.write_text(html, encoding="utf-8")
    except Exception as e:
        print(f"  agrozentr live: {e}")
        for name in ("agrozentr_kalendar_live.html", "agrozentr.html"):
            p = SAMPLES / name
            if p.exists():
                html = p.read_text(encoding="utf-8", errors="replace")
                break
    if not html:
        return []
    return parse_agrozentr_improved(html)


# ---------------------------------------------------------------------------
# 4. Foodsmi HTML + RSS + detail pages — force vertical apk
# ---------------------------------------------------------------------------
def parse_foodsmi_rss(xml: str, source: str = "foodsmi") -> list[dict]:
    """RSS has title/link/description; dates often only in linked pages / description text."""
    out: list[dict] = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        title_m = re.search(r"<title>(.*?)</title>", it, re.S)
        link_m = re.search(r"<link>(.*?)</link>", it)
        desc_m = re.search(r"<description>(.*?)</description>", it, re.S)
        if not (title_m and link_m):
            continue
        raw_title = unescape(re.sub(r"<[^>]+>", "", title_m.group(1))).replace("&amp;", "&")
        url = link_m.group(1).strip()
        desc = unescape(re.sub(r"<[^>]+>", " ", desc_m.group(1) if desc_m else ""))
        desc = re.sub(r"\s+", " ", desc).strip()
        city = ""
        title = raw_title
        if "," in raw_title:
            left, right = raw_title.split(",", 1)
            if len(left) < 45:
                city = left.strip()
                title = right.strip()
        # try date from description
        rng = None
        for pat in [
            r"[Сс]\s+(\d{1,2})\s+по\s+(\d{1,2})\s+([а-яё]+)\s+(\d{4})",
            r"(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+\d{4})",
            r"(\d{1,2}\s+[а-яё]+\s+\d{4})",
            r"состоится\s+(\d{1,2}\s+[а-яё]+\s+\d{4})",
        ]:
            m = re.search(pat, desc, re.I)
            if not m:
                continue
            if len(m.groups()) == 4:
                rng = parse_ru_date_range(
                    f"{m.group(1)}-{m.group(2)} {m.group(3)} {m.group(4)}", int(m.group(4))
                )
            else:
                rng = parse_ru_date_range(m.group(1), 2026)
            if rng:
                break
        # skip past-dated webinars without parseable future date
        if not rng:
            # try slug date 13-oktyabrya-2026
            sm = re.search(
                r"(\d{1,2})-(yanvarya|fevralya|marta|aprelya|maya|iyunya|iyulya|"
                r"avgusta|sentyabrya|oktyabrya|noyabrya|dekabrya)-(\d{4})",
                url, re.I,
            )
            MONTH_SLUG = {
                "yanvarya": 1, "fevralya": 2, "marta": 3, "aprelya": 4, "maya": 5,
                "iyunya": 6, "iyulya": 7, "avgusta": 8, "sentyabrya": 9,
                "oktyabrya": 10, "noyabrya": 11, "dekabrya": 12,
            }
            if sm:
                mo = MONTH_SLUG.get(sm.group(2).lower())
                if mo:
                    try:
                        d = date(int(sm.group(3)), mo, int(sm.group(1)))
                        rng = (d, d)
                    except ValueError:
                        pass
        if not rng:
            continue
        if "прямой эфир" in city.lower() or "прямой эфир" in title.lower():
            city = "Онлайн"
            etype = "Вебинар"
        else:
            etype = "Выставка"
        if "турц" in city.lower() or "анталь" in city.lower():
            country = "Турция"
        else:
            country = "Россия"
        ev = make_event(
            title, rng[0], rng[1], url, source,
            city=city, country=country, etype=etype,
            extra_vert="food агро пищев " + desc,
            description=desc[:300],
        )
        if ev:
            ev["vertical"] = "apk"
            if desc:
                ev["description"] = desc[:400]
            out.append(ev)
    return out


def parse_foodsmi_detail(html: str, url: str, source: str = "foodsmi") -> list[dict]:
    meta = re.search(r'<meta name="description" content="([^"]+)"', html)
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if not title_m:
        return []
    raw_title = strip_tags(title_m.group(1))
    city = ""
    title = raw_title
    if "," in raw_title:
        left, right = raw_title.split(",", 1)
        if len(left) < 45:
            city = left.strip()
            title = right.strip()
    desc = unescape(meta.group(1)) if meta else ""
    rng = None
    for pat in [
        r"[Сс]\s+(\d{1,2})\s+по\s+(\d{1,2})\s+([а-яё]+)\s+(\d{4})",
        r"(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+\d{4})",
        r"(\d{1,2}\s+[а-яё]+\s+\d{4})",
    ]:
        m = re.search(pat, desc, re.I)
        if not m:
            continue
        if len(m.groups()) == 4:
            rng = parse_ru_date_range(
                f"{m.group(1)}-{m.group(2)} {m.group(3)} {m.group(4)}", int(m.group(4))
            )
        else:
            rng = parse_ru_date_range(m.group(1), 2026)
        if rng:
            break
    if not rng:
        return []
    if "прямой эфир" in city.lower():
        city = "Онлайн"
    country = "Турция" if re.search(r"турц|анталь", city, re.I) else "Россия"
    ev = make_event(
        title, rng[0], rng[1], url, source,
        city=city, country=country, etype="Выставка",
        extra_vert="food агро пищев", description=desc[:300],
    )
    if not ev:
        return []
    ev["vertical"] = "apk"
    if desc:
        ev["description"] = desc[:400]
    return [ev]


def ingest_foodsmi() -> list[dict]:
    events: list[dict] = []
    # HTML listing
    html = ""
    try:
        html = fetch_text("https://foodsmi.com/events/")
        (SAMPLES / "foodsmi_events_sprint.html").write_text(html, encoding="utf-8")
    except Exception as e:
        print(f"  foodsmi live: {e}")
        for name in ("foodsmi_events_sprint.html", "foodsmi_events_live.html", "r5_foodsmi.html", "apk_foodsmi.html"):
            p = SAMPLES / name
            if p.exists():
                html = p.read_text(encoding="utf-8", errors="replace")
                break
    if html:
        part = parse_foodsmi(html, "foodsmi")
        for e in part:
            e["vertical"] = "apk"
        events.extend(part)

    # RSS
    rss = ""
    try:
        rss = fetch_text("https://foodsmi.com/events/rss/")
        (SAMPLES / "foodsmi_rss.xml").write_text(rss, encoding="utf-8")
    except Exception as e:
        print(f"  foodsmi rss: {e}")
        p = SAMPLES / "foodsmi_rss.xml"
        if p.exists():
            rss = p.read_text(encoding="utf-8", errors="replace")
    if rss:
        events.extend(parse_foodsmi_rss(rss))

    # detail pages
    for p in SAMPLES.glob("foodsmi_ev_*.html"):
        slug = p.name.replace("foodsmi_ev_", "").replace(".html", "")
        url = f"https://foodsmi.com/events/{slug}/"
        events.extend(parse_foodsmi_detail(p.read_text(encoding="utf-8", errors="replace"), url))

    seen = set()
    uniq = []
    for e in events:
        e["vertical"] = "apk"
        k = dedupe_key(e)
        if k in seen:
            for u in uniq:
                if dedupe_key(u) == k and len(e.get("description") or "") > len(u.get("description") or ""):
                    u["description"] = e["description"]
                    u["organizer_url"] = e.get("organizer_url") or u.get("organizer_url")
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# ---------------------------------------------------------------------------
# 5. Zivot seed article
# ---------------------------------------------------------------------------
def parse_zivot_article(html: str, source: str = "zivot") -> list[dict]:
    out: list[dict] = []
    out.extend(parse_zivot_full(html, source))
    out.extend(parse_zivot_seed(html, source))
    text = strip_tags(html)
    # Name 2026: date, place  OR  Name: Дата: ... Место: ...
    for m in re.finditer(
        r"([A-Za-zА-Яа-яЁё0-9 «»\"\-]{4,70}?)\s*2026:\s*"
        r"(?:Дата:\s*)?([^.\n]{5,50}?)(?:,\s*([^.\n]{3,60}))?",
        text,
    ):
        title = (m.group(1).strip() + " 2026").strip()
        date_s = m.group(2).strip()
        place = (m.group(3) or "").strip()
        # clean date_s if it has trailing place words
        rng = parse_ru_date_range(date_s, 2026)
        if not rng:
            # try first date-like token
            dm = re.search(
                r"\d{1,2}\s*[–\-—]?\s*\d{0,2}\s+[а-яё]+(?:\s+\d{4})?",
                date_s, re.I,
            )
            if dm:
                rng = parse_ru_date_range(dm.group(0), 2026)
        if not rng:
            continue
        city = place.split(",")[0].strip() if place else ""
        ev = make_event(
            title, rng[0], rng[1],
            "https://zivotnovodstvo.ru/agrokalendar-2026-glavnye-vystavki-i-dni-polya-do-koncza-goda/",
            source, city=city, extra_vert="агро", description=f"{title}. {place}"[:200],
        )
        if ev:
            ev["vertical"] = "apk"
            out.append(ev)
    # Агросалон / Агропродмаш structured
    for m in re.finditer(
        r"(Агросалон|Агропродмаш|WorldFood Moscow|ЮГАГРО|Агрорусь|МинводыАГРО|"
        r"Золотая Нива|АГРОВОЛГА)\s*2026:\s*(?:Дата:\s*)?([^М\n]{5,60}?)(?:\s*Место:\s*([^\n]{3,80}))?",
        text, re.I,
    ):
        title = m.group(1).strip() + " 2026"
        date_s = m.group(2).strip().rstrip(".")
        place = (m.group(3) or "").strip()
        rng = parse_ru_date_range(date_s, 2026)
        if not rng:
            continue
        city = place.split(",")[0].strip() if place else ""
        desc = ""
        # look for Суть after
        chunk = text[m.end() : m.end() + 120]
        sm = re.search(r"Суть:\s*([^.]{10,120})", chunk)
        if sm:
            desc = sm.group(1).strip()
        ev = make_event(
            title, rng[0], rng[1],
            "https://zivotnovodstvo.ru/agrokalendar-2026-glavnye-vystavki-i-dni-polya-do-koncza-goda/",
            source, city=city, extra_vert="агро", description=desc or f"{title}. {place}"[:200],
        )
        if ev:
            ev["vertical"] = "apk"
            out.append(ev)
    seen = set()
    uniq = []
    for e in out:
        e["vertical"] = "apk"
        k = (e["title"].strip().lower(), e.get("starts_at"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def ingest_zivot() -> list[dict]:
    html = ""
    live = SAMPLES / "zivot_agrokalendar_2026_live.html"
    try:
        html = fetch_text(
            "https://zivotnovodstvo.ru/agrokalendar-2026-glavnye-vystavki-i-dni-polya-do-koncza-goda/"
        )
        live.write_text(html, encoding="utf-8")
    except Exception as e:
        print(f"  zivot live: {e}")
        for name in (
            "zivot_agrokalendar_2026_live.html", "apk_zivot_2026.html",
            "zivot_agrokalendar.html", "r5_zivot_agrokalendar.html",
        ):
            p = SAMPLES / name
            if p.exists():
                html = p.read_text(encoding="utf-8", errors="replace")
                break
    if not html:
        return []
    return parse_zivot_article(html)


# ---------------------------------------------------------------------------
# 6. Flagship one-pagers — enrich description/dates for upcoming ≥ TODAY
# ---------------------------------------------------------------------------
FLAGSHIPS = [
    ("agravia", "flag_agravia.html", "https://agravia.org/", "AGRAVIA", "агро"),
    ("niva", "flag_niva.html", "https://niva-expo.ru/", "Золотая Нива", "агро нива"),
    ("agrovolga", "flag_agrovolga.html", "https://agrovolga.org/", "АГРОВОЛГА", "агро"),
    ("meat", "flag_meatindustry.html", "https://meatindustry.ru/", "Meat & Poultry Industry Russia", "мясн агро"),
    ("feedvet", "flag_feedvet.html", "https://feedvet-expo.ru/", "КормВетГрэйн Экспо", "корм вет агро"),
    ("sibagro", "flag_sibagroweek.html", "https://sibagroweek.ru/", "Сибирская аграрная неделя", "агро"),
    ("minvody", "flag_minvodyagro.html", "https://minvodyagro.ru/", "МинводыАГРО", "агро"),
    ("seafood", "flag_seafood.html", "https://seafoodexporussia.com/", "Seafood Expo Russia", "рыбн seafood"),
    ("agbz", "flag_agbz.html", "https://events.agbz.ru/", "Зерно России", "зерн агро"),
]


def parse_flagship(html: str, url: str, default_title: str, extra: str, source: str) -> list[dict]:
    out: list[dict] = []
    text = strip_tags(html)[:8000]
    desc = ""
    md = re.search(r'<meta name="description" content="([^"]+)"', html)
    if md:
        desc = unescape(md.group(1))
    if not desc:
        desc = text[:220]

    # JSON-LD
    for m in re.finditer(
        r'"startDate"\s*:\s*"([^"]+)"[\s\S]{0,200}?"endDate"\s*:\s*"([^"]+)"',
        html,
    ):
        try:
            starts = date.fromisoformat(m.group(1)[:10])
            ends = date.fromisoformat(m.group(2)[:10])
        except ValueError:
            continue
        title_m = re.search(r'"name"\s*:\s*"([^"]+)"', html[max(0, m.start() - 500) : m.end() + 200])
        title = unescape(title_m.group(1)) if title_m else default_title
        ev = make_event(
            title, starts, ends, url, source,
            city="", extra_vert=extra, description=desc[:300], etype="Выставка",
        )
        if ev:
            ev["vertical"] = "apk"
            if desc:
                ev["description"] = desc[:400]
            out.append(ev)

    # RU / EN date ranges near title keywords
    for m in re.finditer(
        r"(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|"
        r"сентябр|октябр|ноябр|декабр)\w*\s+202[6-9])",
        text, re.I,
    ):
        rng = parse_ru_date_range(m.group(1), 2026)
        if not rng or rng[1] < TODAY:
            continue
        title = default_title
        # year from date
        if str(rng[0].year) not in title:
            title = f"{default_title} {rng[0].year}"
        ev = make_event(
            title, rng[0], rng[1], url, source,
            extra_vert=extra, description=desc[:300], etype="Выставка",
        )
        if ev:
            ev["vertical"] = "apk"
            if desc:
                ev["description"] = desc[:400]
            out.append(ev)

    # Oct style without year for feedvet: 27 - 29 ОКТЯБРЯ
    if not out and re.search(r"октябр", text, re.I):
        m = re.search(r"(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+октябр\w*)", text, re.I)
        if m:
            rng = parse_ru_date_range(m.group(1) + " 2026", 2026)
            if rng and rng[1] >= TODAY:
                ev = make_event(
                    f"{default_title} 2026", rng[0], rng[1], url, source,
                    city="Москва", extra_vert=extra, description=desc[:300],
                )
                if ev:
                    ev["vertical"] = "apk"
                    if desc:
                        ev["description"] = desc[:400]
                    out.append(ev)

    # Nov without repeating
    if "сиб" in default_title.lower() or "sibagro" in url:
        m = re.search(r"(10\s*[–\-—]\s*12\s+ноябр\w*\s*2026)", text, re.I)
        if m:
            rng = parse_ru_date_range(m.group(1), 2026)
            if rng:
                ev = make_event(
                    "Сибирская аграрная неделя 2026", rng[0], rng[1], url, source,
                    city="Новосибирск", extra_vert=extra, description=desc[:300],
                )
                if ev:
                    ev["vertical"] = "apk"
                    if desc:
                        ev["description"] = desc[:400]
                    out.append(ev)

    seen = set()
    uniq = []
    for e in out:
        k = (e["title"].strip().lower(), e.get("starts_at"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def ingest_flagships() -> list[dict]:
    events: list[dict] = []
    for source, fname, url, title, extra in FLAGSHIPS:
        p = SAMPLES / fname
        html = ""
        if p.exists() and p.stat().st_size > 500:
            html = p.read_text(encoding="utf-8", errors="replace")
        else:
            try:
                html = fetch_text(url)
                p.write_text(html, encoding="utf-8")
            except Exception as e:
                print(f"  flagship {source}: {e}")
                continue
        part = parse_flagship(html, url, title, extra, source)
        print(f"  flagship {source}: {len(part)}")
        events.extend(part)
    return events


# ---------------------------------------------------------------------------
# Enrich existing events' description from new parses (by URL / soft title+date)
# ---------------------------------------------------------------------------
def enrich_descriptions(existing: list[dict], donors: list[dict]) -> int:
    by_url = {}
    by_soft = {}
    for d in donors:
        u = (d.get("organizer_url") or "").rstrip("/").lower()
        if u and d.get("description"):
            by_url[u] = d
        sk = ((d.get("title") or "").strip().lower()[:40], d.get("starts_at") or "")
        if d.get("description"):
            by_soft[sk] = d
    n = 0
    for e in existing:
        u = (e.get("organizer_url") or "").rstrip("/").lower()
        sk = ((e.get("title") or "").strip().lower()[:40], e.get("starts_at") or "")
        donor = by_url.get(u) or by_soft.get(sk)
        if not donor:
            # fuzzy: same start date + title token overlap
            continue
        new_d = (donor.get("description") or "").strip()
        old_d = (e.get("description") or "").strip()
        if new_d and len(new_d) > len(old_d):
            e["description"] = new_d[:400]
            n += 1
    return n


def enrich_from_ics_files(existing: list[dict]) -> int:
    """Match ICS SUMMARY to existing titles and fill DESCRIPTION."""
    n = 0
    ics_events = []
    for p in SAMPLES.glob("ai_*.ics*"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            ics_events.extend(parse_ics_events(text))
        except Exception:
            continue
    by_sum = {(e.get("title") or "").strip().lower(): e for e in ics_events if e.get("description")}
    for e in existing:
        t = (e.get("title") or "").strip().lower()
        donor = by_sum.get(t)
        if not donor:
            continue
        new_d = (donor.get("description") or "").strip()
        old_d = (e.get("description") or "").strip()
        if new_d and len(new_d) > len(old_d):
            e["description"] = new_d[:400]
            n += 1
            if e.get("vertical") != "apk" and APK_RE.search(new_d + " " + t):
                e["vertical"] = "apk"
    return n


def remap_apk(events: list[dict]) -> int:
    changed = 0
    for e in events:
        blob = f"{e.get('title','')} {e.get('description','')} {e.get('type','')} {e.get('source','')}"
        src = e.get("source") or ""
        force = src in {
            "foodsmi", "tsenovik", "agroinvestor", "agrozentr", "zivot", "zivot_seed",
            "agravia", "niva", "agrovolga", "meat", "feedvet", "sibagro", "minvody",
            "seafood", "agbz", "agroday", "sibagroweek",
        }
        if force or APK_RE.search(blob) or SOFT_APK_RE.search(blob):
            if e.get("vertical") != "apk":
                # don't steal pure IT unless agro keywords strong
                if e.get("vertical") == "it" and not (APK_RE.search(blob) or force):
                    continue
                e["vertical"] = "apk"
                changed += 1
    return changed


def write_outputs(events: list[dict]) -> dict:
    events = [e for e in events if (e.get("ends_at") or "") >= TODAY.isoformat()]
    events.sort(key=lambda e: (e.get("starts_at") or "", e.get("title") or ""))
    (ROOT / "events_upcoming.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    js_events = []
    for i, e in enumerate(events, 1):
        summary = (e.get("description") or "").strip()
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
            "summary": summary,
            "source": e.get("source") or "",
        })
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = {"updated_at": now, "count": len(js_events)}
    js = (
        "window.EVENTS = " + json.dumps(js_events, ensure_ascii=False) + ";\n"
        + "window.EVENTS_META = " + json.dumps(meta, ensure_ascii=False) + ";\n"
    )
    (ROOT / "events-data.js").write_text(js, encoding="utf-8")
    return meta


def run() -> dict:
    existing = load_existing()
    before = len(existing)
    before_v = Counter(e.get("vertical") for e in existing)
    stats: dict = {
        "before": before,
        "before_verticals": dict(before_v),
    }
    all_new: list[dict] = []

    print("=== tsenovik Russia 2026 block ===")
    tsen = ingest_tsenovik()
    print(f"  parsed {len(tsen)}")
    existing, n = merge_events(existing, tsen)
    stats["tsenovik_parsed"] = len(tsen)
    stats["tsenovik_added"] = n
    all_new.extend(tsen)

    print("=== agroinvestor + ICS ===")
    ai = ingest_agroinvestor()
    print(f"  parsed {len(ai)}")
    existing, n = merge_events(existing, ai)
    stats["agroinvestor_parsed"] = len(ai)
    stats["agroinvestor_added"] = n
    all_new.extend(ai)

    print("=== agrozentr ===")
    az = ingest_agrozentr()
    print(f"  parsed {len(az)}")
    existing, n = merge_events(existing, az)
    stats["agrozentr_parsed"] = len(az)
    stats["agrozentr_added"] = n
    all_new.extend(az)

    print("=== foodsmi HTML/RSS/details ===")
    food = ingest_foodsmi()
    print(f"  parsed {len(food)}")
    existing, n = merge_events(existing, food)
    stats["foodsmi_parsed"] = len(food)
    stats["foodsmi_added"] = n
    all_new.extend(food)

    print("=== zivot seed ===")
    zv = ingest_zivot()
    print(f"  parsed {len(zv)}")
    existing, n = merge_events(existing, zv)
    stats["zivot_parsed"] = len(zv)
    stats["zivot_added"] = n
    all_new.extend(zv)

    print("=== flagships ===")
    fl = ingest_flagships()
    print(f"  parsed {len(fl)}")
    existing, n = merge_events(existing, fl)
    stats["flagship_parsed"] = len(fl)
    stats["flagship_added"] = n
    all_new.extend(fl)

    enr = enrich_descriptions(existing, all_new)
    enr_ics = enrich_from_ics_files(existing)
    stats["enriched_desc"] = enr
    stats["enriched_ics"] = enr_ics

    remapped = remap_apk(existing)
    stats["remap_changed"] = remapped

    meta = write_outputs(existing)
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    after_v = Counter(e.get("vertical") for e in final)
    stats["after"] = len(final)
    stats["after_verticals"] = dict(after_v)
    stats["updated_at"] = meta["updated_at"]
    stats["count"] = meta["count"]
    stats["apk_net"] = after_v.get("apk", 0) - before_v.get("apk", 0)
    stats["n_net"] = len(final) - before
    return stats


def main():
    stats = run()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(
        f"FINAL N={stats['count']} apk={stats['after_verticals'].get('apk', 0)} "
        f"industry={stats['after_verticals'].get('industry', 0)} it={stats['after_verticals'].get('it', 0)} "
        f"apk_net={stats['apk_net']} n_net={stats['n_net']}"
    )


if __name__ == "__main__":
    main()
