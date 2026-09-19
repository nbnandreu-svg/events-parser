#!/usr/bin/env python3
"""APK push toward ~400: rynok-apk, exponet agri RF/CIS, ExpoCalendar industry (PW samples),
expomap themes (PW samples), agroday seminars (PW optional), soft remap.

Playwright: enabled unless SKIP_PW=1.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Optional

from ingest_round2 import (
    TODAY, APK_RE, strip_tags, make_event, parse_exponet_topic, fetch, SAMPLES as R2_SAMPLES,
)
from ingest_round3 import dedupe_key
from ingest_apk_sprint import SOFT_APK_RE

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)

MONTH_RU = {
    "января": 1, "январь": 1, "февраля": 2, "февраль": 2, "марта": 3, "март": 3,
    "апреля": 4, "апрель": 4, "мая": 5, "май": 5, "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7, "августа": 8, "август": 8, "сентября": 9, "сентябрь": 9,
    "октября": 10, "октябрь": 10, "ноября": 11, "ноябрь": 11, "декабря": 12, "декабрь": 12,
}
MONTH_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

APK_HINT = re.compile(
    r"агро|сельск|пищев|food|dairy|молок|мясн|ветер|животн|зерн|сад|огород|horeca|"
    r"фермер|растениевод|fruit|wine|вино|пив|beer|зоо|рыб|aqua|корм|вет|нива|поле|"
    r"овощ|ягод|мёд|мед|пчел|озелен|питомник|farm|agri|agr[oa]|meat|fish|напит|"
    r"выпечк|кондитер|гастро|продэкспо|югагро|день\s+поля|пчело|дач|pet\b|brew|"
    r"зелён|зелен|утилит|аккорд",
    re.I,
)
SKIP_TITLE = re.compile(
    r"интерткань|mitt\b|недвижим|юридич|\bhr\b|кадр|строител|туризм|textile",
    re.I,
)


def force_apk(ev: Optional[dict]) -> Optional[dict]:
    if not ev:
        return None
    ev["vertical"] = "apk"
    return ev


def clean_title(t: str) -> str:
    t = unescape(strip_tags(t)).strip()
    t = re.sub(r"\s*ХИТ\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" «»\"'")


def mon(s: str) -> Optional[int]:
    s = (s or "").lower()
    if s in MONTH_EN:
        return MONTH_EN[s]
    if s in MONTH_RU:
        return MONTH_RU[s]
    for k, v in MONTH_RU.items():
        if s.startswith(k[:4]):
            return v
    for k, v in MONTH_EN.items():
        if s.startswith(k[:3]):
            return v
    return None


def merge_into(existing: list[dict], new_events: list[dict]) -> tuple[list[dict], int]:
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


# ---- rynok-apk ----
def parse_rynok_dates(ttxt: str, default_year: int = 2026) -> Optional[tuple[date, date]]:
    ttxt = ttxt.replace("\xa0", " ").replace("–", "-").replace("—", "-")
    m = re.search(
        r"(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+(\d{4})",
        ttxt, re.I,
    )
    if m and not re.search(r"\d{1,2}\.\d{1,2}", ttxt):
        key = m.group(1).lower()[:4]
        month = next((v for k, v in MONTH_RU.items() if k.startswith(key) or key.startswith(k[:4])), None)
        if month:
            y = int(m.group(2))
            try:
                return date(y, month, 1), date(y, month, 28)
            except ValueError:
                return None
    dates = re.findall(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", ttxt)
    if not dates:
        return None
    year = default_year
    for _a, _b, y in dates:
        if y:
            year = int(y) if len(y) == 4 else 2000 + int(y)
    try:
        d1 = date(year, int(dates[0][1]), int(dates[0][0]))
        if len(dates) >= 2:
            y2 = year
            if dates[1][2]:
                y2 = int(dates[1][2]) if len(dates[1][2]) == 4 else 2000 + int(dates[1][2])
            d2 = date(y2, int(dates[1][1]), int(dates[1][0]))
        else:
            d2 = d1
        return d1, d2
    except ValueError:
        return None


def parse_rynok_html(html: str, source: str = "rynok_apk") -> list[dict]:
    out: list[dict] = []
    for li in re.findall(r"<li>(.*?)</li>", html, re.S | re.I):
        tm = re.search(r"<time[^>]*>(.*?)</time>", li, re.S | re.I)
        if not tm:
            continue
        rng = parse_rynok_dates(strip_tags(tm.group(1)))
        if not rng:
            continue
        alt = re.search(r'alt="([^"]+)"', li)
        title = clean_title(alt.group(1)) if alt else ""
        if not title:
            m2 = re.search(
                r'<a href="/exhibitions/[^"]+"[^>]*>(?:<img[^>]*>)?\s*([^<]+)</a>',
                li, re.S | re.I,
            )
            title = clean_title(m2.group(1)) if m2 else ""
        if not title or title in ("Россия", "Зарубежье", "Главная"):
            continue
        href_m = re.search(r'href="(/exhibitions/(?:internal|external)/[^"]+)"', li)
        if not href_m:
            continue
        href = href_m.group(1)
        if href.rstrip("/").endswith(("internal", "external")):
            continue
        pm = re.search(r"<p>(.*?)</p>", li, re.S | re.I)
        desc = strip_tags(pm.group(1))[:200] if pm else ""
        url = "https://rynok-apk.ru" + href
        city = ""
        cm = re.search(r"\(г\.\s*([^)]+)\)", title)
        if cm:
            city = cm.group(1).strip()
        country = "Россия"
        if "/external/" in href:
            country = "СНГ"
            for needle, ctry in [
                ("Казахстан", "Казахстан"), ("Астана", "Казахстан"),
                ("Баку", "Азербайджан"), ("Минск", "Беларусь"), ("Ташкент", "Узбекистан"),
            ]:
                if needle.lower() in (title + " " + desc).lower():
                    country = ctry
                    break
        etype = "Выставка"
        blob = title + " " + desc
        if re.search(r"день\s+поля", blob, re.I):
            etype = "День поля"
        elif re.search(r"форум", blob, re.I):
            etype = "Форум"
        elif re.search(r"семинар|конгресс|конференц", blob, re.I):
            etype = "Семинар"
        ev = make_event(
            title, rng[0], rng[1], url, source,
            city=city, country=country, etype=etype,
            extra_vert="агро сельхоз апк " + desc, description=desc,
        )
        if ev:
            out.append(force_apk(ev))
    seen: set[str] = set()
    uniq = []
    for e in out:
        k = (e.get("organizer_url") or "").rstrip("/").lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def ingest_rynok(fetch_live: bool = True) -> list[dict]:
    specs = [
        ("https://rynok-apk.ru/exhibitions/", "apk_rynok_exhibitions.html"),
        ("https://rynok-apk.ru/exhibitions/internal/", "apk_rynok_internal.html"),
        ("https://rynok-apk.ru/exhibitions/external/", "apk_rynok_external.html"),
    ]
    out: list[dict] = []
    for url, fn in specs:
        path = SAMPLES / fn
        if fetch_live:
            try:
                fetch(url, dest=path, timeout=30)
            except Exception as e:
                print(f"  rynok fetch {url}: {type(e).__name__}: {e}")
        if path.exists():
            out.extend(parse_rynok_html(path.read_text("utf-8", errors="replace")))
    seen: set[str] = set()
    uniq = []
    for e in out:
        k = dedupe_key(e)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# ---- exponet agri ----
def decode_prefer(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("cp1251", "utf-8"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def fix_cis_country(ev: dict) -> dict:
    city = ev.get("city") or ""
    for name in (
        "Казахстан", "Кыргызстан", "Киргизия", "Узбекистан", "Беларусь",
        "Азербайджан", "Молдова", "Армения", "Таджикистан", "Туркменистан",
    ):
        if name.lower() in city.lower() or name.lower() in (ev.get("title") or "").lower():
            ev["country"] = "Кыргызстан" if name == "Киргизия" else name
            ev["city"] = re.sub(r",\s*" + re.escape(name) + r".*$", "", city, flags=re.I).strip()
            break
    else:
        if (ev.get("source") or "").startswith("exponet_cis"):
            ev.setdefault("country", "СНГ")
    return ev


def ingest_exponet_agri(fetch_live: bool = True) -> list[dict]:
    targets = [
        (
            "https://www.exponet.ru/exhibitions/countries/rus/topics/agriculture/dates/future/index.ru.html",
            "apk_exponet_rus_agri_future.html",
            "exponet_agri",
        ),
        (
            "https://www.exponet.ru/exhibitions/area/cis/topics/agriculture/dates/future/",
            "apk_exponet_cis_agri_future.html",
            "exponet_cis",
        ),
    ]
    out: list[dict] = []
    for url, fn, src in targets:
        path = SAMPLES / fn
        if fetch_live:
            try:
                fetch(url, dest=path, timeout=35)
            except Exception as e:
                print(f"  exponet fetch {url}: {type(e).__name__}: {e}")
        if not path.exists():
            continue
        html = decode_prefer(path)
        part = parse_exponet_topic(html, src)
        for e in part:
            e = force_apk(e)
            if src == "exponet_cis":
                e = fix_cis_country(e)
            out.append(e)
    seen: set[str] = set()
    uniq = []
    for e in out:
        k = dedupe_key(e)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# ---- ExpoCalendar industry texts (from PW) ----
def parse_expocalendar_text(text: str, source: str = "expocalendar") -> list[dict]:
    lines = [ln.strip() for ln in text.splitlines()]
    out: list[dict] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(
            r"(\d{1,2})\s+([A-Za-zА-Яа-яё]+)\s*[-–—]\s*(\d{1,2})\s+([A-Za-zА-Яа-яё]+)(?:\s+(\d{4}))?",
            ln,
        )
        if not m:
            i += 1
            continue
        completed = "Завершена" in ln or (i + 1 < len(lines) and lines[i + 1] == "Завершена")
        j = i + 1
        while j < len(lines) and (not lines[j] or lines[j] == "Завершена"):
            j += 1
        if j >= len(lines):
            break
        title = clean_title(lines[j])
        if title in ("Очистить", "Найти", "Главная Выставки") or len(title) < 3:
            i += 1
            continue
        j += 1
        while j < len(lines) and not lines[j]:
            j += 1
        desc = ""
        if j < len(lines) and not re.match(r"(Россия|Беларусь|Казахстан)", lines[j]) and not re.match(
            r"\d{1,2}\s+\w+\s*[-–—]", lines[j]
        ):
            desc = lines[j]
            j += 1
        while j < len(lines) and not lines[j]:
            j += 1
        city = ""
        country = "Россия"
        if j < len(lines):
            loc = lines[j]
            cm = re.match(r"(Россия|Беларусь|Казахстан|Узбекистан|Азербайджан)(?:,\s*(.+))?", loc)
            if cm:
                country = cm.group(1)
                rest = cm.group(2) or ""
                city = re.split(r"\s+(?:КВЦ|ВЦ|МВЦ|ВДНХ|«|Отель|Radisson|Гранд)", rest)[0].strip(" ,")
        mo1, mo2 = mon(m.group(2)), mon(m.group(4))
        year = int(m.group(5)) if m.group(5) else 2026
        ym = re.search(r"(20\d{2})", title)
        if ym:
            year = int(ym.group(1))
        if not mo1 or not mo2:
            i += 1
            continue
        try:
            d1 = date(year, mo1, int(m.group(1)))
            d2 = date(year, mo2, int(m.group(3)))
        except ValueError:
            i += 1
            continue
        if (completed and d2 < TODAY) or d2 < TODAY:
            i = j
            continue
        etype = "Форум" if re.search(r"форум|семинар|конференц", title + " " + desc, re.I) else "Выставка"
        ev = make_event(
            title, d1, d2, "https://www.expocalendar.ru/", source,
            city=city, country=country, etype=etype,
            extra_vert="агро сельхоз пищевая ветеринария " + desc, description=desc[:200],
        )
        if ev:
            out.append(force_apk(ev))
        i = j
    seen: set[tuple] = set()
    uniq = []
    for e in out:
        k = (e["title"].lower(), e["starts_at"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def ingest_expocalendar_samples() -> list[dict]:
    out: list[dict] = []
    for p in sorted(SAMPLES.glob("apk_pw_ec_*.txt")):
        out.extend(parse_expocalendar_text(p.read_text("utf-8", errors="replace")))
    for name in (
        "apk_pw_expocalendar_year_filtered.txt",
        "apk_pw_expocalendar_year.txt",
        "apk_pw_expocalendar_month.txt",
    ):
        p = SAMPLES / name
        if p.exists() and p.stat().st_size > 100:
            out.extend(parse_expocalendar_text(p.read_text("utf-8", errors="replace")))
    seen: set[str] = set()
    uniq = []
    for e in out:
        k = dedupe_key(e)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# ---- expomap theme texts (PW) ----
def parse_expomap_listing_text(text: str, source: str = "expomap") -> list[dict]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: list[dict] = []
    i = 0
    while i < len(lines):
        m = re.match(
            r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*[-–—]\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})$",
            lines[i],
        )
        if not m:
            i += 1
            continue
        y = int(m.group(6))
        y = y if y > 100 else 2000 + y
        y1 = m.group(3)
        y1 = int(y1) if y1 and len(y1) == 4 else (2000 + int(y1) if y1 else y)
        try:
            d1 = date(y1, int(m.group(2)), int(m.group(1)))
            d2 = date(y, int(m.group(5)), int(m.group(4)))
        except ValueError:
            i += 1
            continue
        loc = lines[i + 1] if i + 1 < len(lines) else ""
        title = clean_title(lines[i + 2]) if i + 2 < len(lines) else ""
        desc = lines[i + 3] if i + 3 < len(lines) else ""
        if not title or len(title) < 3 or title in ("Подробнее", "Реклама"):
            i += 1
            continue
        if SKIP_TITLE.search(title):
            i += 3
            continue
        country = city = ""
        cm = re.match(r"([^,]+),\s*([^,]+)", loc)
        if cm:
            country, city = cm.group(1).strip(), cm.group(2).strip()
        blob = f"{title} {desc} {loc}"
        if not APK_HINT.search(blob):
            i += 3
            continue
        ev = make_event(
            title, d1, d2, "https://expomap.ru/", source,
            city=city, country=country or "Россия", etype="Выставка",
            extra_vert="агро сельхоз пищевая " + desc, description=desc[:200],
        )
        if ev:
            out.append(force_apk(ev))
        i += 3
    seen: set[tuple] = set()
    uniq = []
    for e in out:
        k = (e["title"].lower()[:50], e["starts_at"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def ingest_expomap_samples() -> list[dict]:
    out: list[dict] = []
    for p in sorted(SAMPLES.glob("apk_pw_expomap_*.txt")):
        out.extend(parse_expomap_listing_text(p.read_text("utf-8", errors="replace")))
    seen: set[str] = set()
    uniq = []
    for e in out:
        k = dedupe_key(e)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# ---- agroday seminars via PW (optional) ----
def pw_fetch(url: str, stem: str, wait_ms: int = 8000, commit: bool = False) -> tuple[str, str]:
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
        page.set_default_timeout(90000)
        try:
            page.goto(url, wait_until="commit" if commit else "domcontentloaded", timeout=60000)
            page.wait_for_timeout(wait_ms)
            for _ in range(10):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(350)
            html = page.content()
            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            (SAMPLES / f"{stem}.html").write_text(html, encoding="utf-8")
            (SAMPLES / f"{stem}.txt").write_text(text, encoding="utf-8")
            return html, text
        finally:
            browser.close()


def ingest_agroday_seminars() -> list[dict]:
    out: list[dict] = []
    text = ""
    try:
        _html, text = pw_fetch(
            "https://agroday.ru/seminars/", "apk_pw_agroday_seminars",
            wait_ms=12000, commit=True,
        )
        print(f"  agroday seminars PW: text={len(text)}")
    except Exception as e:
        print(f"  agroday seminars PW: {type(e).__name__}: {e}")
    for name in ("apk_pw_agroday_seminars.txt", "apk_pw_agroday_seminars2.txt"):
        p = SAMPLES / name
        if (not text) and p.exists() and p.stat().st_size > 200:
            text = p.read_text("utf-8", errors="replace")
    if not text:
        return out
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"(\d{1,2}\s+\w+\s*[—–-]\s*\d{1,2}\s+\w+)\s*\|\s*(.+)$", ln)
        if not m:
            i += 1
            continue
        date_s, city = m.group(1), m.group(2).strip()
        j = i + 1
        while j < len(lines) and not lines[j]:
            j += 1
        title = lines[j] if j < len(lines) else ""
        j += 1
        while j < len(lines) and not lines[j]:
            j += 1
        desc = ""
        if j < len(lines) and not re.match(r"\d{1,2}\s+\w+\s*[—–-]", lines[j]):
            desc = lines[j]
        t = date_s.lower().replace("–", "-").replace("—", "-")
        mm = re.search(r"(\d{1,2})\s+([a-zа-я]+)\s*-\s*(\d{1,2})\s+([a-zа-я]+)", t, re.I)
        year = 2027 if "2027" in title else 2026
        if mm:
            mo1, mo2 = mon(mm.group(2)), mon(mm.group(4))
            if mo1 and mo2:
                try:
                    d1 = date(year, mo1, int(mm.group(1)))
                    d2 = date(year, mo2, int(mm.group(3)))
                except ValueError:
                    i = j if j > i else i + 1
                    continue
                etype = "Форум" if re.search(r"форум", title, re.I) else "Семинар"
                ev = make_event(
                    clean_title(title), d1, d2, "https://agroday.ru/seminars/", "agroday_seminars",
                    city=city, etype=etype, extra_vert="агро сельхоз семинар " + desc,
                    description=desc[:200],
                )
                if ev:
                    out.append(force_apk(ev))
        i = j if j > i else i + 1
    return out


def soft_remap_apk(events: list[dict]) -> int:
    changed = 0
    broader = re.compile(
        r"агро|сельхоз|сельск|пищев|ветер|ферм|зерн|молок|мясн|птице|комбикорм|"
        r"день\s+поля|садов|огород|теплич|урожа|продэкспо|югагро|золотая\s+нива|"
        r"horeca|food\b|dairy|meat|agro|niva|нива|растениевод|животн|пчело|"
        r"винодел|рыбн|аквакульт|овоще|fruit|wine|beer|пив|фазенда|"
        r"foodexpo|владпрод|worldfood|dairytech|кормвет|agravia|iagri|пищёвк|пищевк|"
        r"интекпром|megustro",
        re.I,
    )
    for e in events:
        if e.get("vertical") == "apk":
            continue
        blob = f"{e.get('title','')} {e.get('description','')} {e.get('type','')}"
        if re.search(r"православ|церков|храм", blob, re.I) and not APK_HINT.search(blob):
            continue
        if APK_RE.search(blob) or SOFT_APK_RE.search(blob) or broader.search(blob):
            e["vertical"] = "apk"
            changed += 1
    return changed


def enrich_agromagazine_flagships(events: list[dict]) -> int:
    path = SAMPLES / "apk_agromagazine_expo.html"
    if not path.exists() or path.stat().st_size < 800:
        return 0
    text = strip_tags(path.read_text("utf-8", errors="replace"))
    enriched = 0
    for e in events:
        if e.get("vertical") != "apk":
            continue
        if (e.get("description") or "").strip():
            continue
        title = e.get("title") or ""
        token = title.split()[0] if title else ""
        if len(token) >= 4 and token.lower() in text.lower():
            e["description"] = f"Агро-выставка (карточка agromagazine.ru/expo/). {title}"[:200]
            enriched += 1
    return enriched


def run_apk400(existing: Optional[list[dict]] = None, fetch_live: bool = True) -> tuple[list[dict], dict]:
    if existing is None:
        path = ROOT / "events_upcoming.json"
        existing = json.loads(path.read_text("utf-8")) if path.exists() else []
    before_apk = sum(1 for e in existing if e.get("vertical") == "apk")
    stats: dict = {"before_apk": before_apk, "before_n": len(existing)}

    rynok = ingest_rynok(fetch_live=fetch_live)
    existing, n = merge_into(existing, rynok)
    stats["rynok_parsed"] = len(rynok)
    stats["rynok_added"] = n

    expo = ingest_exponet_agri(fetch_live=fetch_live)
    existing, n = merge_into(existing, expo)
    stats["exponet_agri_parsed"] = len(expo)
    stats["exponet_agri_added"] = n

    ec = ingest_expocalendar_samples()
    existing, n = merge_into(existing, ec)
    stats["expocalendar_parsed"] = len(ec)
    stats["expocalendar_added"] = n

    em = ingest_expomap_samples()
    existing, n = merge_into(existing, em)
    stats["expomap_parsed"] = len(em)
    stats["expomap_added"] = n

    sem = ingest_agroday_seminars()
    existing, n = merge_into(existing, sem)
    stats["agroday_seminars_parsed"] = len(sem)
    stats["agroday_seminars_added"] = n

    stats["soft_remap"] = soft_remap_apk(existing)
    stats["agromagazine_enriched"] = enrich_agromagazine_flagships(existing)

    existing = [e for e in existing if (e.get("ends_at") or "") >= TODAY.isoformat()]
    after_apk = sum(1 for e in existing if e.get("vertical") == "apk")
    stats["after_apk"] = after_apk
    stats["after_n"] = len(existing)
    stats["apk_delta"] = after_apk - before_apk
    return existing, stats


def write_outputs(events: list[dict]) -> dict:
    events = [e for e in events if (e.get("ends_at") or "") >= TODAY.isoformat()]
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
            "summary": (e.get("description") or "").strip(),
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


if __name__ == "__main__":
    os.environ.pop("SKIP_PW", None)
    events, stats = run_apk400(fetch_live=True)
    meta = write_outputs(events)
    stats["updated_at"] = meta["updated_at"]
    stats["count"] = meta["count"]
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"FINAL N={meta['count']} apk={stats['after_apk']} delta={stats['apk_delta']}")
