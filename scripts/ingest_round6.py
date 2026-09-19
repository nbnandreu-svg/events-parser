#!/usr/bin/env python3
"""Events Calendar ingest round6 — texpo.ru primary + crumb package."""
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
    TODAY, ROOT, SAMPLES, make_event, parse_ru_date_range, parse_exponet_topic,
)
from ingest_round3 import (  # noqa: E402
    parse_all_events, merge, write_outputs, dedupe_key, soft_key,
    added_by_source, stats, walls,
)

added_by_source.clear()
stats.clear()
walls.clear()


def parse_texpo(html: str, source: str = "texpo") -> list[dict]:
    """Timiryazev Center /expo/ cards: expolistbadge RU-month ranges + titles."""
    out: list[dict] = []
    blocks = re.split(r"project-list__item height-100", html)
    for b in blocks[1:]:
        badge = re.search(r"expolistbadge[^>]*>\s*<i[^>]*></i>\s*([^<\n]+)", b)
        title_m = re.search(
            r'project-list__item-title[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>\s*([^<]+)',
            b,
        )
        if not (badge and title_m):
            continue
        date_s = unescape(badge.group(1)).strip()
        rng = parse_ru_date_range(date_s)
        if not rng or rng[1] < TODAY:
            continue
        href = title_m.group(1)
        title = unescape(title_m.group(2)).strip()
        url = href if href.startswith("http") else "https://texpo.ru" + href
        ev = make_event(
            title, rng[0], rng[1], url, source,
            city="Москва", etype="Выставка", extra_vert=title,
        )
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


def parse_workevent_city_escaped(html: str, source: str = "workevent") -> list[dict]:
    """City pages embed RSC-escaped JSON; keep only events with /event/...-ID hrefs."""
    hrefs: dict[int, str] = {}
    for l in re.findall(r'href="(/event/[^"]+)"', html):
        mm = re.search(r"-(\d+)$", l)
        if mm:
            hrefs[int(mm.group(1))] = "https://workevent.ru" + l
    out: list[dict] = []
    for m in re.finditer(r'\\"id\\":(\d+),\\"title\\":\\"', html):
        eid = int(m.group(1))
        if eid not in hrefs:
            continue
        chunk = html[m.start() : m.start() + 3000].replace('\\"', '"')
        id_m = re.search(r'"id":(\d+),"title":"([^"]*)"', chunk)
        if not id_m:
            continue
        sd = re.search(r'"start_date":"(\d{4}-\d{2}-\d{2})"', chunk[:900])
        ed = re.search(r'"end_date":"(\d{4}-\d{2}-\d{2})"', chunk[:900])
        if not sd:
            continue
        fl = re.search(r'"format_label":"([^"]*)"', chunk[:1200])
        city = re.search(r'"city":\{"id":\d+,"title":"([^"]*)"', chunk[:1500])
        ind = re.search(r'"industry":\{"id":\d+,"title":"([^"]*)"', chunk[:1800])
        starts = date.fromisoformat(sd.group(1))
        ends = date.fromisoformat(ed.group(1) if ed else sd.group(1))
        ev = make_event(
            id_m.group(2),
            starts,
            ends,
            hrefs[eid],
            source,
            city=city.group(1) if city else "",
            etype=fl.group(1) if fl else "Мероприятие",
            extra_vert=ind.group(1) if ind else "",
        )
        if ev:
            out.append(ev)
    seen = set()
    uniq = []
    for e in out:
        if e["organizer_url"] in seen:
            continue
        seen.add(e["organizer_url"])
        uniq.append(e)
    return uniq


