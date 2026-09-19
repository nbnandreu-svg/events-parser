#!/usr/bin/env python3
"""Refresh events catalog: APK sprint parsers (tsenovik/agroinvestor/agrozentr/foodsmi/zivot/flagships) + agroday + remap + JS."""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from html import unescape
from urllib.parse import unquote
import urllib.request

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
sys.path.insert(0, str(ROOT))

from ingest_round2 import (  # noqa: E402
    TODAY, APK_RE, IT_RE, strip_tags, vertical_for, make_event,
    parse_ru_date_range, parse_tsenovik_calendar_table, parse_tsenovik_news_rows,
    UA,
)
from ingest_round3 import dedupe_key  # noqa: E402
from ingest_round4 import parse_foodsmi  # noqa: E402
from ingest_apk400 import run_apk400  # noqa: E402
from ingest_apk_push2 import run_apk_push2  # noqa: E402
from enrich_summaries import batch_enrich as summary_batch_enrich  # noqa: E402
from ingest_apk_sprint import (  # noqa: E402
    ingest_tsenovik as sprint_ingest_tsenovik,
    ingest_agroinvestor as sprint_ingest_agroinvestor,
    ingest_agrozentr as sprint_ingest_agrozentr,
    ingest_foodsmi as sprint_ingest_foodsmi,
    ingest_zivot as sprint_ingest_zivot,
    ingest_flagships as sprint_ingest_flagships,
    enrich_descriptions as sprint_enrich_descriptions,
    enrich_from_ics_files as sprint_enrich_ics,
    remap_apk as sprint_remap_apk,
    SOFT_APK_RE,
)

MONTH_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def fetch_url(url: str, timeout: int = 40) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        enc = r.headers.get_content_charset() or "utf-8"
    for e in (enc, "utf-8", "cp1251"):
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


def merge_events(existing: list[dict], new_events: list[dict], source_label: str) -> tuple[list[dict], int]:
    seen = {dedupe_key(e) for e in existing}
    url_title = {((e.get("organizer_url") or "").rstrip("/").lower(), (e.get("title") or "").strip().lower()) for e in existing}
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


def remap_apk_upgrade(events: list[dict]) -> int:
    """Upgrade to apk when agro/food keywords match; foodsmi/tsenovik forced via sprint_remap."""
    changed = sprint_remap_apk(events)
    for e in events:
        blob = f"{e.get('title','')} {e.get('description','')} {e.get('type','')}"
        if e.get("vertical") == "industry" and IT_RE.search(blob) and not (
            APK_RE.search(blob) or SOFT_APK_RE.search(blob)
        ):
            e["vertical"] = "it"
            changed += 1
    return changed


def parse_en_date_range(text: str, year: int = 2026) -> Optional[tuple[date, date]]:
    """Parse '06 October — 09 October' or '22 October — 23 October'."""
    t = text.lower().replace("–", "-").replace("—", "-")
    m = re.search(
        r"(\d{1,2})\s+([a-z]+)\s*-\s*(\d{1,2})\s+([a-z]+)",
        t,
    )
    if not m:
        m2 = re.search(r"(\d{1,2})\s+([a-z]+)", t)
        if not m2:
            return None
        d, mon = int(m2.group(1)), MONTH_EN.get(m2.group(2))
        if not mon:
            return None
        try:
            dd = date(year, mon, d)
            return dd, dd
        except ValueError:
            return None
    d1, m1, d2, m2 = int(m.group(1)), MONTH_EN.get(m.group(2)), int(m.group(3)), MONTH_EN.get(m.group(4))
    if not m1 or not m2:
        return None
    try:
        return date(year, m1, d1), date(year, m2, d2)
    except ValueError:
        return None


