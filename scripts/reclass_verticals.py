#!/usr/bin/env python3
"""Offline vertical reclassification for B2B catalog (IT / АПК / Пром).

Rewrites events_upcoming.json + events-data.js; drops clear non-B2B lifestyle.
Writes samples/vertical_reclass_report.txt
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from enrich_summaries import write_catalog
from ingest_round2 import LIFESTYLE_RE, vertical_for

ROOT = Path(__file__).resolve().parent
JSON_PATH = ROOT / "events_upcoming.json"
REPORT_PATH = ROOT / "samples" / "vertical_reclass_report.txt"
MSK = ZoneInfo("Europe/Moscow")

FOCUS = ("Бизнес-завтрак", "Бизнес-ужин", "Круглый стол")


def main() -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    before_n = len(data)
    before_all = Counter(e.get("vertical") or "industry" for e in data)
    before_focus: dict[str, Counter] = {t: Counter() for t in FOCUS}
    for e in data:
        t = e.get("type") or ""
        if t in before_focus:
            before_focus[t][e.get("vertical") or "industry"] += 1

    kept: list[dict] = []
    dropped: list[dict] = []
    changed = 0
    samples_dropped: list[str] = []
    samples_retyped: list[str] = []

    for e in data:
        title = e.get("title") or ""
        desc = e.get("description") or ""
        etype = e.get("type") or ""
        source = e.get("source") or ""
        old_v = e.get("vertical") or "industry"
        new_v = vertical_for(title, desc, etype=etype, source=source)
        if new_v is None:
            dropped.append(e)
            if len(samples_dropped) < 60 and (
                etype in FOCUS
                or LIFESTYLE_RE.search(title)
                or etype in {"Мастер-класс", "Нетворкинг"}
            ):
                samples_dropped.append(f"  [{etype}] {title[:100]}")
            continue
        if new_v != old_v:
            changed += 1
            if len(samples_retyped) < 40:
                samples_retyped.append(f"  {old_v} → {new_v} [{etype}] {title[:90]}")
            e = dict(e)
            e["vertical"] = new_v
        else:
            e = dict(e)
            e["vertical"] = new_v
        kept.append(e)

    after_focus = {t: Counter() for t in FOCUS}
    for e in kept:
        t = e.get("type") or ""
        if t in after_focus:
            after_focus[t][e.get("vertical") or "industry"] += 1
    after_all = Counter(e.get("vertical") or "industry" for e in kept)

    meta = write_catalog(kept)
    now = datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK")

    lines = [
        "Vertical reclassification report",
        f"Updated: {now}",
        f"Catalog meta: {meta}",
        "",
        "GOAL: IT / АПК / Пром only. Lifestyle/culture/art/religion/kids → dropped.",
        "Unknown soft formats (завтрак/ужин/нетворкинг/…) no longer default to industry.",
        "",
        f"N before: {before_n}",
        f"N after:  {len(kept)}",
        f"Dropped non-B2B: {len(dropped)}",
        f"Vertical changed (among kept): {changed}",
        "",
        "All verticals before: " + json.dumps(dict(before_all), ensure_ascii=False),
        "All verticals after:  " + json.dumps(dict(after_all), ensure_ascii=False),
        "",
        "=== FOCUS before → after ===",
    ]
    for t in FOCUS:
        b, a = before_focus[t], after_focus[t]
        lines.append(
            f"{t}: before n={sum(b.values())} {dict(b)} → after n={sum(a.values())} {dict(a)}"
        )
        for v in ("industry", "it", "apk"):
            lines.append(f"  {v}: {b.get(v, 0)} → {a.get(v, 0)} ({a.get(v, 0) - b.get(v, 0):+d})")
    lines.append("")
    lines.append("Dropped by type (top):")
    drop_types = Counter((e.get("type") or "?") for e in dropped)
    for k, n in drop_types.most_common(15):
        lines.append(f"  {k}: {n}")
    lines.append("")
    lines.append("Sample dropped:")
    lines.extend(samples_dropped or ["  (none)"])
    lines.append("")
    lines.append("Sample retyped:")
    lines.extend(samples_retyped or ["  (none)"])
    lines.append("")
    lines.append("Classifier notes:")
    lines.append("- LIFESTYLE_RE on title (and desc for soft/timepad) → exclude")
    lines.append("- APK_RE / IT_RE / INDUSTRY_RE keyword heuristics on title+description")
    lines.append("- Explicit бизнес-завтрак/ужин without lifestyle → industry")
    lines.append("- Soft/timepad without keywords → exclude (no industry default)")
    lines.append("- Curated APK/IT sources default to apk/it when keywords miss")
    lines.append("- Hard curated types (выставка/конференция/…) still default industry")
    lines.append("- Fixed APK false positive: «поле» no longer matches «полезные»")
    lines.append("")

    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"N {before_n} → {len(kept)}; dropped={len(dropped)}; changed={changed}")
    for t in FOCUS:
        b, a = before_focus[t], after_focus[t]
        print(f"{t}: {dict(b)} n={sum(b.values())} → {dict(a)} n={sum(a.values())}")
    print(f"report → {REPORT_PATH}")
    print(f"meta {meta}")


if __name__ == "__main__":
    main()