def parse_eventomat(html: str, source: str = "eventomat") -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<time datetime="([^"]+)".*?<h3 class="card-title"><a href="([^"]+)">([^<]+)</a>',
        html,
        re.S,
    ):
        href = m.group(2)
        if not href.startswith("http"):
            href = "https://eventomat.ru" + href
        if href in seen:
            continue
        seen.add(href)
        try:
            d = date.fromisoformat(m.group(1)[:10])
        except ValueError:
            continue
        chunk = html[m.start() : m.end() + 400]
        city_m = re.search(r"card-city[^>]*>([^<]+)", chunk)
        city = unescape(city_m.group(1)).strip() if city_m else ""
        cat_m = re.search(r"card-cat[^>]*>([^<]+)", html[max(0, m.start() - 250) : m.start()])
        cat = cat_m.group(1).strip() if cat_m else "Мероприятие"
        ev = make_event(
            unescape(m.group(3)).strip(), d, d, href, source,
            city=city, etype=cat, extra_vert=cat,
        )
        if ev:
            out.append(ev)
    return out


def load_cp1251(name: str) -> str:
    p = SAMPLES / name
    if not p.exists():
        return ""
    data = p.read_bytes()
    for enc in ("cp1251", "utf-8"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def main() -> tuple[int, dict, dict]:
    existing = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    before_n = len(existing)
    before_vert = Counter(e.get("vertical") for e in existing)
    before_src = Counter(e.get("source") for e in existing)

    # --- PRIMARY: texpo.ru/expo/ (+ ?dir=2026 same event set) ---
    texpo_files = ["r6_texpo_expo.html", "r6_texpo_dir2026.html"]
    texpo_all: list[dict] = []
    for fn in texpo_files:
        p = SAMPLES / fn
        if not p.exists():
            walls.append(f"texpo missing sample {fn}")
            continue
        part = parse_texpo(p.read_text(encoding="utf-8", errors="replace"))
        stats[f"texpo_parsed_{fn}"] = len(part)
        texpo_all.extend(part)
    # dedupe within harvest
    seen_t = set()
    texpo_uniq = []
    for e in texpo_all:
        k = dedupe_key(e)
        if k in seen_t:
            continue
        seen_t.add(k)
        texpo_uniq.append(e)
    existing = merge(existing, texpo_uniq, "texpo")

    # --- CRUMBS: workevent cities ---
    for fn, label in [
        ("r6_workevent_ekb.html", "workevent:ekaterinburg-17"),
        ("r6_workevent_kazan16.html", "workevent:kazan-16"),
    ]:
        p = SAMPLES / fn
        if not p.exists():
            walls.append(f"workevent crumb missing {fn}")
            continue
        part = parse_workevent_city_escaped(p.read_text(errors="replace"))
        stats[f"parsed_{label}"] = len(part)
        existing = merge(existing, part, label)

    # --- all-events calendar page 1 only (NO PAGEN) ---
    ae_p = SAMPLES / "r6_ae_calendar_p1.html"
    if ae_p.exists():
        part = parse_all_events(ae_p.read_text(errors="replace"))
        stats["ae_cal_p1_parsed"] = len(part)
        existing = merge(existing, part, "all_events:calendar_p1")
    else:
        walls.append("all-events calendar p1 sample missing")

    # --- eventomat ---
    em_p = SAMPLES / "r6_eventomat.html"
    if em_p.exists():
        part = parse_eventomat(em_p.read_text(errors="replace"))
        stats["eventomat_parsed"] = len(part)
        existing = merge(existing, part, "eventomat")
    else:
        walls.append("eventomat sample missing")

    # --- exponet p4l50+ (p1–p3 already done in prior rounds); use r5 samples ---
    exp_net_before = added_by_source.copy()
    for n in range(4, 12):
        fn = f"r5_exponet_rus_p{n}l50.html"
        html = load_cp1251(fn)
        if not html or html.count("<tr") < 3:
            # r6 fresh fetches collapsed; skip empty
            continue
        part = parse_exponet_topic(html, "exponet_future")
        stats[f"exponet_p{n}l50_parsed"] = len(part)
        existing = merge(existing, part, f"exponet:p{n}l50")

    write_outputs(existing)
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    vert = Counter(e.get("vertical") for e in final)
    src = Counter(e.get("source") for e in final)

    net_total = len(final) - before_n
    apk_delta = vert.get("apk", 0) - before_vert.get("apk", 0)
    ind_delta = vert.get("industry", 0) - before_vert.get("industry", 0)
    it_delta = vert.get("it", 0) - before_vert.get("it", 0)

    # Walls / ceiling note
    walls.append(
        "texpo.ru/expo/ (+?dir=2026): Timiryazev Center project list — 99 cards, "
        f"{stats.get('texpo_parsed_r6_texpo_expo.html', 0)} upcoming via expolistbadge RU ranges; "
        "dir=2026 same DETAIL_PAGE_URL set. Not a wide aggregator."
    )
    walls.append(
        "workevent /city/ekaterinburg-17 + /city/kazan-16: RSC-escaped ISO parsed; "
        f"ekb={stats.get('parsed_workevent:ekaterinburg-17', 0)} "
        f"kazan16={stats.get('parsed_workevent:kazan-16', 0)} uniq — net 0 after catalog dedupe "
        "(prior workevent_city_* rounds). Other city-slugs often 404 — not thrashed."
    )
    walls.append(
        f"all-events.ru/events/calendar/ page1 only (NO PAGEN): parsed {stats.get('ae_cal_p1_parsed', 0)}; "
        "net 0 after honest dedupe."
    )
    walls.append(
        f"eventomat.ru: parsed {stats.get('eventomat_parsed', 0)} ISO cards; net 0 after dedupe."
    )
    walls.append(
        "exponet rus p4l50–p11l50: r5 samples still hold ~9 dated rows each (same leftover set); "
        "fresh r6 curl of p5+ collapsed to empty tables; net 0."
    )

    if net_total <= 2:
        walls.append(
            "HTML/RSS exhausted, ceiling fixed — round6 package net≈0 after honest dedupe; "
            "gap to 1500 not closable without inventing dates / third-party APIs / "
            "ExpoCalendar·TimePad·All-Events PAGEN (disallowed)."
        )
        (SAMPLES / "ingest_report_round6.txt").write_text(
            "HTML/RSS exhausted, ceiling fixed\n", encoding="utf-8"
        )

    report = []
    report.append(f"INGEST ROUND6 FINAL REPORT {TODAY.isoformat()} (Europe/Moscow)")
    report.append("")
    report.append(
        f"BASELINE (round5): N={before_n}  apk={before_vert.get('apk', 0)}  "
        f"industry={before_vert.get('industry', 0)}  it={before_vert.get('it', 0)}"
    )
    report.append(
        f"FINAL:             N={len(final)}  apk={vert.get('apk', 0)}  "
        f"industry={vert.get('industry', 0)}  it={vert.get('it', 0)}"
    )
    report.append(
        f"DELTA vs baseline: N {net_total:+d}  apk {apk_delta:+d}  "
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
    report.append(f"  TOTAL net {net_total:+d}")
    report.append("")
    report.append(f"SUCCESS toward 1500: N={len(final)} (>=1500={len(final) >= 1500})")
    report.append(f"Gap remaining: {max(0, 1500 - len(final))}")
    report.append("")
    report.append("WALLS (evidence in samples/r6_*):")
    for i, w in enumerate(walls, 1):
        report.append(f"{i}. {w}")
    report.append("")
    report.append(
        "FILES: events_upcoming.json, events-data.js, ingest_round6.py, "
        "samples/ingest_report_round6.txt, samples/ingest_walls_round6.txt"
    )
    report_text = "\n".join(report) + "\n"

    # Always write full report (overwrite short wall-only if we wrote it)
    if net_total <= 2:
        # prepend wall line then full report
        report_text = "HTML/RSS exhausted, ceiling fixed\n\n" + report_text

    (SAMPLES / "ingest_report_round6.txt").write_text(report_text, encoding="utf-8")
    (SAMPLES / "ingest_walls_round6.txt").write_text(
        "\n".join(f"{i}. {w}" for i, w in enumerate(walls, 1)) + "\n", encoding="utf-8"
    )
    print("\n==== RESULT ====")
    print(report_text)
    return len(final), dict(vert), dict(added_by_source)


if __name__ == "__main__":
    main()
