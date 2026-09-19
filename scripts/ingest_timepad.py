#!/usr/bin/env python3
"""TimePad API ingest for recurring formats. Token from env TIMEPAD_TOKEN or /home/box/.secrets/TIMEPAD_TOKEN. Never log the token."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from collections import Counter
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from ingest_round2 import TODAY, make_event, vertical_for  # noqa: E402
from ingest_round3 import merge  # noqa: E402
from enrich_summaries import write_catalog, is_real_summary  # noqa: E402

SAMPLES = ROOT / "samples"
JSON_PATH = ROOT / "events_upcoming.json"
TOKEN_PATHS = [
    Path(os.environ.get("TIMEPAD_TOKEN_FILE") or ""),
    Path("/home/box/.secrets/TIMEPAD_TOKEN"),
    Path.home() / ".timepad_token",
]

# keyword query → preferred exact type (first match wins on classify)
KEYWORD_QUERIES = [
    "бизнес-завтрак",
    "завтрак",
    "круглый стол",
    "бизнес-ужин",
    "ужин",
    "митап",
    "meetup",
    "вебинар",
    "семинар",
    "нетворкинг",
    "мастер-класс",
    "мастер класс",
]

TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Бизнес-завтрак", re.compile(r"бизнес[-\s]?завтрак\w*|завтрак\w*\s+для|деловой\s+завтрак\w*|образовательн\w*\s+завтрак\w*|партнёрск\w*\s+завтрак\w*|партнерск\w*\s+завтрак\w*", re.I)),
    ("Бизнес-завтрак", re.compile(r"завтрак\w*", re.I)),
    ("Круглый стол", re.compile(r"кругл\w*\s+стол\w*", re.I)),
    ("Бизнес-ужин", re.compile(r"бизнес[-\s]?ужин\w*", re.I)),
    ("Бизнес-ужин", re.compile(r"(?<![а-яё])ужин\w*(?![а-яё])", re.I)),
    ("Митап", re.compile(r"митап\w*|meetup|meet[\s-]?up", re.I)),
    ("Вебинар", re.compile(r"вебинар\w*", re.I)),
    ("Семинар", re.compile(r"семинар\w*", re.I)),
    ("Мастер-класс", re.compile(r"мастер[-\s]?класс\w*", re.I)),
    ("Нетворкинг", re.compile(r"нетворкинг\w*|networking", re.I)),
]

TARGET_TYPES = {
    "Бизнес-завтрак",
    "Круглый стол",
    "Бизнес-ужин",
    "Митап",
    "Вебинар",
    "Семинар",
    "Мастер-класс",
    "Нетворкинг",
}

# Business + IT categories for extra sweep (client-side title filter)
CATEGORY_SWEEP = [217, 452]


def load_token() -> str:
    env = (os.environ.get("TIMEPAD_TOKEN") or "").strip()
    if env:
        return env
    for p in TOKEN_PATHS:
        if p and p.is_file():
            return p.read_text(encoding="utf-8").strip()
    raise SystemExit("TIMEPAD_TOKEN missing (env or secret file)")


def api_get(token: str, params: dict) -> tuple[int, dict]:
    """Returns (http_status, payload). Uses curl — Python TLS often gets 403."""
    q = urllib.parse.urlencode(params, doseq=True)
    url = f"https://api.timepad.ru/v1/events.json?{q}"
    r = subprocess.run(
        [
            "curl", "-sS", "-w", "\n%{http_code}",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Accept: application/json",
            "-H", "User-Agent: EventsCalendarBot/1.0",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr[:200]}")
    out = r.stdout or ""
    # last line is http code
    if "\n" in out:
        body, _, code_s = out.rpartition("\n")
    else:
        body, code_s = out, "0"
    try:
        status = int(code_s.strip())
    except ValueError:
        status = 0
        body = out
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {body[:160]}")
    try:
        return status, json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"bad json ({body[:120]})") from e


def classify_type(name: str, categories: list[dict] | None = None) -> Optional[str]:
    title = unescape(name or "")
    for etype, pat in TYPE_PATTERNS:
        if pat.search(title):
            return etype
    return None


def parse_starts(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        # 2026-09-24T09:30:00+0300
        s2 = s.replace("+0300", "+03:00").replace("+0000", "+00:00")
        if len(s2) >= 10:
            return date.fromisoformat(s2[:10])
    except Exception:
        return None
    return None


def strip_html(t: str) -> str:
    t = unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def event_from_tp(raw: dict, forced_type: Optional[str] = None) -> Optional[dict]:
    name = strip_html(raw.get("name") or "")
    if not name:
        return None
    etype = forced_type or classify_type(name, raw.get("categories"))
    if etype not in TARGET_TYPES:
        return None
    starts_d = parse_starts(raw.get("starts_at") or "")
    if not starts_d or starts_d < TODAY:
        return None
    if starts_d.year > 2027:
        return None
    ends_d = parse_starts(raw.get("ends_at") or "") or starts_d
    url = (raw.get("url") or "").strip()
    if not url:
        return None
    loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    city = (loc.get("city") or raw.get("city") or "").strip()
    if city.lower() in {"спб", "питер", "петербург"}:
        city = "Санкт-Петербург"
    if city.lower() in {"online", "remote"} or "онлайн" in city.lower():
        city = "Онлайн"
    # skip obvious non-RF
    if re.search(r"london|paris|berlin|warsaw|киев|kyiv|алматы|дубай|dubai", city, re.I):
        return None
    country = "Россия"
    desc = strip_html(raw.get("description_short") or raw.get("description") or "")
    cats = " ".join(c.get("name") or "" for c in (raw.get("categories") or []))
    blob = f"{name} {cats} {desc}"
    vert = vertical_for(name, blob, etype=etype, source="timepad")
    if vert is None:
        return None
    if etype in {"Митап", "Вебинар"} and vert == "industry":
        if re.search(r"(?<![а-яёa-z])(it|айти|python|dev|разработ|saas|ai|ml|ии)(?![а-яёa-z])", blob, re.I):
            vert = "it"
    ev = make_event(
        name,
        starts_d,
        ends_d,
        url,
        "timepad",
        city=city or "",
        country=country,
        etype=etype,
        extra_vert=blob,
        description=desc[:500],
    )
    if not ev:
        return None
    ev["vertical"] = vert
    return ev


def fetch_keyword(token: str, keyword: str, limit_per_page: int = 100) -> list[dict]:
    out: list[dict] = []
    skip = 0
    total = None
    while True:
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
        if total is None:
            total = int(data.get("total") or 0)
        vals = data.get("values") or []
        if not vals:
            break
        out.extend(vals)
        skip += len(vals)
        if skip >= total or len(vals) < limit_per_page:
            break
        time.sleep(0.15)
    return out


def fetch_category_filtered(token: str, category_id: int, max_pages: int = 20) -> list[dict]:
    """Pull business/IT category pages and keep only title-matched recurring formats."""
    out: list[dict] = []
    skip = 0
    for _ in range(max_pages):
        _st, data = api_get(
            token,
            {
                "limit": 100,
                "skip": skip,
                "category_ids": category_id,
                "starts_at_min": TODAY.isoformat(),
                "starts_at_max": "2027-12-31",
                "sort": "+starts_at",
                "fields": ["location", "description_short"],
            },
        )
        vals = data.get("values") or []
        if not vals:
            break
        for raw in vals:
            if classify_type(raw.get("name") or ""):
                out.append(raw)
        skip += len(vals)
        total = int(data.get("total") or 0)
        if skip >= total or len(vals) < 100:
            break
        time.sleep(0.12)
    return out


def main() -> None:
    token = load_token()
    walls: list[str] = []
    stats: dict[str, Any] = {"keyword_totals": {}, "fetched_raw": 0, "classified": 0, "smoke_http": None}

    # Smoke call — report HTTP status (never echo token)
    try:
        smoke_st, smoke_data = api_get(
            token,
            {
                "limit": 2,
                "starts_at_min": TODAY.isoformat(),
                "starts_at_max": "2027-12-31",
                "sort": "+starts_at",
                "fields": ["location"],
            },
        )
        stats["smoke_http"] = smoke_st
        stats["smoke_total"] = smoke_data.get("total")
        print(f"SMOKE HTTP: {smoke_st} total_window={smoke_data.get('total')}", flush=True)
    except Exception as e:
        stats["smoke_http"] = getattr(e, "args", [None])[0]
        print(f"SMOKE FAIL: {e}", flush=True)
        raise SystemExit(f"TimePad smoke failed: {e}")
    time.sleep(0.4)

    by_id: dict[int, dict] = {}
    for kw in KEYWORD_QUERIES:
        try:
            rows = fetch_keyword(token, kw)
            stats["keyword_totals"][kw] = len(rows)
            for r in rows:
                rid = r.get("id")
                if rid is not None:
                    by_id[int(rid)] = r
            print(f"kw {kw!r}: {len(rows)}", flush=True)
        except Exception as e:
            walls.append(f"keywords {kw}: {type(e).__name__}")
            print(f"WALL kw {kw}: {type(e).__name__}", flush=True)

    for cid in CATEGORY_SWEEP:
        try:
            rows = fetch_category_filtered(token, cid)
            stats[f"cat_{cid}_matched"] = len(rows)
            for r in rows:
                rid = r.get("id")
                if rid is not None:
                    by_id.setdefault(int(rid), r)
            print(f"cat {cid} matched titles: {len(rows)}", flush=True)
        except Exception as e:
            walls.append(f"category {cid}: {type(e).__name__}")
            print(f"WALL cat {cid}: {type(e).__name__}", flush=True)

    stats["fetched_raw"] = len(by_id)
    new_events: list[dict] = []
    type_c: Counter = Counter()
    for raw in by_id.values():
        ev = event_from_tp(raw)
        if not ev:
            continue
        new_events.append(ev)
        type_c[ev.get("type") or ""] += 1
    stats["classified"] = len(new_events)
    stats["types_new"] = dict(type_c)

    existing = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    before = Counter(e.get("type") or "" for e in existing)
    before_n = len(existing)

    # Prefer richer description when merging same URL: merge() skips dupes —
    # first upgrade empty descriptions on soft match
    soft_map = {}
    for e in existing:
        soft_map[((e.get("title") or "").strip().lower(), e.get("starts_at") or "")] = e

    upgraded = 0
    for ev in new_events:
        k = ((ev.get("title") or "").strip().lower(), ev.get("starts_at") or "")
        old = soft_map.get(k)
        if old and not is_real_summary(old.get("description"), old.get("title")):
            if is_real_summary(ev.get("description"), ev.get("title")):
                old["description"] = ev["description"]
                upgraded += 1
            if (old.get("type") or "") != (ev.get("type") or "") and ev.get("type") in TARGET_TYPES:
                # retag weak generic types
                if (old.get("type") or "") in {"Мероприятие", "Прочее", "Конференция", "Выставка", ""}:
                    old["type"] = ev["type"]
                    upgraded += 1

    existing = merge(existing, new_events, "timepad")
    after = Counter(e.get("type") or "" for e in existing)
    meta = write_catalog(existing)

    report = {
        "stats": stats,
        "upgraded": upgraded,
        "before_n": before_n,
        "after_n": len(existing),
        "meta": meta,
        "types_before": {k: before.get(k, 0) for k in sorted(TARGET_TYPES)},
        "types_after": {k: after.get(k, 0) for k in sorted(TARGET_TYPES)},
        "walls": walls,
    }
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / "ingest_report_timepad.txt").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SAMPLES / "ingest_walls_timepad.txt").write_text(
        "\n".join(walls) or "(none)", encoding="utf-8"
    )
    focus = ["Бизнес-завтрак", "Круглый стол", "Бизнес-ужин", "Митап", "Семинар"]
    print(f"SMOKE HTTP: {stats.get('smoke_http')}", flush=True)
    print(f"N before={before_n} after={len(existing)}", flush=True)
    print("FOCUS before→after:", flush=True)
    for t in focus:
        b, a = before.get(t, 0), after.get(t, 0)
        print(f"  {t}: {b} → {a} ({a-b:+d})", flush=True)
    print("DONE", json.dumps({
        "smoke_http": stats.get("smoke_http"),
        "n": len(existing),
        "types_after": report["types_after"],
        "types_before": report["types_before"],
        "walls": walls,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
