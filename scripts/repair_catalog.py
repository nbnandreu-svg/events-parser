#!/usr/bin/env python3
"""One-shot catalog repair after reclass/kudago/timepad race. Single atomic write at end."""
from __future__ import annotations

import json
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from enrich_summaries import write_catalog  # noqa: E402
from ingest_round2 import LIFESTYLE_RE, vertical_for  # noqa: E402
from ingest_round3 import merge  # noqa: E402
import ingest_timepad as tp  # noqa: E402

JSON_PATH = ROOT / "events_upcoming.json"
JS_PATH = ROOT / "events-data.js"
SAMPLES = ROOT / "samples"
LOCK_PATH = SAMPLES / "CATALOG_WRITE_LOCK"
BACKUP_PATH = SAMPLES / "events_upcoming_pre_repair_2748.json"
KUDAGO_SAMPLE = SAMPLES / "ingest_kudago_exponet_events.json"
REPORT_PATH = SAMPLES / "catalog_repair_report.txt"
MSK = ZoneInfo("Europe/Moscow")
FOCUS = ("Бизнес-завтрак", "Бизнес-ужин", "Круглый стол")

# ≤60 API calls / min
MIN_INTERVAL = 1.05
_last_api = 0.0
_orig_api_get = None


def url_key(e: dict) -> str:
    return (e.get("organizer_url") or e.get("url") or "").strip().lower()


def reclass(events: list[dict]) -> tuple[list[dict], int, list[dict]]:
    kept: list[dict] = []
    dropped: list[dict] = []
    for e in events:
        title = e.get("title") or ""
        desc = e.get("description") or ""
        etype = e.get("type") or ""
        source = e.get("source") or ""
        new_v = vertical_for(title, desc, etype=etype, source=source)
        if new_v is None:
            dropped.append(e)
            continue
        e2 = dict(e)
        e2["vertical"] = new_v
        kept.append(e2)
    return kept, len(dropped), dropped


def merge_kudago(events: list[dict], sample: list[dict]) -> tuple[list[dict], int]:
    by_url = {url_key(e) for e in events if url_key(e)}
    soft = {((e.get("title") or "").strip().lower(), e.get("starts_at") or "") for e in events}
    added = 0
    for e in sample:
        u = url_key(e)
        sk = ((e.get("title") or "").strip().lower(), e.get("starts_at") or "")
        if u and u in by_url:
            continue
        if sk in soft:
            continue
        # reclass sample row too
        v = vertical_for(
            e.get("title") or "",
            e.get("description") or "",
            etype=e.get("type") or "",
            source=e.get("source") or "",
        )
        if v is None:
            continue
        e2 = dict(e)
        e2["vertical"] = v
        events.append(e2)
        if u:
            by_url.add(u)
        soft.add(sk)
        added += 1
    return events, added


def fetch_timepad_b2b() -> list[dict]:
    """Fetch TimePad upcoming B2B-only; lifestyle skipped by vertical_for."""
    global _orig_api_get
    _orig_api_get = tp.api_get

    def throttled(token: str, params: dict):
        global _last_api
        backoff = 60.0
        for attempt in range(8):
            now = time.monotonic()
            wait = MIN_INTERVAL - (now - _last_api)
            if wait > 0:
                time.sleep(wait)
            try:
                out = _orig_api_get(token, params)
                _last_api = time.monotonic()
                return out
            except RuntimeError as e:
                msg = str(e)
                if "HTTP 429" not in msg:
                    raise
                print(f"429 backoff {backoff:.0f}s (attempt {attempt+1})", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 300.0)
                _last_api = time.monotonic()
        raise RuntimeError("TimePad still 429 after retries")

    tp.api_get = throttled  # type: ignore
    # also pad inner page sleeps to stay under 60/min even if both fire
    token = tp.load_token()
    walls: list[str] = []
    by_id: dict[int, dict] = {}

    smoke_st, smoke_data = tp.api_get(
        token,
        {
            "limit": 2,
            "starts_at_min": tp.TODAY.isoformat(),
            "starts_at_max": "2027-12-31",
            "sort": "+starts_at",
            "fields": ["location"],
        },
    )
    print(f"SMOKE HTTP: {smoke_st} total_window={smoke_data.get('total')}", flush=True)

    for kw in tp.KEYWORD_QUERIES:
        try:
            rows = tp.fetch_keyword(token, kw)
            for r in rows:
                rid = r.get("id")
                if rid is not None:
                    by_id[int(rid)] = r
            print(f"kw {kw!r}: {len(rows)}", flush=True)
        except Exception as e:
            walls.append(f"keywords {kw}: {type(e).__name__}: {e}")
            print(f"WALL kw {kw}: {e}", flush=True)

    for cid in tp.CATEGORY_SWEEP:
        try:
            rows = tp.fetch_category_filtered(token, cid)
            for r in rows:
                rid = r.get("id")
                if rid is not None:
                    by_id.setdefault(int(rid), r)
            print(f"cat {cid} matched titles: {len(rows)}", flush=True)
        except Exception as e:
            walls.append(f"category {cid}: {type(e).__name__}: {e}")
            print(f"WALL cat {cid}: {e}", flush=True)

    new_events: list[dict] = []
    type_c: Counter = Counter()
    for raw in by_id.values():
        ev = tp.event_from_tp(raw)
        if not ev:
            continue
        new_events.append(ev)
        type_c[ev.get("type") or ""] += 1

    print(
        f"timepad raw_ids={len(by_id)} b2b_classified={len(new_events)} types={dict(type_c)}",
        flush=True,
    )
    if walls:
        print("walls:", walls, flush=True)
    return new_events


