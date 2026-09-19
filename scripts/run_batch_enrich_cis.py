#!/usr/bin/env python3
"""Batch-enrich RU/CIS summaries toward ≥70% real. Preserves raw type values."""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import enrich_summaries as es

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"


def title_key(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"\b20\d{2}\b", "", t)
    t = re.sub(r"[^a-zа-яё0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def build_expomap_index() -> dict[str, str]:
    pages = []
    for th in (
        "selskoe-hozjajstvo",
        "produktyi-pischevaya-industriya",
        "zhivotnyie-veterinariya",
        "vino-alkogol-tabak",
        "landshaftnyij-dizajn-sad",
    ):
        for pg in range(1, 10):
            base = f"https://expomap.ru/expo/theme/{th}/"
            pages.append(base if pg == 1 else f"{base}?page={pg}")
    for c in ("russia", "kazakhstan", "belarus", "uzbekistan"):
        for pg in range(1, 8):
            base = f"https://expomap.ru/expo/country/{c}/"
            pages.append(base if pg == 1 else f"{base}?page={pg}")
    for th in ("selskoe-hozjajstvo", "produktyi-pischevaya-industriya", "zhivotnyie-veterinariya"):
        for pg in range(1, 4):
            base = f"https://expomap.ru/conference/theme/{th}/"
            pages.append(base if pg == 1 else f"{base}?page={pg}")

    idx: dict[str, str] = {}
    print(f"Building Expomap index from {len(pages)} listing pages…", flush=True)

    def one(url: str) -> list[tuple[str, str]]:
        out = []
        try:
            html, _ = es.fetch_html(url, timeout=35)
        except Exception:
            return out
        for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.S | re.I,
        ):
            try:
                data = json.loads(m.group(1))
            except Exception:
                continue
            stack = data if isinstance(data, list) else [data]
            while stack:
                it = stack.pop(0)
                if isinstance(it, list):
                    stack.extend(it)
                    continue
                if not isinstance(it, dict):
                    continue
                if isinstance(it.get("@graph"), list):
                    stack.extend(it["@graph"])
                t = it.get("@type")
                types = t if isinstance(t, list) else [t]
                if not any(isinstance(x, str) and "Event" in x for x in types):
                    continue
                name = (it.get("name") or "").strip()
                u = (it.get("url") or "").strip()
                if not name or not u:
                    continue
                if "/expo/" not in u and "/conference/" not in u:
                    continue
                k = title_key(name)
                if k:
                    out.append((k, u))
        return out

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(one, u) for u in pages]
        for i, fut in enumerate(as_completed(futs), 1):
            for k, u in fut.result():
                idx.setdefault(k, u)
            if i % 20 == 0:
                print(f"  listings {i}/{len(pages)} index={len(idx)}", flush=True)

    print(f"Expomap index: {len(idx)} titles", flush=True)
    (SAMPLES / "expomap_title_url_index.json").write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return idx


def lookup_expomap(title: str, idx: dict[str, str]) -> str | None:
    k = title_key(title)
    if k in idx:
        return idx[k]
    # prefix / containment soft match
    if len(k) >= 12:
        for ik, u in idx.items():
            if ik.startswith(k[:20]) or k.startswith(ik[:20]):
                return u
            if len(k) >= 16 and (k[:16] in ik or ik[:16] in k):
                return u
    return None


def enrich_one(ev: dict, idx: dict[str, str]) -> str | None:
    title = ev.get("title") or ""
    if es.is_real_summary(ev.get("description"), title):
        return None  # already good

    # 1) try with possibly resolved Expomap URL
    base = dict(ev)
    url = (ev.get("organizer_url") or "").strip()
    if es.is_generic_url(url) or "expomap" in (ev.get("source") or "").lower() or ev.get("vertical") == "apk":
        resolved = lookup_expomap(title, idx) or es.resolve_expomap_url(title)
        if resolved:
            base["organizer_url"] = resolved

    summary = es.fetch_summary_for_event(base)
    if summary and es.is_real_summary(summary, title):
        return summary

    # 2) try original url again if we swapped
    if base.get("organizer_url") != url and url and not es.is_generic_url(url):
        summary = es.fetch_summary_for_event(ev)
        if summary and es.is_real_summary(summary, title):
            return summary

    # 3) if we got a medium blob, polish to 2 sentences
    if summary and len(summary) >= 60 and not es._is_noise_summary(summary):
        polished = es.polish_summary(summary, ev)
        if es.is_real_summary(polished, title):
            return polished

    return None


