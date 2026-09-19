#!/usr/bin/env python3
"""Events Calendar ingest round5 — exhaust APK + industry + IT packages."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from html import unescape
from pathlib import Path
from typing import Optional

sys.path.insert(0, "/workspace/events-calendar-local")
from ingest_round2 import (  # noqa: E402
    TODAY, APK_RE, IT_RE,
    strip_tags, vertical_for, parse_dot_date, parse_ru_date_range,
    make_event, parse_exponet_topic, parse_single_event_site, load_html,
    SAMPLES, ROOT, parse_zivot_seed,
)
from ingest_round3 import (  # noqa: E402
    normalize_url, fuzzy_title, dedupe_key, soft_key, merge, write_outputs,
    parse_zivot_full, added_by_source, stats, walls,
)
from ingest_round4 import (  # noqa: E402
    parse_crocus_any, parse_jugru, parse_foodsmi, parse_agrobvk, parse_niva,
    parse_it_single,
)

added_by_source.clear()
stats.clear()
walls.clear()

ICT2GO_IT_THEMES = {
    "ai", "machine_learning", "Data_Science", "IT", "IT_infrastruktura",
    "razrabotka_PO", "informatsionnaya_bezopasnoct", "Cloud_technology",
    "Kubernetes", "devops", "Blockchain", "blockchain", "ITconsulting",
    "IToutsourcing", "Product_Management", "agile", "SOC", "LegalTech",
    "1c", "CRM", "MES", "automat", "it_in_business", "process_mining",
    "bi", "BI", "gamedev", "computer_games", "cosmos_tech", "PO", "UC",
    "SKUD", "Digital_marketing", "SMM", "5g", "iot", "highload", "telecom",
    "IT_v_stroitelstve", "it_v_nedvizhimosti", "Fixed_link",
    "Additive_Manufacturing", "3d_pechat", "frontend", "backend", "qa",
}


def parse_ddmm_yy(s: str) -> Optional[date]:
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", s.strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_ai_dates(s: str) -> Optional[tuple[date, date]]:
    s = unescape(s or "").strip()
    if not s or re.match(r"^[А-Яа-яЁё]+\s+202\d$", s):
        return None
    s = re.sub(r"\s+\d{1,2}:\d{2}", "", s)
    dots = re.findall(r"(\d{2}\.\d{2}\.\d{4})", s)
    if len(dots) >= 2:
        a, b = parse_dot_date(dots[0]), parse_dot_date(dots[1])
        if a and b:
            return a, b
    if len(dots) == 1:
        a = parse_dot_date(dots[0])
        if a:
            return a, a
    return None


def parse_agroinvestor(html: str, source: str = "agroinvestor") -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(
        r'<a class="upcoming-event" href="([^"]+)">\s*<div class="upcoming-event__item[^"]*">(.*?)</div>\s*</a>',
        html, re.S,
    ):
        href, block = m.group(1), m.group(2)
        date_s = re.search(r'event-card__details-item date">([^<]+)', block)
        title_m = re.search(r'upcoming-event__title[^>]*>([^<]+)', block)
        place = re.search(r'event-card__details-item place">([^<]+)', block)
        typ = re.search(r'event-card__details-item type">([^<]+)', block)
        if not (date_s and title_m):
            continue
        rng = parse_ai_dates(date_s.group(1))
        if not rng or rng[1] < TODAY:
            continue
        city = unescape(place.group(1)).split(",")[0].strip() if place else ""
        url = href if href.startswith("http") else "https://www.agroinvestor.ru" + href
        title = unescape(title_m.group(1)).strip()
        et = typ.group(1).strip() if typ else "Мероприятие"
        extra = title
        if APK_RE.search(title) or re.search(
            r"агро|пищ|полевод|растениевод|золот|биопром|маслож|корм|молоч|сельхоз|холдинг",
            title, re.I,
        ):
            extra = f"агро {title}"
        ev = make_event(title, rng[0], rng[1], url, source, city=city, etype=et, extra_vert=extra)
        if ev:
            out.append(ev)
    for m in re.finditer(r'class="event-card"(.*?)(?=class="event-card"|$)', html, re.S):
        block = m.group(1)[:4000]
        href = re.search(r'href="(/afisha/202[67]/[^"/]+/)"', block)
        date_s = re.search(r'event-card__details-item date">([^<]+)', block)
        title_m = re.search(r'aria-label="([^"]+)"', block)
        if not title_m:
            title_m = re.search(r'event-card__title[^>]*>\s*([^<\n]+)', block)
        place = re.search(r'event-card__details-item place">([^<]+)', block)
        typ = re.search(r'event-card__details-item type">([^<]+)', block)
        if not (href and date_s and title_m):
            continue
        rng = parse_ai_dates(date_s.group(1))
        if not rng or rng[1] < TODAY:
            continue
        title = unescape(title_m.group(1)).strip()
        if len(title) < 4 or title.lower().startswith("узнать"):
            continue
        city = unescape(place.group(1)).split(",")[0].strip() if place else ""
        url = "https://www.agroinvestor.ru" + href.group(1)
        et = typ.group(1).strip() if typ else "Мероприятие"
        extra = title
        if APK_RE.search(title) or re.search(
            r"агро|пищ|полевод|растениевод|золот|биопром|маслож|корм|молоч|сельхоз|холдинг",
            title, re.I,
        ):
            extra = f"агро {title}"
        ev = make_event(title, rng[0], rng[1], url, source, city=city, etype=et, extra_vert=extra)
        if ev:
            out.append(ev)
    seen = set()
    uniq = []
    for e in out:
        k = dedupe_key(e)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def parse_souzmoloko(html: str, source: str = "souzmoloko") -> list[dict]:
    text = strip_tags(html)
    out = []
    for m in re.finditer(
        r"(\d{1,2}(?:\s*[–\-—]\s*\d{1,2})?\s+[а-яё]+\s+202[67])\s*(?:года\s+)?"
        r"(.{10,220}?)(?=\d{1,2}(?:\s*[–\-—]\s*\d{1,2})?\s+[а-яё]+\s+202|\Z)",
        text, re.I,
    ):
        date_s, rest = m.group(1), m.group(2)
        yr = 2027 if "2027" in date_s else 2026
        rng = parse_ru_date_range(date_s, yr)
        if not rng or rng[1] < TODAY:
            continue
        if not re.search(
            r"форум|выставк|конференц|вебинар|интенсив|практикум|саммит|экспо|"
            r"семинар|конгресс|молочн|агро|цифровиз|сесси",
            rest, re.I,
        ):
            continue
        qm = re.search(r"[«\"]([^»\"]{5,90})[»\"]", rest)
        if qm:
            title = qm.group(1).strip()
        else:
            tm = re.search(
                r"(?:пройд[её]т|состоится|пройдет)\s+"
                r"((?:III|IV|V|VI|VII|VIII|IX|X|XI|XII|\d+)?\s*"
                r"[A-Za-zА-Яа-яЁё«\"].{5,80}?)(?:\s+С\s+\d|\s+До\s+|\.|$)",
                rest, re.I,
            )
            if not tm:
                continue
            title = tm.group(1).strip(" .,—–-")
        title = re.sub(r"\s+", " ", title).strip(" .")
        if len(title) < 8:
            continue
        if re.search(r"он становится|подписа|обязательн|календарь мер", title, re.I):
            continue
        city = ""
        cm = re.search(r"в\s+(Москве|Сочи|Геленджике|Казани)", rest, re.I)
        if cm:
            city = {
                "москве": "Москва", "сочи": "Сочи",
                "геленджике": "Геленджик", "казани": "Казань",
            }.get(cm.group(1).lower(), "")
        ev = make_event(
            title, rng[0], rng[1],
            "https://souzmoloko.ru/kalendar-meropriyatiy/2026/",
            source, city=city, extra_vert=f"молоко агро {title}", etype="Мероприятие",
        )
        if ev:
            out.append(ev)
    seen = set()
    uniq = []
    for e in out:
        k = fuzzy_title(e["title"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def parse_ridjey(html: str, source: str = "ridjey") -> list[dict]:
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [strip_tags(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) < 2:
            continue
        raw = cells[0].replace(" ", "")
        m = re.match(r"(\d{2}\.\d{2}\.\d{2,4})[-–—](\d{2}\.\d{2}\.\d{2,4})", raw)
        if not m:
            continue
        a, b = parse_ddmm_yy(m.group(1)), parse_ddmm_yy(m.group(2))
        if not (a and b) or b < TODAY:
            continue
        title = re.sub(r"\s+", " ", cells[1]).strip()
        if len(title) > 120:
            parts = re.split(r"(?<=\d{4})\s+", title, maxsplit=1)
            title = parts[0].strip() or title[:100]
        city = cells[3].split(",")[0].strip() if len(cells) > 3 else ""
        themes = cells[2] if len(cells) > 2 else ""
        href = re.search(r'href="(https?://[^"]+)"', tr)
        url = (href.group(1) if href else "https://ridjey.ru/info/selhoz/")
        url = url.replace("https://https://", "https://")
        ev = make_event(
            title, a, b, url, source, city=city,
            extra_vert=f"{themes} {title}", etype="Выставка",
        )
        if ev:
            out.append(ev)
    return out


def parse_ict2go(html: str, source: str = "ict2go") -> list[dict]:
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
        themes_block = m.group(7)
        theme_slugs = set(re.findall(r'href="/themes/([^"/]+)/"', themes_block))
        title = unescape(m.group(6)).strip()
        blob = title + " " + strip_tags(themes_block)
        is_it = bool(theme_slugs & ICT2GO_IT_THEMES) or bool(IT_RE.search(blob))
        if not is_it and not re.search(
            r"\bIT\b|DevOps|кибер|ИИ\b|AI\b|Data\s*Science|информационн|"
            r"цифров|телеком|облач|хакатон|software|frontend|backend|ML\b",
            blob, re.I,
        ):
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
        typ_m = re.search(r'class="event-type"[^>]*>([^<]+)', themes_block)
        et = typ_m.group(1) if typ_m else "Конференция"
        ev = make_event(title, d1, d2, url, source, city=city, etype=et, extra_vert=f"IT {blob}")
        if ev:
            ev["vertical"] = "it"
            out.append(ev)
    return out


def parse_ict2go_rss(xml: str, source: str = "ict2go") -> list[dict]:
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S | re.I):
        title_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
        desc_m = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", item, re.S)
        link_m = re.search(r"<link>([^<]+)</link>", item)
        if not title_m:
            continue
        title = unescape(strip_tags(title_m.group(1))).strip()
        desc = unescape(strip_tags(desc_m.group(1))) if desc_m else ""
        blob = f"{title} {desc}"
        if not IT_RE.search(blob) and not re.search(
            r"\bIT\b|DevOps|кибер|ИИ|AI\b|информационн|разработ|цифр", blob, re.I
        ):
            continue
        rng = None
        for dm in re.finditer(
            r"(\d{1,2}(?:\s*[–\-—]\s*\d{1,2})?\s+[а-яё]+\s+202[67])", blob, re.I
        ):
            yr = 2027 if "2027" in dm.group(1) else 2026
            rng = parse_ru_date_range(dm.group(1), yr)
            if rng:
                break
        if not rng or rng[1] < TODAY:
            continue
        url = link_m.group(1).strip() if link_m else "https://ict2go.ru/"
        ev = make_event(title, rng[0], rng[1], url, source, etype="Мероприятие", extra_vert=f"IT {blob}")
        if ev:
            ev["vertical"] = "it"
            out.append(ev)
    return out


def parse_totalexpo(html: str, source: str = "totalexpo") -> list[dict]:
    out = []
    for m in re.finditer(
        r'<h2><a href="(/expo/\d+\.aspx)">([^<]+)</a></h2>\s*'
        r'<p>(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})</p>(.*?)</div>',
        html, re.S,
    ):
        href, title, d1s, d2s, rest = m.groups()
        a, b = parse_dot_date(d1s), parse_dot_date(d2s)
        if not a or not b or b < TODAY:
            continue
        city, country = "", "Россия"
        cm = re.search(r"Место проведения:\s*([^<]+)</p>", rest)
        if cm:
            parts = [p.strip() for p in cm.group(1).split(",")]
            if len(parts) >= 2:
                country, city = parts[0], parts[1]
            elif parts:
                city = parts[0]
        themes = re.search(r"Тематика:\s*(.*?)</p>", rest, re.S)
        theme = strip_tags(themes.group(1)) if themes else ""
        url = "https://totalexpo.ru" + href
        ev = make_event(
            unescape(title).strip(), a, b, url, source,
            city=city, country=country, extra_vert=theme,
        )
        if ev:
            out.append(ev)
    return out


def parse_point_range(html: str, title: str, url: str, source: str,
                      city: str = "", extra: str = "", force_vert: str = "") -> list[dict]:
    text = strip_tags(html)
    out = []
    for pat in [
        r"(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+2027)",
        r"(\d{1,2}\s+[а-яё]+\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+2027)",
        r"(\d{1,2}\s+[а-яё]+\s+2027)",
        r"(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+2026)",
        r"(\d{1,2}\s+[а-яё]+\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+2026)",
    ]:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        yr = 2027 if "2027" in m.group(1) else 2026
        rng = parse_ru_date_range(m.group(1), yr)
        if rng and rng[1] >= TODAY:
            ev = make_event(title, rng[0], rng[1], url, source, city=city, extra_vert=extra or title)
            if ev:
                if force_vert:
                    ev["vertical"] = force_vert
                out.append(ev)
                return out
    return out


def parse_innoprom(html: str, source: str = "innoprom") -> list[dict]:
    text = strip_tags(html)
    out = []
    specs = [
        (r"ИННОПРОМ\.?\s*БЕЛАРУСЬ\s+(\d{1,2}\s+[а-яё]+\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+202[67])",
         "ИННОПРОМ. Беларусь", "Минск", "Беларусь"),
        (r"ИННОПРОМ\.?\s*ЦЕНТРАЛЬНАЯ\s+АЗИЯ\s+(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+202[67])",
         "ИННОПРОМ. Центральная Азия", "Ташкент", "Узбекистан"),
        (r"ИННОПРОМ\s+(\d{1,2}\s*[–\-—]\s*\d{1,2}\s+[а-яё]+\s+2027)",
         "ИННОПРОМ", "Екатеринбург", "Россия"),
    ]
    for pat, label, city, country in specs:
        for m in re.finditer(pat, text, re.I):
            yr = 2027 if "2027" in m.group(1) else 2026
            rng = parse_ru_date_range(m.group(1), yr)
            if rng and rng[1] >= TODAY:
                ev = make_event(
                    f"{label} {rng[0].year}", rng[0], rng[1],
                    "https://innoprom.com/", source,
                    city=city, country=country, extra_vert="промышленность innoprom",
                )
                if ev:
                    out.append(ev)
    # local dedupe
    seen = set()
    uniq = []
    for e in out:
        k = soft_key(e)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def load_cp1251(name: str) -> str:
    p = SAMPLES / name
    if not p.exists():
        return ""
    raw = p.read_bytes()
    for enc in ("cp1251", "utf-8"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def main() -> None:
    existing = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    before_n = len(existing)
    before_vert = Counter(e.get("vertical") for e in existing)
    before_src = Counter(e.get("source") for e in existing)
    print(f"BEFORE N={before_n} vert={dict(before_vert)}")

    # ===== APK =====
    html = load_cp1251("r5_exponet_agri_future.html")
    part = parse_exponet_topic(html, "exponet_future") if html else []
    print(f"  exponet agri: {len(part)}")
    existing = merge(existing, part, "exponet_agri")

    html = load_html("r5_souzmoloko_2026.html") or load_html("r5_souzmoloko_www.html")
    part = parse_souzmoloko(html) if html else []
    print(f"  souzmoloko: {len(part)}")
    existing = merge(existing, part, "souzmoloko")

    html = load_html("r5_ridjey_selhoz.html") or load_html("r5_ridjey_www.html")
    part = parse_ridjey(html) if html else []
    print(f"  ridjey: {len(part)}")
    existing = merge(existing, part, "ridjey")

    for fn in [
        "r5_agroinvestor_afisha.html", "r5_agroinvestor_afisha_www.html",
        "r5_agroinvestor_catalog.html", "r5_agroinvestor_catalog2.html",
        "r5_agroinvestor_cat_p2.html", "r5_agroinvestor_cat_p3.html",
    ]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_agroinvestor(html)
        print(f"  agroinvestor {fn}: {len(part)}")
        existing = merge(existing, part, f"agroinvestor:{fn}")

    html = load_html("r5_foodsmi.html") or load_html("apk_foodsmi.html")
    if html:
        raw = parse_foodsmi(html, "foodsmi")
        part = []
        for e in raw:
            blob = f"{e['title']} {e.get('description', '')}"
            if APK_RE.search(blob) or re.search(
                r"food|пищ|молоч|мяс|рыб|агро|horeca|worldfood|упаков|напит|хлеб|кондитер",
                blob, re.I,
            ):
                e["vertical"] = "apk"
                part.append(e)
        print(f"  foodsmi apk-filter: {len(part)}")
        existing = merge(existing, part, "foodsmi")

    html = load_html("r5_agrobvk.html")
    part = parse_agrobvk(html) if html else []
    print(f"  agrobvk: {len(part)}")
    existing = merge(existing, part, "agrobvk")

    html = load_html("r5_niva.html")
    part = parse_niva(html) if html else []
    print(f"  niva: {len(part)}")
    existing = merge(existing, part, "niva")

    for fn in ["apk_zivot_2026.html", "zivot_agrokalendar.html", "r5_zivot_vystavki.html"]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_zivot_full(html, "zivot") + parse_zivot_seed(html, "zivot")
        print(f"  zivot {fn}: {len(part)}")
        existing = merge(existing, part, f"zivot:{fn}")

    walls.append("imol.club/event/+/katalog/: TimeoutError >60s (retry). WALL.")
    walls.append("agroday.ru/expo/*: TimeoutError. WALL.")
    walls.append("seafoodexporussia.com: HTTP 502. WALL.")
    walls.append("minvodyagro.ru / russian-field-day.ru: July 2026 PAST — skipped.")
    walls.append(
        "agroxxi.ru/vystavki.html: redirects to novosti-selskogo-hozjaistva.html (news hub). WALL."
    )
    walls.append(
        "zivotnovodstvo.ru/agrokalendar-2026/: redirects to sprosit-veterinara; used /vystavki/ + prior samples."
    )

    # ===== INDUSTRY =====
    # Exponet pagination p2l50.. (real pagination; pNl10000 was same-as-p1)
    for n in range(2, 12):
        html = load_cp1251(f"r5_exponet_rus_p{n}l50.html")
        if not html:
            continue
        part = parse_exponet_topic(html, "exponet_future")
        print(f"  exponet rus p{n}l50: {len(part)}")
        existing = merge(existing, part, f"exponet_page:p{n}l50")
    for fn in ["r5_exponet_rus_p2l100.html", "r5_exponet_rus_p3l100.html", "r5_exponet_rus_p1.html"]:
        html = load_cp1251(fn)
        if not html:
            continue
        part = parse_exponet_topic(html, "exponet_future")
        print(f"  exponet {fn}: {len(part)}")
        existing = merge(existing, part, f"exponet_page:{fn}")

    for fn in [
        "r5_exponet_building_future.html", "r5_exponet_transport_future.html",
        "r5_exponet_prom_future.html", "r5_exponet_oil_future.html",
        "r5_exponet_furn_future.html", "r5_exponet_goods_future.html",
        "r5_exponet_biz_future.html", "r5_exponet_moda_future.html",
        "r5_exponet_forest_future.html", "r5_exponet_it_future.html",
        "r5_exponet_health_future.html",
    ]:
        html = load_cp1251(fn)
        if not html:
            continue
        part = parse_exponet_topic(html, "exponet_future")
        if part:
            print(f"  exponet topic {fn}: {len(part)}")
            existing = merge(existing, part, f"exponet_topic:{fn}")

    html = load_html("r5_crocus_2026.html")
    part = parse_crocus_any(html, "crocus") if html else []
    print(f"  crocus: {len(part)}")
    existing = merge(existing, part, "crocus:r5")

    html = load_html("r5_innoprom.html") or load_html("r5_innoprom_www.html")
    part = parse_innoprom(html) if html else []
    print(f"  innoprom: {len(part)}")
    existing = merge(existing, part, "innoprom")

    for fn, title, url, src, city, extra, fv in [
        ("r5_neftegaz.html", "Нефтегаз 2027", "https://neftegaz-expo.ru/", "neftegaz", "Москва", "нефтегаз oil gas", ""),
        ("r5_neftegaz_www.html", "Нефтегаз 2027", "https://www.neftegaz-expo.ru/", "neftegaz", "Москва", "нефтегаз", ""),
        ("r5_metobr.html", "Металлообработка 2027", "https://metobr-expo.ru/", "metobr", "Москва", "металлообработка", ""),
        ("r5_metobr_www.html", "Металлообработка 2027", "https://www.metobr-expo.ru/", "metobr", "Москва", "металлообработка", ""),
        ("r5_rosmould.html", "Rosmould / Rosplast 2027", "https://rosmould.ru/", "rosmould", "Москва", "industry mould", ""),
        ("r5_sviaz.html", "Связь-Экспокомм 2027", "https://sviaz-expo.ru/", "sviaz", "Москва", "связь телеком IT", "it"),
        ("r5_sviaz_www.html", "Связь-Экспокомм 2027", "https://www.sviaz-expo.ru/", "sviaz", "Москва", "связь телеком IT", "it"),
    ]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_point_range(html, title, url, src, city=city, extra=extra, force_vert=fv)
        print(f"  {src}/{fn}: {len(part)}")
        existing = merge(existing, part, f"{src}:{fn}")

    for fn in ["r5_totalexpo_expo.html", "r5_totalexpo_expo2.html"]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_totalexpo(html)
        print(f"  totalexpo {fn}: {len(part)}")
        existing = merge(existing, part, f"totalexpo:{fn}")

    walls.append("rspp.ru/events/activities/ (+ /events/): TimeoutError. WALL.")
    walls.append(
        "exponet pNl10000.ru.html returns same-as-p1 content; real pagination is pNl50 "
        "(p2l50..p4l50 have rows; p5+ collapse to 2027 leftover set)."
    )
    walls.append(
        "exponet topics medicine/chemistry/electronics/telecom: skipped per steering (0 dates expected); "
        "building/transport/prom/oil harvested."
    )

    # ===== IT =====
    html = load_html("r5_jugru.html") or load_html("it_jugru.html")
    part = parse_jugru(html) if html else []
    print(f"  jugru: {len(part)}")
    existing = merge(existing, part, "jugru")

    for fn, title, url in [
        ("r5_highload.html", "HighLoad++ 2026", "https://highload.ru/"),
        ("r5_highload2.html", "HighLoad++ 2026", "https://highload.ru/"),
        ("r5_heisenbug.html", "Heisenbug 2026", "https://heisenbug.ru/"),
        ("r5_jpoint.html", "JPoint 2026", "https://jpoint.ru/"),
        ("r5_holyjs.html", "HolyJS 2026", "https://holyjs.ru/"),
        ("r5_cppconf.html", "C++ Russia 2026", "https://cppconf.ru/"),
        ("r5_mobius.html", "Mobius 2026", "https://mobiusconf.com/"),
        ("r5_devopsconf.html", "DevOpsConf 2026", "https://devopsconf.io/"),
        ("r5_devopsconf_ru.html", "DevOpsConf 2026", "https://devopsconf.ru/"),
        ("r5_ontico.html", "Ontico", "https://ontico.ru/"),
        ("r5_ontico2.html", "Ontico", "https://ontico.ru/"),
    ]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_it_single(html, title, url, "it_site")
        print(f"  {fn}: {len(part)}")
        existing = merge(existing, part, f"it_site:{fn}")

    for fn in ["r5_ict2go_events.html", "r5_ict2go_events2.html", "r5_ict2go_from.html", "r5_ict2go.html"]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_ict2go(html)
        print(f"  ict2go {fn}: {len(part)}")
        existing = merge(existing, part, f"ict2go:{fn}")

    for fn in ["r5_ict2go_rss.xml", "r5_ict2go_rss2.xml"]:
        html = load_html(fn)
        if not html:
            continue
        part = parse_ict2go_rss(html)
        print(f"  ict2go rss {fn}: {len(part)}")
        existing = merge(existing, part, f"ict2go_rss:{fn}")

    html = load_html("r5_itsec_cal_www.html") or load_html("r5_itsec_www.html") or load_html("r5_itsec_cal2.html")
    if html:
        text = strip_tags(html)
        part = []
        for m in re.finditer(
            r"(\d{1,2}(?:\s*[–\-—]\s*\d{1,2})?\s+[а-яё]+\s+202[67])\s*"
            r"([A-Za-zА-Яа-яЁё0-9 «»\"\-\.]{8,90})",
            text, re.I,
        ):
            yr = 2027 if "2027" in m.group(1) else 2026
            rng = parse_ru_date_range(m.group(1), yr)
            if not rng or rng[1] < TODAY:
                continue
            title = m.group(2).strip(" .,—–-")
            if len(title) < 8 or re.search(r"подписа|контакт|меню|все права", title, re.I):
                continue
            if not re.search(r"безопас|InfoSec|форум|конференц|выставк|семинар|IT|ИБ", title + m.group(0), re.I):
                continue
            ev = make_event(
                title, rng[0], rng[1], "https://www.itsec.ru/calendar/", "itsec",
                etype="Конференция", extra_vert=f"IT security {title}",
            )
            if ev:
                ev["vertical"] = "it"
                part.append(ev)
        # dedupe local
        seen = set()
        uniq = []
        for e in part:
            k = fuzzy_title(e["title"]) + e["starts_at"]
            if k in seen:
                continue
            seen.add(k)
            uniq.append(e)
        print(f"  itsec: {len(uniq)}")
        existing = merge(existing, uniq[:25], "itsec")
    walls.append("itsec.ru/calendar (non-www): 404/SSL; www.itsec.ru/calendar/ used.")

    html = load_html("r5_webbear_cal.html") or load_html("r5_webbear.html")
    if html:
        text = strip_tags(html)
        dates = re.findall(r"\d{1,2}\s+[а-яё]+\s+202[67]", text, re.I)
        if dates:
            walls.append(
                f"web-bear.ru/calendar: {len(dates)} date string(s) in HTML but no structured multi-event cards; "
                "not inventing titles. WALL."
            )
        else:
            walls.append("web-bear.ru/calendar: no dated event cards in static HTML. WALL.")

    walls.append(
        "ExpoCalendar / KudaBiz page=N / meatindustry 2024-only / expomap SPA / it-events.com: skipped per steering."
    )

    write_outputs(existing)
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    vert = Counter(e.get("vertical") for e in final)
    src = Counter(e.get("source") for e in final)

    apk_delta = vert.get("apk", 0) - before_vert.get("apk", 0)
    ind_delta = vert.get("industry", 0) - before_vert.get("industry", 0)
    it_delta = vert.get("it", 0) - before_vert.get("it", 0)

    if apk_delta <= 2:
        walls.append(
            "APK listings exhausted (≈0–few net after package): exponet agri + souzmoloko + ridjey + "
            "agroinvestor/afisha + foodsmi-apk + zivot/seeds; imol/agroday/seafood/agroxxi/minvody/rfd walls. "
            "Evidence samples/r5_*."
        )

    report = []
    report.append(f"INGEST ROUND5 FINAL REPORT {TODAY.isoformat()} (Europe/Moscow)")
    report.append("")
    report.append(
        f"BASELINE (round4): N={before_n}  apk={before_vert.get('apk',0)}  "
        f"industry={before_vert.get('industry',0)}  it={before_vert.get('it',0)}"
    )
    report.append(
        f"FINAL:             N={len(final)}  apk={vert.get('apk',0)}  "
        f"industry={vert.get('industry',0)}  it={vert.get('it',0)}"
    )
    report.append(
        f"DELTA vs baseline: N {len(final)-before_n:+d}  apk {apk_delta:+d}  "
        f"industry {ind_delta:+d}  it {it_delta:+d}"
    )
    report.append("")
    report.append("BY SOURCE (final):")
    for k, v in src.most_common():
        report.append(f"  {k}: {v} (was {before_src.get(k, 0)})")
    report.append("")
    report.append("HARVEST THIS ROUND (net after honest dedupe):")
    harvested = False
    for k, v in sorted(added_by_source.items(), key=lambda x: -x[1]):
        if v:
            report.append(f"  {k}: +{v}")
            harvested = True
    if not harvested:
        report.append("  (none)")
    report.append("")
    report.append(f"SUCCESS toward 1500: N={len(final)} (>=1500={len(final) >= 1500})")
    report.append(f"APK growth: {apk_delta:+d}; industry: {ind_delta:+d}; IT: {it_delta:+d}")
    report.append("")
    report.append("WALLS (evidence in samples/):")
    for i, w in enumerate(walls, 1):
        report.append(f"{i}. {w}")
    report.append("")
    report.append(
        "FILES: events_upcoming.json, events-data.js, ingest_round5.py, "
        "samples/ingest_report_round5.txt, samples/ingest_walls_round5.txt"
    )
    report_text = "\n".join(report) + "\n"
    (SAMPLES / "ingest_report_round5.txt").write_text(report_text, encoding="utf-8")
    (SAMPLES / "ingest_walls_round5.txt").write_text(
        "\n".join(f"{i}. {w}" for i, w in enumerate(walls, 1)) + "\n", encoding="utf-8"
    )
    print("\n==== RESULT ====")
    print(report_text)
    return len(final), dict(vert), dict(added_by_source)


if __name__ == "__main__":
    main()