def focus_by_vertical(events: list[dict]) -> dict[str, dict]:
    out: dict[str, Counter] = {t: Counter() for t in FOCUS}
    for e in events:
        t = e.get("type") or ""
        if t in out:
            out[t][e.get("vertical") or "industry"] += 1
    return {t: dict(c) for t, c in out.items()}


def atomic_write(events: list[dict]) -> dict:
    """Write json+js once with MSK timestamp in EVENTS_META."""
    meta = write_catalog(events)
    # Patch meta to include MSK label (box is already Europe/Moscow)
    now = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")
    js = JS_PATH.read_text(encoding="utf-8")
    # replace EVENTS_META line
    import re

    meta2 = {"updated_at": now, "count": meta["count"]}
    js2 = re.sub(
        r"window\.EVENTS_META = \{.*?\};",
        "window.EVENTS_META = " + json.dumps(meta2, ensure_ascii=False) + ";",
        js,
        count=1,
    )
    JS_PATH.write_text(js2, encoding="utf-8")
    return meta2


def main() -> None:
    LOCK_PATH.parent.mkdir(exist_ok=True)
    LOCK_PATH.write_text("repair_catalog\n", encoding="utf-8")

    if not BACKUP_PATH.exists():
        shutil.copy2(JSON_PATH, BACKUP_PATH)
        print(f"backup created {BACKUP_PATH}", flush=True)
    else:
        print(f"backup exists {BACKUP_PATH}", flush=True)

    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    n_before = len(data)
    print(f"loaded N={n_before}", flush=True)

    # 2. Offline reclass
    kept, n_dropped, dropped = reclass(data)
    print(f"reclass kept={len(kept)} dropped_lifestyle={n_dropped}", flush=True)

    # 3. Merge kudago/exponet durable sample
    sample = json.loads(KUDAGO_SAMPLE.read_text(encoding="utf-8"))
    kept, n_kudago_added = merge_kudago(kept, sample)
    print(f"kudago/exponet merge added={n_kudago_added} sample_n={len(sample)}", flush=True)

    # 4. Re-fetch TimePad B2B and merge (no lifestyle)
    tp_new = fetch_timepad_b2b()
    before_tp = sum(1 for e in kept if e.get("source") == "timepad")
    kept = merge(kept, tp_new, "timepad")
    after_tp = sum(1 for e in kept if e.get("source") == "timepad")
    print(f"timepad merge before={before_tp} after={after_tp} fetched_b2b={len(tp_new)}", flush=True)

    # 5. Single atomic write
    meta = atomic_write(kept)
    print(f"wrote catalog meta={meta}", flush=True)

    # 6. Report
    n_after = len(kept)
    # recount from disk to be sure
    disk = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    src = Counter(e.get("source") or "?" for e in disk)
    verts = Counter(e.get("vertical") or "?" for e in disk)
    focus = focus_by_vertical(disk)
    drop_types = Counter((e.get("type") or "?") for e in dropped)
    drop_src = Counter((e.get("source") or "?") for e in dropped)

    breakfast_n = sum(focus.get("Бизнес-завтрак", {}).values())
    bf_ind_share = (
        (focus.get("Бизнес-завтрак", {}).get("industry", 0) / breakfast_n)
        if breakfast_n
        else 0.0
    )

    now = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")
    lines = [
        "Catalog repair report",
        f"Updated: {now}",
        f"EVENTS_META: {json.dumps(meta, ensure_ascii=False)}",
        "",
        "STEPS: backup → offline reclass (drop lifestyle) → merge kudago/exponet sample",
        "     → TimePad B2B refetch (vertical_for skips lifestyle) → atomic write once",
        "",
        f"N before: {n_before}",
        f"N after:  {n_after} (disk={len(disk)})",
        f"Dropped lifestyle / non-B2B: {n_dropped}",
        f"Kudago/exponet sample merge added: {n_kudago_added}",
        f"TimePad B2B fetched (classified): {len(tp_new)}",
        f"TimePad count after: {src.get('timepad', 0)} (was {before_tp} post-reclass before merge)",
        f"kudago: {src.get('kudago', 0)}",
        f"exponet: {src.get('exponet', 0)}",
        f"kudago+exponet: {src.get('kudago', 0) + src.get('exponet', 0)}",
        "",
        "Verticals: " + json.dumps(dict(verts), ensure_ascii=False),
        "",
        "=== Breakfast / dinner / roundtable by vertical ===",
    ]
    for t in FOCUS:
        c = focus.get(t, {})
        lines.append(f"{t}: n={sum(c.values())} {c}")
    lines.append(f"Breakfast industry share: {bf_ind_share:.1%} (should not be ~90%)")
    lines.append("")
    lines.append("Dropped by type (top):")
    for k, n in drop_types.most_common(15):
        lines.append(f"  {k}: {n}")
    lines.append("Dropped by source (top):")
    for k, n in drop_src.most_common(10):
        lines.append(f"  {k}: {n}")
    lines.append("")
    lines.append(f"Backup: {BACKUP_PATH}")
    lines.append("Lock: released after write")
    lines.append("enrich_timepad_descriptions.py: description-only by event id; respects lock")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT_PATH.read_text(encoding="utf-8"), flush=True)

    # release lock
    if LOCK_PATH.exists():
        LOCK_PATH.unlink()
    print("LOCK released", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # keep lock on failure so enrich doesn't race mid-failure
        raise