def parse_agroday_list_text(text: str, source: str = "agroday") -> list[dict]:
    """Parse Playwright/WebFetch plaintext / markdown of agroday.ru/expo/."""
    out: list[dict] = []
    # Blocks like: 06 October — 09 October| Москва \n\n TITLE \n\n description
    # or EN month ranges followed by city then title
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        ln = lines[i]
        # date|city pattern
        m = re.match(
            r"(\d{1,2}\s+\w+\s*[—–-]\s*\d{1,2}\s+\w+)\s*\|\s*(.+)$",
            ln,
        )
        if not m:
            m = re.match(
                r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s*[—–-]\s*\d{1,2}\s+\w+)\s*\|\s*(.+)$",
                ln,
                re.I,
            )
        if m:
            date_s, city = m.group(1), m.group(2).strip()
            # find next non-empty title line that isn't Organizer
            title = ""
            desc = ""
            j = i + 1
            while j < len(lines) and not lines[j]:
                j += 1
            if j < len(lines) and not lines[j].lower().startswith("организатор") and not lines[j].lower().startswith("organizer"):
                title = lines[j].strip(" «»\"")
                j += 1
                while j < len(lines) and not lines[j]:
                    j += 1
                if j < len(lines) and not re.match(r"\d{1,2}\s+\w+\s*[—–-]", lines[j]) and not lines[j].lower().startswith("организатор"):
                    if not lines[j].lower().startswith("organizer"):
                        desc = lines[j]
            year = 2027 if "2027" in title else 2026
            rng = parse_en_date_range(date_s, year)
            if not rng:
                rng = parse_ru_date_range(date_s, year)
            if rng and title and len(title) >= 3:
                # skip if title looks like description-only
                url = "https://agroday.ru/expo/"
                slug = re.sub(r"[^a-z0-9а-яё]+", "-", title.lower())
                ev = make_event(
                    title, rng[0], rng[1], url, source,
                    city=city, country="Россия", etype="Выставка",
                    extra_vert="агро сельхоз " + desc, description=(desc or "")[:300],
                )
                if ev:
                    ev["vertical"] = "apk"
                    out.append(ev)
            i = j
            continue
        i += 1
    # Dedup by title
    seen = set()
    uniq = []
    for e in out:
        k = e["title"].strip().lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def parse_agroday_html(html: str, source: str = "agroday") -> list[dict]:
    """Best-effort parse of rendered or static HTML."""
    text = strip_tags(html)
    # Also try structured cards
    out = parse_agroday_list_text(text, source)
    # EN date | city patterns still in raw
    for m in re.finditer(
        r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s*[—–-]\s*\d{1,2}\s+\w+)\s*\|\s*([^\n<]+)",
        html,
        re.I,
    ):
        pass  # covered via text
    return out


def seed_agroday_known() -> list[dict]:
    """Seed from WebFetch snapshots of agroday.ru/expo/* (live HTML stalls ~16KB)."""
    known = [
        ("АГРОСАЛОН 2026", "2026-10-06", "2026-10-09", "Москва",
         "Международная специализированная выставка сельскохозяйственной техники. МВЦ Крокус Экспо.",
         "https://agroday.ru/expo/agrosalon/"),
        ("ВолгоградАГРО-2026", "2026-10-22", "2026-10-23", "Волгоград",
         "Торговая площадка сельхозтехники, оборудования, семян, средств защиты растений.",
         "https://agroday.ru/expo/volgogradagro/"),
        ("ЮГАГРО 2026", "2026-11-17", "2026-11-20", "Краснодар",
         "Международная выставка сельскохозяйственной техники, оборудования и материалов для растениеводства. ВКК Экспоград Юг.",
         "https://agroday.ru/expo/yugagro/"),
        ("iAGRI 2027", "2027-01-20", "2027-01-22", "Москва",
         "Международная выставка инноваций и высоких технологий для АПК. МВЦ Крокус Экспо.",
         "https://agroday.ru/expo/iagri/"),
        ("AGRAVIA (АГРАВИЯ) 2027", "2027-01-20", "2027-01-22", "Москва",
         "Крупнейшая международная выставка агропромышленных технологий. МВЦ Крокус Экспо.",
         "https://agroday.ru/expo/agravia-agraviya/"),
    ]
    out = []
    for title, s, e, city, desc, url in known:
        ev = make_event(
            title, date.fromisoformat(s), date.fromisoformat(e), url, "agroday",
            city=city, etype="Выставка", extra_vert="агро сельхоз", description=desc,
        )
        if ev:
            ev["vertical"] = "apk"
            out.append(ev)
    return out


def upsert_by_url(existing: list[dict], new_events: list[dict]) -> tuple[list[dict], int, int]:
    """Insert or refresh fields for same organizer_url (agroday date fixes)."""
    by_url = {}
    for i, e in enumerate(existing):
        u = (e.get("organizer_url") or "").rstrip("/").lower()
        if u:
            by_url[u] = i
    added = updated = 0
    for e in new_events:
        u = (e.get("organizer_url") or "").rstrip("/").lower()
        if u and u in by_url:
            i = by_url[u]
            # refresh dates/title/desc/vertical
            for k in ("title", "starts_at", "ends_at", "city", "description", "vertical", "type"):
                if e.get(k):
                    existing[i][k] = e[k]
            updated += 1
        else:
            existing, n = merge_events(existing, [e], e.get("source") or "agroday")
            added += n
            if u:
                by_url[u] = len(existing) - 1
    return existing, added, updated



def ingest_agroday() -> list[dict]:
    events: list[dict] = []
    # 1) Playwright if available
    try:
        import os
        if os.environ.get("SKIP_PW") == "1":
            raise RuntimeError("SKIP_PW")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
            )
            try:
                page.goto("https://agroday.ru/expo/", wait_until="commit", timeout=60000)
                page.wait_for_timeout(15000)
                for _ in range(8):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(500)
                html = page.content()
                text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                SAMPLES.mkdir(exist_ok=True)
                (SAMPLES / "apk_agroday_expo.html").write_text(html, encoding="utf-8")
                (SAMPLES / "apk_agroday_expo.txt").write_text(text, encoding="utf-8")
                events.extend(parse_agroday_list_text(text))
                events.extend(parse_agroday_html(html))
            finally:
                browser.close()
    except Exception as e:
        print(f"  agroday playwright: {type(e).__name__}: {e}")

    # 2) cached samples
    for name in ("apk_agroday_webfetch.md", "apk_agroday_expo.txt", "apk_agroday_expo.html", "apk_agroday_full.html", "apk_agroday_curl.html"):
        p = SAMPLES / name
        if p.exists() and p.stat().st_size > 1000:
            raw = p.read_text(encoding="utf-8", errors="replace")
            if name.endswith(".txt"):
                events.extend(parse_agroday_list_text(raw))
            else:
                events.extend(parse_agroday_html(raw))

    # 3) seed known upcoming from WebFetch
    events.extend(seed_agroday_known())

    # dedupe
    seen = set()
    uniq = []
    for e in events:
        k = e["title"].strip().lower()
        if k in seen:
            continue
        seen.add(k)
        e["vertical"] = "apk"
        uniq.append(e)
    return uniq


