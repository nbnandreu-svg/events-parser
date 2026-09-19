#!/usr/bin/env python3
"""APK push2: Expomap HTTP pagination (sel/food/vet/vino/sad + conferences),
agrozentr kalendar, field-day seeds, exponet food/zoology RF, soft remap.
Skip agroday. Target apk 300–400.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Optional

from ingest_round2 import (
    TODAY, APK_RE, UA, strip_tags, make_event, parse_exponet_topic,
    parse_ru_date_range,
)
from ingest_round3 import dedupe_key
from ingest_apk_sprint import SOFT_APK_RE
from ingest_apk400 import (
    force_apk, clean_title, merge_into, decode_prefer, fix_cis_country,
    soft_remap_apk as soft_remap_apk400, APK_HINT, SKIP_TITLE, mon,
)

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)

# Expomap theme pages (correct RU slugs). English slugs return empty.
EXPOMAP_THEMES = [
    # (url_base, max_pages, source, etype, force)
    ("https://expomap.ru/expo/theme/selskoe-hozjajstvo/", 21, "expomap", "Выставка", True),
    ("https://expomap.ru/expo/theme/produktyi-pischevaya-industriya/", 34, "expomap", "Выставка", True),
    ("https://expomap.ru/expo/theme/zhivotnyie-veterinariya/", 7, "expomap", "Выставка", True),
    ("https://expomap.ru/expo/theme/vino-alkogol-tabak/", 8, "expomap", "Выставка", True),
    ("https://expomap.ru/expo/theme/landshaftnyij-dizajn-sad/", 10, "expomap", "Выставка", True),
    ("https://expomap.ru/conference/theme/selskoe-hozjajstvo/", 2, "expomap_conf", "Конференция", True),
    ("https://expomap.ru/conference/theme/produktyi-pischevaya-industriya/", 2, "expomap_conf", "Конференция", True),
    ("https://expomap.ru/conference/theme/zhivotnyie-veterinariya/", 1, "expomap_conf", "Конференция", True),
]

NOISE = re.compile(
    r"^(Реклама|Подробнее|Важные события|Найдено|Период|Выставки|Конференции|"
    r"Сельское хозяйство|Пищевая|Ближайшее|ФИЛЬТРЫ|Популярные|Топовые|"
    r"ОТКРЫТЬ|Главная|Каталог|Создать|Ваш город|Да|Нет|RU|EN|#|"
    r"Площадки|Новости|Статьи|О компании|Контакты|\d+\s*событий)$",
    re.I,
)


def http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def html_to_lines(html: str) -> list[str]:
    t = re.sub(r"<script[\s\S]*?</script>", "\n", html, flags=re.I)
    t = re.sub(r"<style[\s\S]*?</style>", "\n", t, flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(?:div|p|li|h\d|tr|td|th|section|article)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = unescape(t)
    lines = []
    for ln in t.splitlines():
        s = re.sub(r"\s+", " ", ln).strip()
        if s:
            lines.append(s)
    return lines


DATE_RE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*[—–-]\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})$"
)
DATE_RE2 = re.compile(
    r"^(\d{1,2})\.(\d{1,2})\s*[—–-]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})$"
)


def parse_date_line(ln: str) -> Optional[tuple[date, date]]:
    ln = ln.replace("—", "-").replace("–", "-").strip()
    m = DATE_RE.match(ln) or re.match(
        r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", ln
    )
    if m:
        y2 = int(m.group(6))
        y2 = y2 if y2 > 100 else 2000 + y2
        y1s = m.group(3)
        if y1s:
            y1 = int(y1s) if len(y1s) == 4 else 2000 + int(y1s)
        else:
            y1 = y2
        try:
            return date(y1, int(m.group(2)), int(m.group(1))), date(y2, int(m.group(5)), int(m.group(4)))
        except ValueError:
            return None
    m2 = DATE_RE2.match(ln) or re.match(
        r"^(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})$", ln
    )
    if m2:
        y = int(m2.group(5))
        try:
            return date(y, int(m2.group(2)), int(m2.group(1))), date(y, int(m2.group(4)), int(m2.group(3)))
        except ValueError:
            return None
    return None


def parse_expomap_lines(lines: list[str], source: str, etype: str, force: bool) -> list[dict]:
    out: list[dict] = []
    i = 0
    while i < len(lines):
        rng = parse_date_line(lines[i])
        if not rng:
            i += 1
            continue
        d1, d2 = rng
        j = i + 1
        loc = ""
        if j < len(lines) and re.match(r"^[^,#\d][^,]*,\s*\S", lines[j]) and not NOISE.match(lines[j]):
            loc = lines[j]
            j += 1
        # skip pure numbers / stats
        while j < len(lines) and re.fullmatch(r"\d[\d\s]*", lines[j].replace(" ", "")):
            j += 1
        if j >= len(lines):
            break
        title = clean_title(lines[j])
        j += 1
        # strip HIT badge glued
        title = re.sub(r"\s*ХИТ\s*$", "", title, flags=re.I).strip()
        if not title or len(title) < 3 or NOISE.match(title) or SKIP_TITLE.search(title):
            i += 1
            continue
        desc = ""
        while j < len(lines):
            cand = lines[j]
            if parse_date_line(cand) or NOISE.match(cand) or cand.startswith("#"):
                break
            if re.fullmatch(r"\d[\d\s]*", cand.replace(" ", "")):
                j += 1
                continue
            if re.match(r"^[^,]*,\s*\S", cand) and len(cand) < 60 and not desc:
                # another location-looking line — skip if we already have loc
                if not loc:
                    loc = cand
                j += 1
                continue
            if not desc and len(cand) > 3:
                desc = cand
                j += 1
                break
            break
        country, city = "", ""
        cm = re.match(r"([^,]+),\s*(.+)$", loc)
        if cm:
            country, city = cm.group(1).strip(), cm.group(2).strip()
        blob = f"{title} {desc} {loc}"
        if not force and not APK_HINT.search(blob):
            i = j if j > i else i + 1
            continue
        url = "https://expomap.ru/"
        if "conference" in source or etype == "Конференция":
            url = "https://expomap.ru/conference/"
        ev = make_event(
            title, d1, d2, url, source,
            city=city, country=country or "Россия", etype=etype,
            extra_vert="агро сельхоз пищевая ветеринария сад вино " + desc,
            description=desc[:200],
        )
        if ev:
            if force:
                force_apk(ev)
            out.append(ev)
        i = j if j > i else i + 1
    # dedupe within page
    seen: set[tuple] = set()
    uniq = []
    for e in out:
        k = (e["title"].lower()[:60], e["starts_at"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def fetch_theme_page(base: str, page: int) -> tuple[int, str]:
    url = base if page <= 1 else f"{base.rstrip('/')}/?page={page}"
    raw = http_get(url, timeout=35)
    return page, raw.decode("utf-8", errors="replace")


def ingest_expomap_paginated(fetch_live: bool = True, workers: int = 8) -> list[dict]:
    all_ev: list[dict] = []
    for base, max_pages, source, etype, force in EXPOMAP_THEMES:
        slug = base.rstrip("/").split("/")[-1]
        kind = "conf" if "/conference/" in base else "expo"
        print(f"  expomap {kind}/{slug} pages=1..{max_pages}", flush=True)
        pages_html: dict[int, str] = {}
        if fetch_live:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(fetch_theme_page, base, p): p for p in range(1, max_pages + 1)}
                for fut in as_completed(futs):
                    p = futs[fut]
                    try:
                        page, html = fut.result()
                        pages_html[page] = html
                        stem = f"apk_em_{kind}_{slug}_p{page}"
                        (SAMPLES / f"{stem}.html").write_text(html, encoding="utf-8")
                        print(f"    p{page} ok {len(html)}", flush=True)
                    except Exception as e:
                        print(f"    p{p} FAIL {type(e).__name__}: {e}", flush=True)
                        # fallback sample
                        stem = f"apk_em_{kind}_{slug}_p{p}"
                        sp = SAMPLES / f"{stem}.html"
                        if sp.exists():
                            pages_html[p] = sp.read_text("utf-8", errors="replace")
        else:
            for p in range(1, max_pages + 1):
                stem = f"apk_em_{kind}_{slug}_p{p}"
                sp = SAMPLES / f"{stem}.html"
                if sp.exists():
                    pages_html[p] = sp.read_text("utf-8", errors="replace")
        theme_n = 0
        for p in sorted(pages_html):
            lines = html_to_lines(pages_html[p])
            part = parse_expomap_lines(lines, source, etype, force)
            theme_n += len(part)
            all_ev.extend(part)
            # also save text for debugging
            (SAMPLES / f"apk_em_{kind}_{slug}_p{p}.txt").write_text(
                "\n".join(lines[:800]), encoding="utf-8"
            )
        print(f"    -> parsed {theme_n} from {len(pages_html)} pages", flush=True)
    # global dedupe
    seen: set[str] = set()
    uniq = []
    for e in all_ev:
        k = dedupe_key(e)
        sk = ((e.get("title") or "").strip().lower(), e.get("starts_at") or "")
        kk = f"{k}|{sk}"
        if kk in seen:
            continue
        seen.add(kk)
        uniq.append(e)
    return uniq


def parse_agrozentr_kalendar(html: str) -> list[dict]:
    out: list[dict] = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
    for r in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S | re.I)
        if len(cells) < 2:
            continue
        plain = [strip_tags(c) for c in cells]
        if plain[0].upper() in ("ПЕРИОД",) or len(plain[0]) < 4:
            # month headers like ЯНВАРЬ
            if re.fullmatch(r"[А-ЯЁA-Z\s]+", plain[0]) and len(plain) == 1:
                continue
        if len(plain) < 3:
            continue
        period, title = plain[0], plain[1]
        if title.upper() in ("МЕРОПРИЯТИЕ",) or period.upper() in ("ПЕРИОД",):
            continue
        if re.fullmatch(r"[А-ЯЁ]+", period.strip()):
            continue
        info = plain[2] if len(plain) > 2 else ""
        place = plain[3] if len(plain) > 3 else ""
        rng = parse_ru_date_range(period)
        if not rng:
            continue
        href = re.search(r'href="(https?://[^"]+)"', r)
        url = href.group(1) if href else "https://www.agrozentr.ru/info/kalendar-meropriyatiy/"
        city = ""
        cm = re.search(r"г\.\s*([^,\n]+)", place)
        if cm:
            city = cm.group(1).strip()
        elif place:
            city = place.split(",")[0].strip()[:60]
        etype = "День поля" if re.search(r"день\s+поля|день\s+пол", title + " " + info, re.I) else "Выставка"
        if re.search(r"форум", title, re.I):
            etype = "Форум"
        ev = make_event(
            clean_title(title), rng[0], rng[1], url, "agrozentr",
            city=city, etype=etype,
            extra_vert="агро сельхоз день поля " + info + " " + place,
            description=(info + " " + place)[:200],
        )
        if ev:
            out.append(force_apk(ev))
    return out


def ingest_agrozentr(fetch_live: bool = True) -> list[dict]:
    path = SAMPLES / "apk_agrozentr_kalendar.html"
    if fetch_live:
        try:
            data = http_get("https://www.agrozentr.ru/info/kalendar-meropriyatiy/", timeout=30)
            path.write_bytes(data)
        except Exception as e:
            print(f"  agrozentr fetch: {type(e).__name__}: {e}", flush=True)
    if not path.exists():
        return []
    return parse_agrozentr_kalendar(path.read_text("utf-8", errors="replace"))


def seed_field_days() -> list[dict]:
    """Seed known field-day flagships from their sites (dates in HTML). Past events filtered by make_event."""
    seeds = []
    specs = [
        (
            SAMPLES / "apk_field_russian-field-dayru.html",
            "https://russian-field-day.ru/",
            "Всероссийский День поля-2026",
            "Прутской",
            "День поля",
        ),
        (
            SAMPLES / "apk_field_don-poleru.html",
            "https://don-pole.ru/",
            "День Донского поля 2026",
            "Зерноград",
            "День поля",
        ),
        (
            SAMPLES / "apk_field_field74ru.html",
            "https://field74.ru/",
            "День поля Челябинск 2026",
            "Челябинск",
            "День поля",
        ),
    ]
    for path, url, default_title, city, etype in specs:
        if not path.exists():
            continue
        text = strip_tags(path.read_text("utf-8", errors="replace"))
        rng = parse_ru_date_range(text)
        if not rng:
            # try explicit patterns
            m = re.search(
                r"(\d{1,2})\s*[–—-]\s*(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})",
                text, re.I,
            )
            if m:
                mo = mon(m.group(3))
                if mo:
                    try:
                        rng = (date(int(m.group(4)), mo, int(m.group(1))),
                               date(int(m.group(4)), mo, int(m.group(2))))
                    except ValueError:
                        rng = None
        if not rng:
            m2 = re.search(
                r"(\d{1,2})\s*[-–—]\s*(\d{1,2})\s+(июня|июля|мая|августа)\s+(\d{4})",
                text, re.I,
            )
            if m2:
                mo = mon(m2.group(3))
                if mo:
                    try:
                        rng = (date(int(m2.group(4)), mo, int(m2.group(1))),
                               date(int(m2.group(4)), mo, int(m2.group(2))))
                    except ValueError:
                        rng = None
        title = default_title
        tm = re.search(r"(Всероссийский\s+день\s+поля[^\.]{0,40}|День\s+Донского\s+поля|День\s+поля\s*[-–]?\s*2026)", text, re.I)
        if tm:
            title = clean_title(tm.group(1))
        ev = make_event(
            title, rng[0], rng[1], url, "field_day",
            city=city, etype=etype,
            extra_vert="агро день поля сельхоз",
            description=f"Полевой день / демопоказ. Источник: {url}",
        ) if rng else None
        if ev:
            seeds.append(force_apk(ev))
    # AGBZ Зерно России 2027 from events.agbz.ru
    agbz = SAMPLES / "apk_agbz_events_site.html"
    if agbz.exists():
        text = strip_tags(agbz.read_text("utf-8", errors="replace"))
        if re.search(r"Зерно\s+России\s*[-–—]?\s*2027", text, re.I):
            ev = make_event(
                "Зерно России 2027",
                date(2027, 2, 18), date(2027, 2, 19),
                "https://events.agbz.ru/", "agbz",
                city="Москва", etype="Форум",
                extra_vert="агро зерно форум сельхоз",
                description="XI Сельскохозяйственный форум «Зерно России 2027»",
            )
            if ev:
                seeds.append(force_apk(ev))
    return seeds


def ingest_exponet_food_zoo(fetch_live: bool = True) -> list[dict]:
    targets = [
        (
            "https://www.exponet.ru/exhibitions/countries/rus/topics/food/dates/future/index.ru.html",
            "apk_exponet_rus_food2.html",
            "exponet_food",
        ),
        (
            "https://www.exponet.ru/exhibitions/countries/rus/topics/zoology/dates/future/index.ru.html",
            "apk_exponet_rus_zoo.html",
            "exponet_zoo",
        ),
        (
            "https://www.exponet.ru/exhibitions/countries/rus/topics/animals/dates/future/index.ru.html",
            "apk_exponet_rus_animals.html",
            "exponet_zoo",
        ),
        # country agri already harvested; keep CIS food if any
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
                data = http_get(url, timeout=30)
                path.write_bytes(data)
            except Exception as e:
                print(f"  exponet {src}: {type(e).__name__}: {e}", flush=True)
        if not path.exists():
            # try probe copies
            for alt in ("apk_exponet_food_probe.html", "apk_exponet_zoology_probe.html", "apk_exponet_animals_probe.html"):
                if src.startswith("exponet_food") and "food" in alt and (SAMPLES / alt).exists():
                    path = SAMPLES / alt
                    break
                if src.startswith("exponet_zoo") and ("zoo" in alt or "animal" in alt) and (SAMPLES / alt).exists():
                    path = SAMPLES / alt
                    break
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


def soft_remap_strict(events: list[dict]) -> int:
    """Remap industry/it → apk only when clearly agro/food/vet/field-day/feed."""
    changed = 0
    clear = re.compile(
        r"агро|сельхоз|сельск|пищев|ветер|ферм|зерн|молок|мясн|птице|комбикорм|"
        r"день\s+поля|теплич|урожа|продэкспо|югагро|золотая\s+нива|"
        r"\bfood\b|dairy|meat|\bagro|niva|нива|растениевод|животн|пчело|"
        r"винодел|рыбн|аквакульт|овоще|foodexpo|владпрод|worldfood|dairytech|"
        r"кормвет|agravia|iagri|интекпром|megustro|farmtek|growtech|винорус|"
        r"винотех|сибдач|осень\s+на\s+даче|дач[еа]|сад\b|garden\s+show|"
        r"horti|fruit\s+trade|полевод|семеновод|корм\w*|feed\b|vet\b|"
        r"питомник|озелен|виноград|agr[oi]",
        re.I,
    )
    skip = re.compile(
        r"aquatherm|aquaflame|электро\b|текстиль|транспортн|светотех|финсовет|"
        r"фармбизнес|недвижим|автоматизация\s+фарм|mitt\b|интерткань",
        re.I,
    )
    for e in events:
        if e.get("vertical") == "apk":
            continue
        blob = f"{e.get('title','')} {e.get('description','')} {e.get('type','')}"
        if skip.search(blob):
            continue
        if clear.search(blob) or APK_RE.search(blob) or SOFT_APK_RE.search(blob):
            e["vertical"] = "apk"
            changed += 1
    return changed


def run_apk_push2(existing: Optional[list[dict]] = None, fetch_live: bool = True) -> tuple[list[dict], dict]:
    if existing is None:
        path = ROOT / "events_upcoming.json"
        existing = json.loads(path.read_text("utf-8")) if path.exists() else []
    before_apk = sum(1 for e in existing if e.get("vertical") == "apk")
    stats: dict = {
        "before_apk": before_apk,
        "before_n": len(existing),
        "by_source": {},
    }

    em = ingest_expomap_paginated(fetch_live=fetch_live)
    existing, n = merge_into(existing, em)
    stats["expomap_paginated_parsed"] = len(em)
    stats["expomap_paginated_added"] = n
    stats["by_source"]["expomap_paginated"] = n

    az = ingest_agrozentr(fetch_live=fetch_live)
    existing, n = merge_into(existing, az)
    stats["agrozentr_parsed"] = len(az)
    stats["agrozentr_added"] = n

    fd = seed_field_days()
    existing, n = merge_into(existing, fd)
    stats["field_day_parsed"] = len(fd)
    stats["field_day_added"] = n

    ex = ingest_exponet_food_zoo(fetch_live=fetch_live)
    existing, n = merge_into(existing, ex)
    stats["exponet_food_zoo_parsed"] = len(ex)
    stats["exponet_food_zoo_added"] = n

    stats["soft_remap"] = soft_remap_strict(existing)
    # also run apk400 soft remap for consistency
    stats["soft_remap_apk400"] = soft_remap_apk400(existing)

    existing = [e for e in existing if (e.get("ends_at") or "") >= TODAY.isoformat()]
    after_apk = sum(1 for e in existing if e.get("vertical") == "apk")
    stats["after_apk"] = after_apk
    stats["after_n"] = len(existing)
    stats["apk_delta"] = after_apk - before_apk
    stats["verticals"] = dict(Counter(e.get("vertical") for e in existing))
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
    os.environ["SKIP_PW"] = "1"  # no agroday / PW; HTTP pagination only
    t0 = time.time()
    events, stats = run_apk_push2(fetch_live=True)
    meta = write_outputs(events)
    stats["updated_at"] = meta["updated_at"]
    stats["count"] = meta["count"]
    stats["elapsed_s"] = round(time.time() - t0, 1)
    report = SAMPLES / "ingest_report_apk_push2.txt"
    lines = [
        "APK push2 ingest report",
        f"Generated: {meta['updated_at']} MSK",
        "",
        f"BEFORE: N={stats['before_n']} apk={stats['before_apk']}",
        f"AFTER:  N={stats['after_n']} apk={stats['after_apk']} verticals={stats['verticals']}",
        f"APK delta: {stats['apk_delta']:+d}",
        "",
        f"expomap paginated: parsed={stats['expomap_paginated_parsed']} added={stats['expomap_paginated_added']}",
        f"agrozentr: parsed={stats['agrozentr_parsed']} added={stats['agrozentr_added']}",
        f"field_day seeds: parsed={stats['field_day_parsed']} added={stats['field_day_added']}",
        f"exponet food/zoo: parsed={stats['exponet_food_zoo_parsed']} added={stats['exponet_food_zoo_added']}",
        f"soft_remap: {stats['soft_remap']} (+apk400 {stats['soft_remap_apk400']})",
        f"elapsed_s: {stats['elapsed_s']}",
        "",
        "WALLS: agroday skipped (hang). English expomap theme slugs empty.",
        "Exponet RF food/zoology topic pages return empty listings.",
        "Workevent/all-events/kudabiz theme URLs 404 — skipped.",
        "NOT touched: index.html, DESIGN_SYSTEM.md",
        "",
        f"FINAL apk N = {stats['after_apk']}",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"FINAL N={meta['count']} apk={stats['after_apk']} delta={stats['apk_delta']}")
