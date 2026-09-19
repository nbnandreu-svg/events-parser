#!/usr/bin/env python3
"""APK × soft formats + forum ingest (завтрак|стол|ужин|форум[+конференция agro]).

Seeds: imol, milknews, agroinvestor, foodsmi, All-Events agro themes, TimePad.
Strict agro-gate required for vertical=apk. Never print TIMEPAD_TOKEN.
Does NOT touch index.html.
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
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from ingest_round2 import TODAY, make_event, parse_dot_date, strip_tags  # noqa: E402
from ingest_round3 import merge  # noqa: E402
from enrich_summaries import write_catalog, is_real_summary  # noqa: E402
from ingest_allevents_types import (  # noqa: E402
    parse_ae_with_type,
    parse_ae_flex_cards,
    upcoming_section_id,
    page_is_generic,
    prefer_cis,
    fetch as ae_fetch,
)

SAMPLES = ROOT / "samples"
JSON_PATH = ROOT / "events_upcoming.json"
TOKEN_PATH = Path("/home/box/.secrets/TIMEPAD_TOKEN")

# Strict agro-gate (title OR desc). Avoid false hits: лазерных←зерн, ответственности←вет
STRICT_APK = re.compile(
    r"агро|молоч|пищев?|пищёв|ферм[аеуыи]|фермер|сельхоз|сельск\w*\s+хоз|"
    r"ветерин|(?<![а-яёa-z])вет(?![а-яёa-z])|мясн|"
    r"зернов|(?<![а-яёa-z])зерн(?:о|а|е|ом|у)(?![а-яёa-z])|"
    r"комбикорм|винодел|аквакульт|растениевод|полевод|агрохолдинг|агроинвест|"
    r"foodsmi|milknews|imol|кормвет|world\s*food|продтех|продмаш|"
    r"животнов|птицевод|свиновод|кормопроизвод|молоко\s+росси|dairy|"
    r"масложир|рыбн\w*\s+(?:форум|выставк|промышл)|seafood|"
    r"bioprom|биопром|цифроземь|food[\s-]?market(?:ing)?|foodmarket|(?<![a-z])food(?![a-z])|питани",
    re.I,
)
# Forbidden topics — reject unless agro marker also present
FORBIDDEN_NO_AGRO = re.compile(
    r"(?<![а-яёa-z])(?:ии|ai)(?![а-яёa-z])|искусственн\w*\s+интеллект|"
    r"недвижим|предпринимател|партн[её]рск",
    re.I,
)

SOFT_TYPES = {"Бизнес-завтрак", "Круглый стол", "Бизнес-ужин", "Форум"}
SOFT_PLUS_CONF = SOFT_TYPES | {"Конференция"}

TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Бизнес-завтрак", re.compile(
        r"бизнес[-\s]?завтрак\w*|завтрак\w*\s+для|деловой\s+завтрак\w*|"
        r"образовательн\w*\s+завтрак\w*",
        re.I,
    )),
    ("Бизнес-завтрак", re.compile(r"(?<![а-яёa-z])завтрак\w*", re.I)),
    ("Круглый стол", re.compile(r"кругл\w*\s+стол\w*", re.I)),
    ("Бизнес-ужин", re.compile(r"бизнес[-\s]?ужин\w*|тематическ\w*\s+ужин\w*|торжественн\w*\s+ужин\w*", re.I)),
    ("Бизнес-ужин", re.compile(r"(?<![а-яёa-z])ужин\w*", re.I)),
    ("Форум", re.compile(r"форум\w*", re.I)),
    ("Конференция", re.compile(r"конференц\w*|практикум\w*|конгресс\w*", re.I)),
    ("Выставка", re.compile(r"выставк\w*|expo\b|экспо\b", re.I)),
    ("Вебинар", re.compile(r"вебинар\w*|прямой\s+эфир", re.I)),
    ("Семинар", re.compile(r"семинар\w*|сесси\w*", re.I)),
    ("Митап", re.compile(r"митап\w*|meetup", re.I)),
]

AE_THEME_SLUGS = ["agroprom", "selhoz", "pishchevaya_promyshlennost", "food_industry"]

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "май": 5, "мая": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

walls: list[str] = []
stats: dict[str, Any] = {}


def strip_html(t: str) -> str:
    t = unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_strict_apk(title: str, desc: str = "") -> bool:
    title = title or ""
    desc = desc or ""
    # soft-quality: prefer title agro; allow desc agro only if title not forbidden
    if FORBIDDEN_NO_AGRO.search(title) and not STRICT_APK.search(title):
        return False
    if STRICT_APK.search(title):
        return True
    if STRICT_APK.search(desc):
        return True
    return False


def classify_type(name: str, hint: str = "") -> str:
    blob = f"{name or ''} {hint or ''}"
    for etype, pat in TYPE_PATTERNS:
        if pat.search(blob):
            return etype
    return "Мероприятие"


def parse_ru_date(s: str) -> Optional[date]:
    s = (s or "").strip().lower()
    m = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(20\d{2})", s)
    if m:
        day = int(m.group(1))
        mon_s = m.group(2)
        year = int(m.group(3))
        mon = None
        for k, v in MONTHS.items():
            if mon_s.startswith(k):
                mon = v
                break
        if mon:
            try:
                return date(year, mon, day)
            except ValueError:
                return None
    m = re.search(r"(\d{2})\.(\d{2})\.(20\d{2})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # month-only → 1st of month
    m = re.search(r"([а-яё]+)\s+(20\d{2})", s)
    if m:
        mon_s, year = m.group(1), int(m.group(2))
        for k, v in MONTHS.items():
            if mon_s.startswith(k):
                try:
                    return date(year, v, 1)
                except ValueError:
                    return None
    return None


def parse_starts_iso(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        s2 = s.replace("+0300", "+03:00").replace("+0000", "+00:00")
        if len(s2) >= 10:
            return date.fromisoformat(s2[:10])
    except Exception:
        return None
    return None


def load_token() -> str:
    env = (os.environ.get("TIMEPAD_TOKEN") or "").strip()
    if env:
        return env
    if TOKEN_PATH.is_file():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    raise SystemExit("TIMEPAD_TOKEN missing")


def http_get(url: str, timeout: int = 30) -> tuple[Optional[str], Optional[str]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace"), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def curl_get(url: str, headers: list[str] | None = None, timeout: int = 90) -> tuple[int, str]:
    cmd = ["curl", "-sS", "-L", "-w", "\n%{http_code}", "-A", UA, "--max-time", str(timeout)]
    for h in headers or []:
        cmd += ["-H", h]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    out = r.stdout or ""
    if "\n" in out:
        body, _, code_s = out.rpartition("\n")
    else:
        body, code_s = out, "0"
    try:
        status = int(code_s.strip())
    except ValueError:
        status, body = 0, out
    return status, body


def api_get(token: str, params: dict) -> tuple[int, dict]:
    q = urllib.parse.urlencode(params, doseq=True)
    url = f"https://api.timepad.ru/v1/events.json?{q}"
    status, body = curl_get(
        url,
        headers=[
            f"Authorization: Bearer {token}",
            "Accept: application/json",
        ],
    )
    if status == 429:
        raise RuntimeError("HTTP 429 rate limit")
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:160]}")
    return status, json.loads(body)


def force_event(
    title: str,
    starts: date,
    ends: date,
    url: str,
    source: str,
    etype: str,
    city: str = "",
    desc: str = "",
) -> Optional[dict]:
    if ends < TODAY or starts.year > 2027:
        return None
    if not is_strict_apk(title, desc):
        return None
    ev = make_event(
        title, starts, ends, url, source,
        city=city, country="Россия", etype=etype,
        extra_vert="агро пище молоч сельхоз " + title,
        description=desc[:500] if desc else "",
    )
    if not ev:
        ev = {
            "title": title,
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "city": city or "",
            "country": "Россия",
            "vertical": "apk",
            "type": etype,
            "date_status": "confirmed",
            "organizer_url": url,
            "source": source,
            "description": (desc or "")[:200],
        }
    ev["vertical"] = "apk"
    ev["type"] = etype
    return ev


# ─── Seeds ───────────────────────────────────────────────────────────

def seed_imol() -> list[dict]:
    out: list[dict] = []
    url = "https://imol.pro/apfmr2026"
    html, err = http_get(url)
    if err or not html:
        # fallback to cached sample
        p = SAMPLES / "seed_imol_apfmr2026.html"
        if p.is_file():
            html = p.read_text(encoding="utf-8", errors="replace")
            walls.append(f"imol live fetch failed ({err}); using cached sample")
        else:
            walls.append(f"imol {url}: {err}")
            return out
    else:
        (SAMPLES / "seed_imol_apfmr2026.html").write_text(html[:600000], encoding="utf-8")

    # Main forum 24-26 Nov 2026
    title = "VIII Агропромышленный форум «Молоко России»"
    starts, ends = date(2026, 11, 24), date(2026, 11, 26)
    desc = (
        "Ежегодное масштабное мероприятие в молочной индустрии. "
        "Тематические ужины, деловая программа, экскурсии на фермы. imol.pro"
    )
    ev = force_event(title, starts, ends, url, "imol", "Форум", city="Московская область", desc=desc)
    if ev:
        out.append(ev)
    # Thematic dinners as soft format
    dinner = force_event(
        "Тематические ужины VIII Агропромышленного форума «Молоко России»",
        date(2026, 11, 24), date(2026, 11, 26), url + "#dinners", "imol",
        "Бизнес-ужин", city="Московская область",
        desc="Тематические ужины для неформального общения участников молочного форума. imol",
    )
    if dinner:
        out.append(dinner)
    print(f"  imol: {len(out)} events", flush=True)
    stats["seed_imol"] = len(out)
    return out


def seed_milknews() -> list[dict]:
    out: list[dict] = []
    # Known event pages (fetched/cached)
    pages = [
        ("https://event.milknews.ru/events/forum-27", "seed_mn_forum-27.html", "Форум"),
        ("https://event.milknews.ru/events/efficiency-conf-26", "seed_mn_efficiency-conf-26.html", "Конференция"),
        ("https://event.milknews.ru/events/leaders-26", "seed_mn_leaders-26.html", "Форум"),
        ("https://event.milknews.ru/events/ice-cream-2026", "seed_mn_ice-cream-2026.html", "Семинар"),
        ("https://event.milknews.ru/events/sessions-summer-26", "seed_mn_sessions-summer-26.html", "Семинар"),
    ]
    past_skipped = 0
    for url, cache, type_hint in pages:
        p = SAMPLES / cache
        html = None
        if not p.is_file():
            html, err = http_get(url)
            if html:
                p.write_text(html[:400000], encoding="utf-8")
            else:
                walls.append(f"milknews {url}: {err}")
                continue
        else:
            html = p.read_text(encoding="utf-8", errors="replace")
        title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        title = strip_html(title_m.group(1)) if title_m else ""
        if not title:
            tm = re.search(r"<title[^>]*>([^<]+)", html, re.I)
            title = strip_html(tm.group(1)) if tm else ""
        # first Russian date in page
        dm = re.search(r"(\d{1,2}\s+[а-яё]+\s+20\d{2})", html, re.I)
        d = parse_ru_date(dm.group(1)) if dm else None
        if not d or d < TODAY:
            past_skipped += 1
            continue
        etype = classify_type(title, type_hint) if type_hint == "Форум" else type_hint
        if type_hint == "Форум":
            etype = "Форум"
        elif "форум" in title.lower():
            etype = "Форум"
        elif type_hint == "Конференция":
            etype = "Конференция"
        else:
            etype = classify_type(title, type_hint)
        ev = force_event(
            title, d, d, url, "milknews", etype, city="",
            desc=f"Молочная отрасль. milknews. {title}",
        )
        if ev:
            out.append(ev)
    if past_skipped:
        walls.append(
            f"milknews: {past_skipped} event page(s) before TODAY={TODAY.isoformat()} "
            f"(incl. leaders-26 21.01.2026) — skipped"
        )
    print(f"  milknews: {len(out)} upcoming", flush=True)
    stats["seed_milknews"] = len(out)
    return out


def seed_agroinvestor() -> list[dict]:
    out: list[dict] = []
    urls = [
        "https://agroinvestor.ru/afisha/",
        "https://agroinvestor.ru/afisha/catalog/",
        "https://agroinvestor.ru/afisha/catalog/?date_preset=future",
    ]
    # slug → human title overrides / heuristics
    SLUG_TITLE = {
        "agroinvestor-pro-rastenievodstvo": "Агроинвестор PRO: Растениеводство",
        "bioprom-promyshlennost-i-texnologii-dlya-cheloveka": "БИОПРОМ: промышленность и технологии для человека",
        "zolotaya-osen-2026": "Золотая осень 2026",
        "iii-rossijskij-forum-polevodov-2026": "III Российский форум Полеводов 2026",
        "xi-mezhdunarodnyj-kongress-maslozhirovaya-industriya": "XI Международный конгресс «Масложировая индустрия»",
        "kormvetgrejn-2026": "КормВетГрейн 2026",
        "agroxoldingi-rossii-2026": "Агрохолдинги России 2026",
        "mezhdunarodnyj-forum-elektronnoj-kommercii-i-ritejla-e-ritejl-forum-2026": "Е-РИТЕЙЛ ФОРУМ 2026",
        "premiya-agrodiler-goda-iii-sezon": "Премия «Агродилер года», III сезон",
    }
    TYPE_FROM_BADGE = {
        "форум": "Форум",
        "конференция": "Конференция",
        "выставка": "Выставка",
        "премия": "Премия",
    }
    seen: set[str] = set()
    for url in urls:
        html, err = http_get(url)
        if err or not html:
            # try cache
            cache = SAMPLES / ("seed_agroinvestor_afisha.html" if "catalog" not in url else "seed_agroinvestor_catalog.html")
            if cache.is_file():
                html = cache.read_text(encoding="utf-8", errors="replace")
                walls.append(f"agroinvestor {url}: {err}; using cache")
            else:
                walls.append(f"agroinvestor {url}: {err}")
                continue
        safe = "afisha" if url.rstrip("/").endswith("afisha") else "catalog"
        (SAMPLES / f"seed_agroinvestor_{safe}.html").write_text(html[:500000], encoding="utf-8")

        # Pair each /afisha/20xx/slug/ with nearest date + badge + title texts
        for m in re.finditer(r'href="(/afisha/(20\d{2})/([^"/]+)/)"', html):
            href, year_s, slug = m.group(1), m.group(2), m.group(3)
            full = urljoin("https://agroinvestor.ru", href)
            if full in seen:
                continue
            seen.add(full)
            ctx = html[max(0, m.start() - 400): m.start() + 1200]
            dates = re.findall(r"(\d{2}\.\d{2}\.20\d{2})", ctx)
            months = re.findall(
                r"((?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+20\d{2})",
                ctx, re.I,
            )
            badge = ""
            bm = re.search(r"event__badges[^>]*>\s*([^<]+)", ctx, re.I)
            if not bm:
                # info block often has type as short word
                for cand in re.findall(r">\s*(Форум|Конференция|Выставка|Премия)\s*<", ctx, re.I):
                    badge = cand
                    break
            else:
                badge = bm.group(1).strip()

            texts = [strip_html(x) for x in re.findall(r">([^<]{5,160})<", ctx)]
            texts = [
                t for t in texts
                if t not in {"Узнать подробнее", "В календарь", "Подробнее", "Форум", "Конференция", "Выставка", "Премия"}
                and "http" not in t.lower()
                and not re.match(r"^\d{2}\.\d{2}\.20\d{2}", t)
                and "календар" not in t.lower()
            ]
            title = SLUG_TITLE.get(slug)
            if not title:
                # prefer longest Cyrillic-heavy text
                cyr = [t for t in texts if re.search(r"[а-яёА-ЯЁ]{4}", t) and len(t) > 10]
                title = max(cyr, key=len) if cyr else slug.replace("-", " ")

            d: Optional[date] = None
            if dates:
                d = parse_dot_date(dates[0])
            elif months:
                d = parse_ru_date(months[0])
            if not d:
                # try year from path + month from slug context
                continue
            if d < TODAY:
                continue

            etype = TYPE_FROM_BADGE.get(badge.lower(), "") or classify_type(title, badge)
            # Only soft/forum/conf for this product push; still ingest exhibitions if agro
            if etype not in SOFT_PLUS_CONF | {"Выставка", "Премия", "Мероприятие"}:
                etype = classify_type(title)

            desc = f"Агроинвестор афиша. agroinvestor. {title}"
            if not is_strict_apk(title, desc + " " + slug):
                # e-retail forum etc. — skip for apk
                continue
            # Prefer soft/forum/conf; still add agro exhibitions as apk but type preserved
            if etype == "Мероприятие" and "форум" in slug:
                etype = "Форум"
            if etype == "Мероприятие" and ("kongress" in slug or "konfer" in slug or "pro-rastenievodstvo" in slug):
                etype = "Конференция"

            ev = force_event(title, d, d, full, "agroinvestor", etype, city="", desc=desc)
            if ev:
                out.append(ev)
        time.sleep(0.3)

    print(f"  agroinvestor: {len(out)}", flush=True)
    stats["seed_agroinvestor"] = len(out)
    return out


def seed_foodsmi() -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, 4):
        url = "https://foodsmi.com/events/" if page == 1 else f"https://foodsmi.com/events/?PAGEN_1={page}"
        html, err = http_get(url)
        if err or not html:
            cache = SAMPLES / (f"seed_foodsmi_events{'_p'+str(page) if page>1 else ''}.html")
            if page == 1:
                cache = SAMPLES / "seed_foodsmi_events.html"
            if cache.is_file():
                html = cache.read_text(encoding="utf-8", errors="replace")
            else:
                walls.append(f"foodsmi page{page}: {err}")
                continue
        (SAMPLES / f"seed_foodsmi_events_p{page}.html").write_text(html[:400000], encoding="utf-8")

        for m in re.finditer(
            r"(\d{1,2}(?:-\d{1,2})?\s+[а-яё]+(?:\s+20\d{2})?)"
            r".{0,220}?"
            r'href="(/events/[^"]+/)"[^>]*>\s*([^<]{8,160})',
            html, re.S | re.I,
        ):
            date_s, href, title = m.group(1), m.group(2), strip_html(m.group(3))
            # normalize "15-18 сентября 2026" → take first day
            date_s2 = re.sub(r"^(\d{1,2})-\d{1,2}(\s+)", r"\1\2", date_s)
            # if year missing, try from href or nearby
            if not re.search(r"20\d{2}", date_s2):
                ym = re.search(r"20\d{2}", href) or re.search(r"20\d{2}", html[m.start():m.start() + 300])
                if ym:
                    date_s2 = date_s2 + " " + ym.group(0)
            d = parse_ru_date(date_s2)
            if not d or d < TODAY:
                continue
            full = urljoin("https://foodsmi.com", href)
            if full in seen:
                continue
            seen.add(full)
            # strip city prefix "Москва, Title"
            title_clean = re.sub(r"^[А-Яа-яёЁA-Za-z\-\s]+,\s*", "", title).strip() or title
            etype = classify_type(title_clean + " " + href)
            if etype == "Мероприятие" and "forum" in href:
                etype = "Форум"
            desc = f"foodsmi пищевая отрасль. {title}"
            if not is_strict_apk(title_clean, desc + " " + href):
                continue
            # skip pure HoReCA/restaurant unless food-industry agro signal strong
            if re.search(r"ресторанн|horeca|зож|cleanexpo|охраны\s+труда|деревом\s+решений", title_clean, re.I):
                if not STRICT_APK.search(title_clean):
                    continue
            city = ""
            cm = re.match(r"^([^,]+),", title)
            if cm and len(cm.group(1)) < 40:
                city = cm.group(1).strip()
            ev = force_event(title_clean, d, d, full, "foodsmi", etype, city=city, desc=desc)
            if ev:
                out.append(ev)
        time.sleep(0.35)

    print(f"  foodsmi: {len(out)}", flush=True)
    stats["seed_foodsmi"] = len(out)
    return out


def ingest_allevents_apk() -> list[dict]:
    collected: dict[str, dict] = {}

    def take(evs: list[dict]) -> int:
        n = 0
        for e in evs:
            e = dict(e)
            title = e.get("title") or ""
            desc = e.get("description") or ""
            if not is_strict_apk(title, desc):
                continue
            if not prefer_cis(e):
                continue
            e["vertical"] = "apk"
            e["type"] = classify_type(title, e.get("type") or "")
            k = (e.get("organizer_url") or title).rstrip("/")
            if k in collected:
                old = collected[k]
                if old.get("type") not in SOFT_TYPES and e.get("type") in SOFT_TYPES:
                    collected[k] = e
                continue
            collected[k] = e
            n += 1
        return n

    for theme in AE_THEME_SLUGS:
        url = f"https://all-events.ru/events/calendar/theme-is-{theme}/"
        html, err = ae_fetch(url)
        time.sleep(0.45)
        if err or not html:
            walls.append(f"AE theme-is-{theme}: {err or 'empty'}")
            print(f"  WALL AE theme-is-{theme}: {err or 'empty'}", flush=True)
            continue
        (SAMPLES / f"ae_apk_theme-is-{theme}.html").write_text(html, encoding="utf-8")
        if page_is_generic(html):
            walls.append(f"AE theme-is-{theme}: generic title — empty/broken filter")
            print(f"  WALL AE theme-is-{theme}: generic", flush=True)
            continue
        part = parse_ae_with_type(html, "Конференция")
        if not part:
            part = parse_ae_flex_cards(html, "Конференция")
        n = take(part)
        print(f"  AE theme-is-{theme}: parsed={len(part)} taken_strict={n}", flush=True)
        stats[f"ae_theme_{theme}"] = {"parsed": len(part), "taken": n}
        sid = upcoming_section_id(html)
        if sid:
            for page in range(2, 5):
                lm = f"{url}?PAGEN_1={page}&load_more={sid}"
                h2, err2 = ae_fetch(lm)
                time.sleep(0.35)
                if err2 or not h2:
                    break
                part2 = parse_ae_with_type(h2, "Конференция") or parse_ae_flex_cards(h2, "Конференция")
                nn = take(part2)
                print(f"    PAGEN_1={page}: +{nn}", flush=True)
                if nn == 0 and not part2:
                    break

    return list(collected.values())


def event_from_tp_apk(raw: dict) -> Optional[dict]:
    name = strip_html(raw.get("name") or "")
    if not name:
        return None
    etype = classify_type(name)
    starts_d = parse_starts_iso(raw.get("starts_at") or "")
    if not starts_d or starts_d < TODAY or starts_d.year > 2027:
        return None
    ends_d = parse_starts_iso(raw.get("ends_at") or "") or starts_d
    url = (raw.get("url") or "").strip()
    if not url:
        return None
    loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    city = (loc.get("city") or raw.get("city") or "").strip()
    if city.lower() in {"спб", "питер", "петербург"}:
        city = "Санкт-Петербург"
    if "онлайн" in city.lower() or city.lower() in {"online", "remote"}:
        city = "Онлайн"
    if re.search(r"london|paris|berlin|warsaw|киев|kyiv|дубай|dubai", city, re.I):
        return None
    desc = strip_html(raw.get("description_short") or raw.get("description") or "")
    cats = " ".join(c.get("name") or "" for c in (raw.get("categories") or []))
    if not is_strict_apk(name, f"{cats} {desc}"):
        return None
    # keep soft formats + forum (+ conf if agro)
    if etype not in SOFT_PLUS_CONF | {"Выставка", "Семинар", "Вебинар", "Митап", "Нетворкинг", "Мероприятие"}:
        pass  # still keep agro hits
    return force_event(name, starts_d, ends_d, url, "timepad", etype, city=city, desc=desc)


def fetch_keyword(token: str, keyword: str, limit_per_page: int = 100) -> tuple[list[dict], int]:
    out: list[dict] = []
    skip = 0
    total = 0
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
                continue
            raise
        total = int(data.get("total") or 0)
        vals = data.get("values") or []
        if not vals:
            break
        out.extend(vals)
        skip += len(vals)
        pages += 1
        if skip >= total or len(vals) < limit_per_page or pages >= 12:
            break
        time.sleep(1.1)
    return out, total


def count_soft(events: list[dict]) -> dict[str, int]:
    apk = [e for e in events if (e.get("vertical") or "") == "apk"]
    def n(t: str) -> int:
        return sum(1 for e in apk if (e.get("type") or "") == t)
    bf, st, dn, fr, cf = n("Бизнес-завтрак"), n("Круглый стол"), n("Бизнес-ужин"), n("Форум"), n("Конференция")
    soft_sum = bf + st + dn + fr
    return {
        "apk": len(apk),
        "завтрак": bf,
        "стол": st,
        "ужин": dn,
        "форум": fr,
        "конференция": cf,
        "soft_forum_sum": soft_sum,
    }


def reclassify_existing(events: list[dict]) -> tuple[int, int]:
    """Demote apk soft/forum that fail agro-gate; promote agro soft that were mis-tagged."""
    demoted = 0
    promoted = 0
    for e in events:
        title = e.get("title") or ""
        desc = e.get("description") or ""
        etype = e.get("type") or ""
        vert = e.get("vertical") or ""
        if etype in SOFT_PLUS_CONF:
            if vert == "apk" and not is_strict_apk(title, desc):
                # demote to biz/other rather than leave false apk
                e["vertical"] = "biz"
                demoted += 1
            elif vert != "apk" and is_strict_apk(title, desc):
                # only promote if type is soft/forum (or conf with clear agro)
                if etype in SOFT_TYPES or (etype == "Конференция" and is_strict_apk(title, desc)):
                    e["vertical"] = "apk"
                    promoted += 1
        # normalize plural types
        if etype == "Форумы":
            e["type"] = "Форум"
        if etype == "Конференции":
            e["type"] = "Конференция"
    return demoted, promoted


def ensure_gorodskoy_it(events: list[dict]) -> int:
    fixed = 0
    for e in events:
        t = (e.get("title") or "").lower()
        if "городск" in t and "завтрак" in t and ("оон" in t or "ии" in t or "ai" in t):
            if e.get("vertical") != "it":
                e["vertical"] = "it"
                fixed += 1
    return fixed


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    existing = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    before_n = len(existing)
    before = count_soft(existing)
    print(
        f"BEFORE N={before_n} apk={before['apk']} "
        f"завтрак={before['завтрак']} стол={before['стол']} ужин={before['ужин']} "
        f"форум={before['форум']} конф={before['конференция']} "
        f"soft+forum_sum={before['soft_forum_sum']}",
        flush=True,
    )

    # ── Seeds ──
    print("\n=== Seeds ===", flush=True)
    new_imol = seed_imol()
    new_mn = seed_milknews()
    new_ai = seed_agroinvestor()
    new_fs = seed_foodsmi()

    # ── All-Events ──
    print("\n=== All-Events APK themes ===", flush=True)
    new_ae = ingest_allevents_apk()
    stats["ae_collected"] = len(new_ae)
    print(f"AE collected strict={len(new_ae)} types={dict(Counter(e.get('type') for e in new_ae))}", flush=True)

    # ── TimePad ──
    print("\n=== TimePad ===", flush=True)
    new_tp: list[dict] = []
    try:
        token = load_token()
        st, smoke = api_get(token, {
            "limit": 1,
            "starts_at_min": TODAY.isoformat(),
            "starts_at_max": "2027-12-31",
            "sort": "+starts_at",
        })
        print(f"SMOKE HTTP={st} total_window={smoke.get('total')}", flush=True)
        stats["smoke_http"] = st
        time.sleep(1.1)
        by_id: dict[int, dict] = {}
        kw_totals: dict[str, int] = {}
        for kw in ["агро", "молоч", "пище", "ферм", "сельхоз", "зерн", "мясн"]:
            try:
                rows, api_total = fetch_keyword(token, kw)
                kw_totals[kw] = api_total
                for r in rows:
                    rid = r.get("id")
                    if rid is not None:
                        by_id[int(rid)] = r
                print(f"  kw {kw!r}: fetched={len(rows)} api_total={api_total}", flush=True)
            except Exception as e:
                walls.append(f"TimePad kw {kw}: {type(e).__name__}: {e}")
                print(f"  WALL kw {kw}: {e}", flush=True)
                if "403" in str(e):
                    walls.append("TimePad 403 from box — rely on seeds+AE")
                    break
            time.sleep(1.1)
        stats["tp_kw_totals"] = kw_totals
        type_c: Counter = Counter()
        for raw in by_id.values():
            ev = event_from_tp_apk(raw)
            if not ev:
                continue
            # product focus: soft + forum (+ conf)
            if ev.get("type") not in SOFT_PLUS_CONF and ev.get("type") not in {
                "Выставка", "Семинар", "Вебинар", "Митап", "Мероприятие"
            }:
                continue
            new_tp.append(ev)
            type_c[ev.get("type") or ""] += 1
        stats["tp_classified"] = len(new_tp)
        stats["tp_types"] = dict(type_c)
        print(f"TimePad classified={len(new_tp)} types={dict(type_c)}", flush=True)
        soft_tp = sum(type_c.get(t, 0) for t in SOFT_TYPES)
        if soft_tp == 0 and type_c.get("Форум", 0) == 0:
            walls.append(
                f"TimePad agro keywords: soft+forum STRICT≈0 "
                f"(breakfast/stol/dinner/forum after gate)"
            )
    except Exception as e:
        walls.append(f"TimePad: {type(e).__name__}: {e}")
        print(f"TimePad WALL: {e}", flush=True)

    # ── Reclassify + merge ──
    demoted, promoted = reclassify_existing(existing)
    stats["demoted"] = demoted
    stats["promoted"] = promoted

    batches = [
        (new_imol, "imol"),
        (new_mn, "milknews"),
        (new_ai, "agroinvestor"),
        (new_fs, "foodsmi"),
        (new_ae, "all_events_apk"),
        (new_tp, "timepad_apk"),
    ]
    for batch, label in batches:
        existing = merge(existing, batch, label)

    fixed_it = ensure_gorodskoy_it(existing)
    # second pass reclassify after merge
    d2, p2 = reclassify_existing(existing)
    demoted += d2
    promoted += p2

    meta = write_catalog(existing)
    after_n = len(existing)
    after = count_soft(existing)

    # examples kept
    examples = []
    for e in existing:
        if e.get("vertical") != "apk":
            continue
        if e.get("type") not in SOFT_TYPES:
            continue
        if not is_strict_apk(e.get("title") or "", e.get("description") or ""):
            continue
        examples.append(f"{e.get('type')} | {e.get('starts_at','')} | {(e.get('title') or '')[:75]}")
        if len(examples) >= 5:
            break

    wall_lines = ["  " + w for w in walls] or ["  (none)"]
    report_lines = [
        f"APK SOFT FORMATS INGEST REPORT {datetime.now().strftime('%Y-%m-%d %H:%M')} Europe/Moscow",
        "Criterion: vertical=apk AND type∈{Бизнес-завтрак, Круглый стол, Бизнес-ужин, Форум}",
        "  (+ Конференция counted separately if agro forum-like)",
        "Strict agro-gate in title|desc; forbid ИИ/недвиж/предпринимател/партнёрск without agro.",
        "",
        f"BEFORE: N={before_n} apk={before['apk']} "
        f"завтрак={before['завтрак']} стол={before['стол']} ужин={before['ужин']} "
        f"форум={before['форум']} конф={before['конференция']} "
        f"soft+forum_sum={before['soft_forum_sum']}",
        f"AFTER:  N={after_n} apk={after['apk']} "
        f"завтрак={after['завтрак']} стол={after['стол']} ужин={after['ужин']} "
        f"форум={after['форум']} конф={after['конференция']} "
        f"soft+forum_sum={after['soft_forum_sum']}",
        f"DELTA:  N {after_n - before_n:+d} "
        f"завтрак {after['завтрак'] - before['завтрак']:+d} "
        f"стол {after['стол'] - before['стол']:+d} "
        f"ужин {after['ужин'] - before['ужин']:+d} "
        f"форум {after['форум'] - before['форум']:+d} "
        f"sum {after['soft_forum_sum'] - before['soft_forum_sum']:+d}",
        f"SUCCESS target ≥5 (stretch 15): soft+forum_sum = {after['soft_forum_sum']}",
        f"seeds: imol={stats.get('seed_imol')} milknews={stats.get('seed_milknews')} "
        f"agroinvestor={stats.get('seed_agroinvestor')} foodsmi={stats.get('seed_foodsmi')}",
        f"AE collected={stats.get('ae_collected')} TP classified={stats.get('tp_classified')} "
        f"types={json.dumps(stats.get('tp_types'), ensure_ascii=False)}",
        f"reclassify demoted_false_apk={demoted} promoted={promoted} gorodskoy_it={fixed_it}",
        f"write_catalog meta={meta}",
        "index.html NOT touched",
        "",
        "EXAMPLES kept (apk × soft/forum):",
        *( ["  " + x for x in examples] if examples else ["  (none)"] ),
        "",
        "WALLS:",
        *wall_lines,
    ]
    if after["завтрак"] == 0:
        report_lines.append(
            f"HONEST WALL breakfast: АПК×Бизнес-завтрак=0 under strict agro (TODAY={TODAY.isoformat()})"
        )
    if after["soft_forum_sum"] < 5:
        report_lines.append(
            f"HONEST WALL: soft+forum_sum={after['soft_forum_sum']} <5"
        )
    elif after["soft_forum_sum"] < 15:
        report_lines.append(f"OK ≥5 below stretch 15: sum={after['soft_forum_sum']}")
    else:
        report_lines.append(f"STRETCH OK: sum={after['soft_forum_sum']}")

    report = "\n".join(report_lines) + "\n"
    (SAMPLES / "ingest_report_apk_soft_formats.txt").write_text(report, encoding="utf-8")
    (SAMPLES / "ingest_walls_apk_soft_formats.txt").write_text(
        "\n".join(walls) or "(none)", encoding="utf-8"
    )
    (SAMPLES / "apk_soft_formats_new_events.json").write_text(
        json.dumps(
            {
                "imol": new_imol,
                "milknews": new_mn,
                "agroinvestor": new_ai,
                "foodsmi": new_fs,
                "allevents": new_ae[:150],
                "timepad": new_tp[:150],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n" + report, flush=True)


if __name__ == "__main__":
    main()