def ingest_foodsmi() -> list[dict]:
    """HTML + RSS + detail pages; force vertical apk (food industry)."""
    return sprint_ingest_foodsmi()


def ingest_tsenovik() -> list[dict]:
    """Parse block «Выставки России 2026» (not NewsCalNews grid)."""
    return sprint_ingest_tsenovik()


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


def refresh() -> dict:
    existing = load_existing()
    before = len(existing)
    before_v = Counter(e.get("vertical") for e in existing)
    stats = {"before": before, "before_verticals": dict(before_v)}
    donors: list[dict] = []

    agro = ingest_agroday()
    existing, n_agro, n_upd = upsert_by_url(existing, agro)
    stats["agroday_parsed"] = len(agro)
    stats["agroday_added"] = n_agro
    stats["agroday_updated"] = n_upd
    donors.extend(agro)

    food = ingest_foodsmi()
    existing, n_food = merge_events(existing, food, "foodsmi")
    stats["foodsmi_parsed"] = len(food)
    stats["foodsmi_added"] = n_food
    donors.extend(food)

    tsen = ingest_tsenovik()
    existing, n_tsen = merge_events(existing, tsen, "tsenovik")
    stats["tsenovik_parsed"] = len(tsen)
    stats["tsenovik_added"] = n_tsen
    donors.extend(tsen)

    ai = sprint_ingest_agroinvestor()
    existing, n_ai = merge_events(existing, ai, "agroinvestor")
    stats["agroinvestor_parsed"] = len(ai)
    stats["agroinvestor_added"] = n_ai
    donors.extend(ai)

    az = sprint_ingest_agrozentr()
    existing, n_az = merge_events(existing, az, "agrozentr")
    stats["agrozentr_parsed"] = len(az)
    stats["agrozentr_added"] = n_az
    donors.extend(az)

    zv = sprint_ingest_zivot()
    existing, n_zv = merge_events(existing, zv, "zivot")
    stats["zivot_parsed"] = len(zv)
    stats["zivot_added"] = n_zv
    donors.extend(zv)

    fl = sprint_ingest_flagships()
    existing, n_fl = merge_events(existing, fl, "flagship")
    stats["flagship_parsed"] = len(fl)
    stats["flagship_added"] = n_fl
    donors.extend(fl)

    # APK400 sources (rynok/exponet agri/expocalendar/expomap/seminars/remap)
    existing, apk400_stats = run_apk400(existing=existing, fetch_live=True)
    stats["apk400"] = apk400_stats
    donors.extend([])  # sources already merged into existing

    # APK push2: Expomap HTTP pagination + agrozentr + field-day + soft remap
    existing, push2_stats = run_apk_push2(existing=existing, fetch_live=True)
    stats["apk_push2"] = push2_stats

    stats["enriched_desc"] = sprint_enrich_descriptions(existing, donors)
    stats["enriched_ics"] = sprint_enrich_ics(existing)

    remapped = remap_apk_upgrade(existing)
    stats["remap_changed"] = remapped

    meta = write_outputs(existing)
    # Opt-in CIS summary enrich (RUN_SUMMARY_ENRICH=1). Full batch: run_batch_enrich_cis.py
    import os
    if os.environ.get("RUN_SUMMARY_ENRICH") == "1":
        try:
            sstats = summary_batch_enrich(only_cis=True, workers=6)
            stats["summary_enrich"] = {
                k: sstats.get(k)
                for k in ("ok", "fail", "cis_real_pct", "after_cis_real")
                if k in sstats
            }
            meta = write_outputs(json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8")))
        except Exception as e:
            stats["summary_enrich_error"] = f"{type(e).__name__}: {e}"
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    after_v = Counter(e.get("vertical") for e in final)
    stats["after"] = len(final)
    stats["after_verticals"] = dict(after_v)
    stats["updated_at"] = meta["updated_at"]
    stats["count"] = meta["count"]
    return stats


def main():
    stats = refresh()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(
        f"FINAL N={stats['count']} apk={stats['after_verticals'].get('apk',0)} "
        f"industry={stats['after_verticals'].get('industry',0)} it={stats['after_verticals'].get('it',0)}"
    )


if __name__ == "__main__":
    main()
