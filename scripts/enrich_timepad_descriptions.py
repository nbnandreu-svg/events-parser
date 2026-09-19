#!/usr/bin/env python3
"""Fill empty TimePad event descriptions via GET /v1/events/{id}.json. Never log token.

Updates description field by organizer_url / TimePad event id only.
Respects samples/CATALOG_WRITE_LOCK (exits if present) to avoid racing catalog writers.
"""
from __future__ import annotations

import html as html_mod
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from enrich_summaries import write_catalog, is_real_summary  # noqa: E402

JSON_PATH = ROOT / "events_upcoming.json"
LOCK_PATH = ROOT / "samples" / "CATALOG_WRITE_LOCK"
TOKEN_PATH = Path("/home/box/.secrets/TIMEPAD_TOKEN")
WORKERS = 4
SLEEP = 1.05  # ≤60 req/min


def load_token() -> str:
    t = (os.environ.get("TIMEPAD_TOKEN") or "").strip()
    if t:
        return t
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def strip_html(t: str) -> str:
    t = html_mod.unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def event_id_from_url(url: str) -> str | None:
    m = re.search(r"/event/(\d+)", url or "")
    return m.group(1) if m else None


def fetch_desc(token: str, eid: str) -> str:
    url = (
        f"https://api.timepad.ru/v1/events/{eid}.json"
        f"?fields=name,description_short,description_html"
    )
    r = subprocess.run(
        [
            "curl",
            "-sS",
            "--max-time",
            "25",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Accept: application/json",
            "-H",
            "User-Agent: EventsCalendarBot/1.0",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=35,
    )
    if r.returncode != 0 or not r.stdout:
        return ""
    try:
        j = json.loads(r.stdout)
    except json.JSONDecodeError:
        return ""
    short = strip_html(j.get("description_short") or "")
    if len(short) >= 80:
        return short
    long = strip_html(j.get("description_html") or "")
    return long if len(long) >= 80 else (short or long)


def main() -> None:
    if LOCK_PATH.exists():
        print(f"PAUSED: catalog write lock present at {LOCK_PATH}", flush=True)
        raise SystemExit(0)

    token = load_token()
    events = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    # Collect targets by stable keys (url / tp id), not list index
    targets: list[tuple[str, str, str]] = []  # (url_key, eid, title)
    seen_eid: set[str] = set()
    for e in events:
        if e.get("source") != "timepad":
            continue
        if is_real_summary(e.get("description"), e.get("title")):
            continue
        url = (e.get("organizer_url") or "").strip()
        eid = event_id_from_url(url)
        if not eid or eid in seen_eid:
            continue
        seen_eid.add(eid)
        targets.append((url.lower(), eid, e.get("title") or ""))

    print(f"targets {len(targets)}", flush=True)
    ok = fail = 0
    results: dict[str, str] = {}  # eid -> desc

    def work(item: tuple[str, str, str]):
        url_key, eid, title = item
        time.sleep(SLEEP)
        desc = fetch_desc(token, eid)
        return url_key, eid, title, desc

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(work, t) for t in targets]
        done = 0
        for fut in as_completed(futs):
            url_key, eid, title, desc = fut.result()
            done += 1
            if desc and is_real_summary(desc, title):
                results[eid] = desc
                ok += 1
            elif desc and len(desc) >= 80:
                if desc.strip().lower() != title.strip().lower():
                    results[eid] = desc
                    ok += 1
                else:
                    fail += 1
            else:
                fail += 1
            if done % 50 == 0 or done == len(targets):
                print(f"progress {done}/{len(targets)} ok={ok} fail={fail}", flush=True)

    if LOCK_PATH.exists():
        print(f"PAUSED before write: lock appeared at {LOCK_PATH}; descriptions not applied", flush=True)
        raise SystemExit(0)

    # Reload catalog so we only patch description by id/url — never stale overwrite
    events = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    applied = 0
    for e in events:
        if e.get("source") != "timepad":
            continue
        eid = event_id_from_url(e.get("organizer_url") or "")
        if eid and eid in results:
            if not is_real_summary(e.get("description"), e.get("title")):
                e["description"] = results[eid]
                applied += 1

    meta = write_catalog(events)
    try:
        from enrich_summaries import CIS_COUNTRIES

        cis = [e for e in events if e.get("country") in CIS_COUNTRIES]
    except Exception:
        cis = [e for e in events if e.get("country") == "Россия"]

    empty = sum(1 for e in cis if not is_real_summary(e.get("description"), e.get("title")))
    tp_empty = sum(
        1
        for e in events
        if e.get("source") == "timepad"
        and not is_real_summary(e.get("description"), e.get("title"))
    )
    report = {
        "targets": len(targets),
        "ok": ok,
        "fail": fail,
        "applied_by_id": applied,
        "meta": meta,
        "cis_n": len(cis),
        "cis_empty": empty,
        "timepad_empty_left": tp_empty,
        "note": "description-only updates keyed by TimePad event id / organizer_url",
    }
    (ROOT / "samples" / "ingest_report_timepad_enrich.txt").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("DONE", json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
