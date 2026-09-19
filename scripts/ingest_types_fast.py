#!/usr/bin/env python3
"""ASAP finish: AE load_more + edu-afisha + ict2go + kudabiz. No PW/series/cities."""
from __future__ import annotations
import json, re, sys, time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_allevents_types import (
    fetch, save_sample, parse_ae_with_type, parse_edu_afisha, parse_ict2go_typed,
    parse_kudabiz_live, merge_and_retag, type_counts, retitle_retag_catalog,
    TYPE_SLUGS, ALT_SLUGS, TARGET_TYPES, TYPE_NORMALIZE, upcoming_section_id,
    extract_upcoming_chunk, page_is_generic, TODAY, SAMPLES, ROOT,
)
from enrich_summaries import write_catalog
from ingest_round3 import dedupe_key

walls: list[str] = []
evidence: list[str] = []


def uniq_merge(bucket):
    seen, out = set(), []
    for e in bucket:
        k = dedupe_key(e)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def ae_collect(by_type, slug, etype):
    url = f"https://all-events.ru/events/calendar/type-is-{slug}/"
    html, err = fetch(url, timeout=35)
    time.sleep(0.2)
    if err or not html:
        walls.append(f"{url}: {err or 'empty'}")
        print(f"  FAIL {slug}: {err}")
        if slug in ALT_SLUGS:
            ae_collect(by_type, ALT_SLUGS[slug], etype)
        return
    save_sample(f"ae_types_{slug}.html", html)
    if page_is_generic(html):
        walls.append(f"{url}: generic SKIP")
        print(f"  SKIP generic {slug}")
        if slug in ALT_SLUGS:
            ae_collect(by_type, ALT_SLUGS[slug], etype)
        return
    part = parse_ae_with_type(html, etype)
    by_type[etype].extend(part)
    sid = upcoming_section_id(html)
    chunk = extract_upcoming_chunk(html)
    n_schema = len(re.findall(r'itemprop="startDate"', chunk))
    evidence.append(f"AE {slug}: upcoming_section_cards={n_schema} parsed>={TODAY}={len(part)} sid={sid}")
    print(f"  AE {slug}: section={n_schema} parsed={len(part)} sid={sid}")
    if not sid:
        return
    seen = {e.get("organizer_url") for e in by_type[etype]}
    for page in range(2, 7):
        lm = f"{url}?PAGEN_1={page}&load_more={sid}"
        h2, err = fetch(lm, timeout=35)
        time.sleep(0.2)
        if err or not h2:
            walls.append(f"{lm}: {err or 'empty'}")
            break
        save_sample(f"ae_types_{slug}_lm_p{page}.html", h2)
        part2 = parse_ae_with_type(h2, etype)
        new = [e for e in part2 if e.get("organizer_url") not in seen]
        for e in new:
            seen.add(e.get("organizer_url"))
            by_type[etype].append(e)
        print(f"    lm p{page}: +{len(new)}")
        evidence.append(f"AE {slug} PAGEN_1={page}&load_more: new={len(new)}")
        if not new:
            break


