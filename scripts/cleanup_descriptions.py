#!/usr/bin/env python3
"""Clean catalog descriptions for the public demo.

Strips HTML, drops listing-hub leaks / stale years / foreign blurbs,
polishes short on-topic one-liners with date and place.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from enrich_summaries import (  # noqa: E402
    _norm,
    belongs_to_title,
    clean_description,
    is_usable_description,
    write_catalog,
)

JSON_PATH = Path(__file__).resolve().parents[1] / "events_upcoming.json"
if not JSON_PATH.exists():
    JSON_PATH = ROOT / "events_upcoming.json"
SAMPLES = JSON_PATH.parent / "samples"


def strip_title_echo(title: str, desc: str) -> str:
    nt = _norm(title)
    if not nt or not desc:
        return desc
    parts = re.split(r"(?<=[.!?])\s+", desc.strip())
    if len(parts) < 2:
        return desc
    last = _norm(parts[-1])
    if last and (last == nt or (nt in last and len(last) < len(nt) + 20)):
        return " ".join(parts[:-1]).strip()
    return desc


def title_family(title: str) -> str:
    t = (title or "").lower().replace("ё", "е")
    t = re.sub(r"\b20\d{2}\b", " ", t)
    t = re.sub(r"[^a-zа-я0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def apply_cleanup(events: list[dict]) -> dict:
    stats = Counter()
    # First pass: clean + drop obvious junk
    for e in events:
        raw = e.get("description") or ""
        cleaned = strip_title_echo(e.get("title") or "", clean_description(raw))
        if cleaned != raw:
            stats["html_or_ws_cleaned"] += 1
        if not cleaned:
            if raw:
                stats["cleared_empty"] += 1
            e["description"] = ""
            continue
        if not belongs_to_title(e.get("title") or "", cleaned):
            e["description"] = ""
            stats["cleared_mismatch"] += 1
            continue
        if not is_usable_description(cleaned, e.get("title")):
            e["description"] = ""
            stats["cleared_unusable"] += 1
            continue
        e["description"] = cleaned
        stats["kept"] += 1

    # Second pass: same blurb on 3+ unrelated title families → keep only matching titles
    by_prefix: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        d = e.get("description") or ""
        if len(d) < 80:
            continue
        by_prefix[d[:180]].append(e)
    for prefix, group in by_prefix.items():
        families = {title_family(e.get("title") or "") for e in group}
        families.discard("")
        if len(families) < 3:
            continue
        for e in group:
            if not belongs_to_title(e.get("title") or "", e.get("description") or ""):
                e["description"] = ""
                stats["cleared_shared_leak"] += 1
                continue
            # If the shared text does not mention this title family at all, drop it
            fam = title_family(e.get("title") or "")
            toks = {w for w in fam.split() if len(w) >= 5}
            low = (e.get("description") or "").lower().replace("ё", "е")
            if toks and not any(w in low for w in toks):
                e["description"] = ""
                stats["cleared_shared_leak"] += 1

    nonempty = sum(1 for e in events if (e.get("description") or "").strip())
    usable = sum(1 for e in events if is_usable_description(e.get("description"), e.get("title")))
    stats["total"] = len(events)
    stats["nonempty_after"] = nonempty
    stats["usable_after"] = usable
    return dict(stats)


def main() -> None:
    events = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    stats = apply_cleanup(events)
    meta = write_catalog(events)
    stats["written_count"] = meta.get("count")
    stats["updated_at"] = meta.get("updated_at")
    report = json.dumps(stats, ensure_ascii=False, indent=2) + "\n"
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / "cleanup_descriptions_report.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