def main():
    t0 = time.time()
    events = es.load_json_events()
    types_before = sorted({e.get("type") for e in events})

    cis = [e for e in events if e.get("country") in es.CIS_COUNTRIES]
    before_real = sum(1 for e in cis if es.is_real_summary(e.get("description"), e.get("title")))
    print(f"BEFORE CIS real={before_real}/{len(cis)} ({100*before_real/len(cis):.1f}%)", flush=True)

    idx = build_expomap_index()

    # Priority: apk CIS needing enrich, then other CIS needing enrich
    targets = []
    for e in events:
        if e.get("country") not in es.CIS_COUNTRIES:
            continue
        if es.is_real_summary(e.get("description"), e.get("title")):
            continue
        targets.append(e)
    targets.sort(
        key=lambda e: (
            0 if e.get("vertical") == "apk" else 1,
            e.get("starts_at") or "",
            e.get("title") or "",
        )
    )
    print(f"Targets needing enrich: {len(targets)}", flush=True)

    updates: dict[str, str] = {}
    ok = fail = 0

    def work(ev: dict):
        time.sleep(0.05)
        try:
            s = enrich_one(ev, idx)
        except Exception:
            s = None
        key = f"{ev.get('title')}|{ev.get('starts_at')}"
        return key, ev.get("title") or "", s

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(work, e) for e in targets]
        done = 0
        for fut in as_completed(futs):
            key, title, summary = fut.result()
            done += 1
            if summary and es.is_real_summary(summary, title):
                updates[key] = summary
                ok += 1
            else:
                fail += 1
            if done % 40 == 0 or done == len(targets):
                print(f"  progress {done}/{len(targets)} ok={ok} fail={fail}", flush=True)

    for e in events:
        key = f"{e.get('title')}|{e.get('starts_at')}"
        if key in updates:
            e["description"] = updates[key]
            # NEVER touch type

    meta = es.write_catalog(events)
    types_after = sorted({e.get("type") for e in events})
    assert types_before == types_after, "TYPE VALUES CHANGED — abort"

    cis2 = [e for e in events if e.get("country") in es.CIS_COUNTRIES]
    after_real = sum(1 for e in cis2 if es.is_real_summary(e.get("description"), e.get("title")))
    pct = 100.0 * after_real / len(cis2) if cis2 else 0

    examples = []
    for e in cis2:
        if es.is_real_summary(e.get("description"), e.get("title")) and e.get("title") in {
            x.split("|")[0] for x in updates
        }:
            examples.append({"title": e["title"], "summary": (e.get("description") or "")[:220]})
            if len(examples) >= 3:
                break
    if len(examples) < 3:
        for e in cis2:
            if es.is_real_summary(e.get("description"), e.get("title")):
                examples.append({"title": e["title"], "summary": (e.get("description") or "")[:220]})
                if len(examples) >= 3:
                    break

    stats = {
        "before_cis_real": before_real,
        "after_cis_real": after_real,
        "cis_n": len(cis2),
        "cis_real_pct": round(pct, 1),
        "ok": ok,
        "fail": fail,
        "expomap_index": len(idx),
        "updated_at": meta["updated_at"],
        "count": meta["count"],
        "elapsed_s": round(time.time() - t0, 1),
        "types_preserved": len(types_after),
        "examples": examples,
        "nonempty_cis_pct": round(
            100 * sum(1 for e in cis2 if (e.get("description") or "").strip()) / len(cis2), 1
        ),
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    (SAMPLES / "ingest_report_summary_enrich.txt").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"FINAL CIS real {after_real}/{len(cis2)} = {pct:.1f}%", flush=True)


if __name__ == "__main__":
    main()