def main():
    existing = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    before_n = len(existing)
    before_types = type_counts(existing)
    targets = [
        "Бизнес-завтрак", "Круглый стол", "Бизнес-ужин",
        "Митап", "Вебинар", "Семинар", "Мастер-класс",
    ]
    print("BEFORE:")
    for t in targets:
        print(f"  {t}: {before_types.get(t, 0)}")

    by_type = {t: [] for _, t in TYPE_SLUGS}
    total_added = total_retag = 0

    print("\n=== AE types ===")
    for slug, etype in TYPE_SLUGS:
        ae_collect(by_type, slug, etype)
    for etype in list(by_type):
        by_type[etype] = uniq_merge(by_type[etype])
        print(f"  UNIQUE {etype}: {len(by_type[etype])}")
    for etype, evs in by_type.items():
        existing, a, r = merge_and_retag(existing, evs, f"ae:{etype}")
        total_added += a
        total_retag += r
        print(f"  merge {etype}: +{a} retag={r}")

    walls.append("PAGEN_2=404; real pagination PAGEN_1+load_more. Cities skipped (ASAP).")
    walls.append(
        "AE type-is-krugliy-stol «Предстоящие» ≈2 cards starts_at>=2026-09-19; "
        "Load More hidden/no-op. Full-page ~12 cards mix past; unique ISO≈20 incl past/UI — not 50 upcoming."
    )

    print("\n=== edu-afisha ===")
    edu_all = []
    for base, etype, slug in [
        ("https://edu-afisha.ru/events/kruglye-stoly/", "Круглый стол", "kruglye-stoly"),
        ("https://edu-afisha.ru/events/biznes-zavtraki/", "Бизнес-завтрак", "biznes-zavtraki"),
        ("https://edu-afisha.ru/events/mitapy/", "Митап", "mitapy"),
        ("https://edu-afisha.ru/events/vebinary/", "Вебинар", "vebinary"),
    ]:
        for page in range(1, 3):
            url = base if page == 1 else f"{base}page/{page}/"
            html, err = fetch(url, timeout=30)
            time.sleep(0.2)
            if err or not html:
                if page == 1:
                    walls.append(f"{url}: {err}")
                break
            save_sample(f"edu_afisha_{slug}_p{page}.html", html)
            part = parse_edu_afisha(html, etype)
            print(f"  {slug} p{page}: {len(part)}")
            edu_all.extend(part)
            if not part:
                break
    edu_u = uniq_merge(edu_all)
    existing, a, r = merge_and_retag(existing, edu_u, "edu")
    total_added += a
    total_retag += r
    print(f"  edu merge +{a} retag={r}")
    evidence.append(f"edu-afisha unique typed upcoming={len(edu_u)}")

    print("\n=== ict2go ===")
    ict = []
    for url, sn in [
        ("https://ict2go.ru/events/", "ict2go_events_types.html"),
        ("https://ict2go.ru/types/meetup/", "ict2go_type_meetup.html"),
        ("https://ict2go.ru/types/webinar/", "ict2go_type_webinar.html"),
        ("https://ict2go.ru/types/seminar/", "ict2go_type_seminar.html"),
    ]:
        html, err = fetch(url, timeout=35)
        time.sleep(0.2)
        if err or not html:
            walls.append(f"{url}: {err}")
            p = SAMPLES / sn
            if p.exists():
                html = p.read_text(encoding="utf-8", errors="replace")
            else:
                continue
        else:
            save_sample(sn, html)
        part = parse_ict2go_typed(html)
        print(f"  {sn}: {len(part)}")
        ict.extend(part)
    p = SAMPLES / "ae_ict2go_events_live.html"
    if p.exists():
        ict.extend(parse_ict2go_typed(p.read_text(encoding="utf-8", errors="replace")))
    ict_u = uniq_merge(ict)
    existing, a, r = merge_and_retag(existing, ict_u, "ict")
    total_added += a
    total_retag += r
    print(f"  ict merge +{a} retag={r}")

    print("\n=== kudabiz live ===")
    kb = []
    for cat_id, etype in [
        ("6", "Бизнес-завтрак"),
        ("7", "Вебинар"),
        ("8", "Мастер-класс"),
        ("4", "Семинар"),
        ("5", "Митап"),
    ]:
        url = f"https://kudabiz.ru/live-search?cat={cat_id}&when=upcoming"
        html, err = fetch(url, timeout=25, extra_headers={"X-Requested-With": "XMLHttpRequest"})
        time.sleep(0.15)
        if err or not html:
            walls.append(f"{url}: {err}")
            continue
        save_sample(f"kb_live_cat{cat_id}.html", html)
        part = parse_kudabiz_live(html, etype)
        print(f"  cat={cat_id}: {len(part)}")
        kb.extend(part)
    kb_u = uniq_merge(kb)
    existing, a, r = merge_and_retag(existing, kb_u, "kb")
    total_added += a
    total_retag += r
    print(f"  kb merge +{a} retag={r}")

    walls.append("Series/PW (probusinessrus, adv.dp, event4etverg, bizzavtrak): SKIPPED ASAP")
    walls.append("kudabiz: no roundtable category; /cat/kruglye-stoly 404")
    walls.append("type-is-biznes-uzhin / type-is-vebinar generic; webinar+master-class OK")

    norm = 0
    for e in existing:
        t = e.get("type") or ""
        if t in TYPE_NORMALIZE and TYPE_NORMALIZE[t] in TARGET_TYPES:
            e["type"] = TYPE_NORMALIZE[t]
            norm += 1
    title_r = retitle_retag_catalog(existing)
    print(f"norm={norm} title_retag={title_r}")

    write_catalog(existing)
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    after = type_counts(final)
    print("\n==== RESULT ====")
    print(f"N {before_n} → {len(final)} ({len(final)-before_n:+d}) added={total_added} retag={total_retag}")
    print("TYPES before→after:")
    for t in targets:
        print(f"  {t}: {before_types.get(t,0)} → {after.get(t,0)} ({after.get(t,0)-before_types.get(t,0):+d})")

    report = [
        f"ALLEEVENTS TYPES FAST FINISH {TODAY.isoformat()} Europe/Moscow",
        "SKIP_PW; no cities; series skipped; index.html NOT touched",
        f"N {before_n} → {len(final)} added={total_added} retag={total_retag} norm={norm} title_retag={title_r}",
        "TYPES before→after:",
    ]
    for t in targets:
        report.append(f"  {t}: {before_types.get(t,0)} → {after.get(t,0)}")
    report.append("EVIDENCE:")
    report.extend(f"- {x}" for x in evidence)
    report.append("WALLS:")
    report.extend(f"- {w}" for w in walls)
    (SAMPLES / "ingest_report_allevents_types.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    (SAMPLES / "ingest_walls_allevents_types.txt").write_text("\n".join(walls) + "\n", encoding="utf-8")
    (SAMPLES / "ingest_evidence_allevents_types.txt").write_text("\n".join(evidence) + "\n", encoding="utf-8")
    print("\nWALLS:")
    for w in walls:
        print(f"- {w}")
    print("DONE")


if __name__ == "__main__":
    main()
