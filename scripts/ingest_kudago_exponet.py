#!/usr/bin/env python3
"""Ingest KudaGo (public REST) + Exponet XML calendar into events_upcoming.json.

Sources: kudago, exponet. No TimePad. No paid scrapers.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from ingest_round2 import TODAY, make_event, vertical_for, strip_tags  # noqa: E402
from ingest_round3 import merge, fuzzy_title  # noqa: E402
from enrich_summaries import write_catalog  # noqa: E402

SAMPLES = ROOT / "samples"
JSON_PATH = ROOT / "events_upcoming.json"
UA = "EventsCalendarBot/1.0 (+events-calendar-local; research ingest)"

LOC_CITY = {
    "msk": "Москва",
    "spb": "Санкт-Петербург",
    "ekb": "Екатеринбург",
    "kzn": "Казань",
    "nnv": "Нижний Новгород",
}
LOCATIONS = list(LOC_CITY.keys())

# Exponet topic slugs useful for it/apk/industry
EXPONET_TOPICS = [
    "agriculture",  # apk
    "forest",
    "building",
    "transport",
    "oil",
    "promexpo",
    "infotech",  # it
    "business",
    "municipal",
    "furniture",
    "healthcare",
    "pets",  # apk-adjacent
]
CIS_COUNTRIES = {
    "rus": "Россия",
    "blr": "Беларусь",
    "kaz": "Казахстан",
    "uzb": "Узбекистан",
    "aze": "Азербайджан",
    "geo": "Грузия",
    "kgz": "Кыргызстан",
    "mda": "Молдова",
}

# Lifestyle topic markers in Exponet topic_id text → drop unless also agri/industry/IT
EXPONET_LIFESTYLE_ONLY = re.compile(
    r"^(Ярмарки|Товары народного потребления|Мода|Спорт|Культура|Досуг)$",
    re.I,
)

KUDAGO_B2B = re.compile(
    r"бизнес[-\s]?(завтрак|ужин|форум|конференц|мероприят)|"
    r"конференци|форум|семинар|нетворкинг|митап|meetup|вебинар|"
    r"стартап|предпринимател|инвест(ор|иц|ирован)|финтех|"
    r"цифров(ой|ая|ые|ых|изац)|информационн(ые|ых|ая)\s+технолог|"
    r"\bIT\b|айти|devops|кибербез|"
    r"промышленн|индустриальн|"
    r"лёгк(ой|ая)\s+и\s+текстильн|текстильн\w*\s+промышлен|"
    r"выставк\w*.{0,40}(промышлен|отрасл|бизнес|торгов)|"
    r"агропром|сельхоз|пищев\w+\s+пром|строительн\w*\s+(форум|выставк|конферен)|"
    r"b2b|деловая\s+программ|деловое\s+мероприят|"
    r"крупногабаритн\w*\s+техник|уральских\s+заводов",
    re.I,
)
KUDAGO_LIFE = re.compile(
    r"концерт|театр|спектакл|вечеринк|детск|кинопоказ|квест|"
    r"стендап|standup|живопис|йога|танц|свадеб|фейерверк|караоке|"
    r"иммерсивн|музей|картин|скульптур|вокал|конн(ые|ая)\s+прогул|"
    r"рукодел|ёлочн|игруш|ювелир|дали|пикассо|монеты|философ|"
    r"футбол|культурн\w*\s+корпоратив|творческ\w*\s+досуг|"
    r"винтаж|пыльн\w*\s+жемчужин|пушист|дарвиновск|зоогеографи|"
    r"собак|кошек|beauty|галерея\s+красоты|гастроном|фестиваль|"
    r"индустри\w*\s+красоты|косметическ",
    re.I,
)
KUDAGO_SEARCH_QUERIES = [
    "конференция",
    "форум",
    "семинар",
    "нетворкинг",
    "бизнес",
    "выставка промышлен",
    "экспо",
]


def http_get_bytes(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_get_json(url: str) -> Any:
    data = http_get_bytes(url)
    return json.loads(data.decode("utf-8"))


def strip_html(t: str) -> str:
    t = unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def classify_type(title: str, description: str = "") -> str:
    blob = f"{title} {description}"
    patterns = [
        ("Конференция", r"конференци"),
        ("Форум", r"форум"),
        ("Семинар", r"семинар"),
        ("Вебинар", r"вебинар"),
        ("Митап", r"митап|meetup"),
        ("Нетворкинг", r"нетворкинг|networking"),
        ("Бизнес-завтрак", r"бизнес[-\s]?завтрак|деловой\s+завтрак"),
        ("Бизнес-ужин", r"бизнес[-\s]?ужин"),
        ("Выставка", r"выставк|экспо|expo|салон"),
        ("Мастер-класс", r"мастер[-\s]?класс"),
    ]
    for etype, pat in patterns:
        if re.search(pat, blob, re.I):
            return etype
    return "Мероприятие"


def kudago_pick_dates(dates: list[dict]) -> Optional[tuple[date, date]]:
    """Pick nearest upcoming finite date range."""
    best = None
    for d in dates or []:
        if d.get("is_endless") or d.get("is_startless"):
            continue
        sd = d.get("start_date")
        ed = d.get("end_date") or sd
        if not sd:
            # unix fallback
            start_ts = d.get("start")
            end_ts = d.get("end") or start_ts
            if not start_ts or start_ts < 0 or start_ts > 3_000_000_000:
                continue
            try:
                s = datetime.fromtimestamp(int(start_ts), tz=timezone.utc).date()
                e = datetime.fromtimestamp(int(end_ts), tz=timezone.utc).date()
            except Exception:
                continue
        else:
            try:
                s = date.fromisoformat(sd[:10])
                e = date.fromisoformat((ed or sd)[:10])
            except Exception:
                continue
        if e < TODAY:
            continue
        if best is None or s < best[0]:
            best = (s, e)
    return best


def kudago_is_b2b(title: str, description: str, categories: list) -> bool:
    cats = []
    for c in categories or []:
        if isinstance(c, dict):
            cats.append(c.get("slug") or "")
        else:
            cats.append(str(c))
    blob = f"{title} {description}"
    if any(c in {"concert", "theater", "party", "kids", "cinema", "quest", "holiday"} for c in cats):
        if not re.search(r"бизнес|конференц|форум|промышлен|B2B|b2b", blob, re.I):
            return False
    if "business-events" in cats:
        return not (KUDAGO_LIFE.search(title) and not KUDAGO_B2B.search(blob))
    if KUDAGO_LIFE.search(blob) and not re.search(
        r"промышлен|индустри|бизнес[-\s]?конферен|бизнес[-\s]?форум|B2B|b2b", blob, re.I
    ):
        return False
    return bool(KUDAGO_B2B.search(blob))


def kudago_event_to_row(ev: dict) -> Optional[dict]:
    title = strip_html(ev.get("title") or "")
    desc = strip_html(ev.get("description") or "")
    cats = ev.get("categories") or []
    if not kudago_is_b2b(title, desc, cats):
        return None
    rng = kudago_pick_dates(ev.get("dates") or [])
    if not rng:
        return None
    starts, ends = rng
    loc = ev.get("location")
    if isinstance(loc, dict):
        slug = loc.get("slug") or ""
    else:
        slug = str(loc or "")
    city = LOC_CITY.get(slug, "")
    place = ev.get("place")
    if not city and isinstance(place, dict):
        # place may have address / title only
        city = strip_html(place.get("title") or "")[:60]
    url = ev.get("site_url") or ""
    if not url and ev.get("id"):
        url = f"https://kudago.com/event/{ev['id']}/"
    etype = classify_type(title, desc)
    # force IT vertical hints for tech education
    extra = desc
    cat_slugs = " ".join(
        (c.get("slug") if isinstance(c, dict) else str(c)) for c in cats
    )
    if "business-events" in cat_slugs:
        extra += " бизнес"
    return make_event(
        title=title,
        starts=starts,
        ends=ends,
        url=url,
        source="kudago",
        city=city,
        country="Россия",
        etype=etype,
        extra_vert=extra,
        description=desc[:500],
    )


def fetch_kudago() -> list[dict]:
    now = int(time.time())
    by_id: dict[int, dict] = {}
    # 1) category sweeps
    for loc in LOCATIONS:
        for cat in ("business-events", "education", "exhibition"):
            page = 1
            while page <= 8:
                qs = urllib.parse.urlencode(
                    {
                        "location": loc,
                        "categories": cat,
                        "page_size": 100,
                        "page": page,
                        "actual_since": now,
                        "fields": "id,title,dates,place,description,site_url,location,categories,tags",
                        "expand": "place,dates",
                    }
                )
                url = f"https://kudago.com/public-api/v1.4/events/?{qs}"
                try:
                    d = http_get_json(url)
                except Exception as e:
                    print(f"  kudago {loc}/{cat} p{page}: {type(e).__name__}: {e}")
                    break
                results = d.get("results") or []
                if not results:
                    break
                for ev in results:
                    eid = ev.get("id")
                    if eid is not None:
                        by_id[eid] = ev
                if not d.get("next"):
                    break
                page += 1
                time.sleep(0.12)
            time.sleep(0.1)

    # 2) search API for B2B keywords (may return past; filter later)
    for q in KUDAGO_SEARCH_QUERIES:
        qs = urllib.parse.urlencode({"q": q, "ctype": "event", "page_size": 50})
        url = f"https://kudago.com/public-api/v1.4/search/?{qs}"
        try:
            d = http_get_json(url)
        except Exception as e:
            print(f"  kudago search {q!r}: {type(e).__name__}: {e}")
            continue
        for r in d.get("results") or []:
            eid = r.get("id")
            if eid is None or eid in by_id:
                continue
            # fetch full event for dates
            try:
                det = http_get_json(
                    f"https://kudago.com/public-api/v1.4/events/{eid}/?"
                    + urllib.parse.urlencode(
                        {
                            "fields": "id,title,dates,place,description,site_url,location,categories,tags",
                            "expand": "place,dates",
                        }
                    )
                )
                by_id[eid] = det
            except Exception as e:
                print(f"  kudago detail {eid}: {type(e).__name__}: {e}")
            time.sleep(0.12)
        time.sleep(0.15)

    out: list[dict] = []
    for ev in by_id.values():
        row = kudago_event_to_row(ev)
        if row:
            out.append(row)
    # internal dedupe
    seen = set()
    uniq = []
    for e in out:
        k = (fuzzy_title(e["title"]), e.get("starts_at"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def parse_exponet_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})$", s)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def exponet_topics_ok(topics: list[str]) -> bool:
    if not topics:
        return True
    joined = " | ".join(topics)
    # drop pure lifestyle/fairs without agri/industry/IT/business
    has_core = bool(
        re.search(
            r"Сельск|пищев|промышлен|Информацион|коммуникац|Бизнес|экономик|"
            r"Строитель|транспорт|Нефт|газ|мебел|муницип|здравоохран|"
            r"зоо|животн|лесн|лесо",
            joined,
            re.I,
        )
    )
    only_life = all(
        EXPONET_LIFESTYLE_ONLY.match(t.strip())
        or re.search(r"Ярмарки|народного потребления|Мода и стиль|Спорт|Культура", t, re.I)
        for t in topics
    )
    if only_life and not has_core:
        return False
    # garden fair with agri topic is ok (apk)
    return has_core or not only_life


def exponet_item_to_row(
    item: dict,
    default_country: str = "Россия",
    topic_hint: str = "",
) -> Optional[dict]:
    title = strip_tags(item.get("title") or "").strip()
    if not title:
        return None
    starts = parse_exponet_date(item.get("date") or "")
    ends = parse_exponet_date(item.get("date_end") or "") or starts
    if not starts or not ends:
        return None
    if ends < TODAY:
        return None
    topics = item.get("topics") or []
    if not exponet_topics_ok(topics):
        return None
    desc = strip_tags(item.get("description") or "")
    # skip pure consumer craft fairs unless agri/industry tagged
    if re.search(r"выставка-ярмарка", desc, re.I) and not re.search(
        r"Сельск|пищев|промышлен|Информацион|Бизнес|Строитель|транспорт|Нефт",
        " ".join(topics),
        re.I,
    ):
        return None
    city = (item.get("city_name") or "").strip()
    country = (item.get("country_name") or "").strip() or default_country
    link = (item.get("link") or "").strip()
    if link.startswith("http://"):
        link = "https://" + link[len("http://") :]
    if not link:
        # stable fallback so URL-dedupe still works per title
        slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", title).strip("-").lower()[:80]
        link = f"https://www.exponet.ru/exhibitions/?q={urllib.parse.quote(title[:80])}"
    extra = " ".join(topics) + " " + topic_hint + " " + desc
    # force vertical hints
    if topic_hint == "infotech" or re.search(r"Информацион|коммуникац", " ".join(topics), re.I):
        extra += " IT информационные технологии"
    if topic_hint in {"agriculture", "pets"} or re.search(
        r"Сельск|пищев|зоо|животн", " ".join(topics), re.I
    ):
        extra += " агро сельское хозяйство"
    return make_event(
        title=title,
        starts=starts,
        ends=ends,
        url=link,
        source="exponet",
        city=city,
        country=country,
        etype="Выставка",
        extra_vert=extra,
        description=(desc or "; ".join(topics))[:500],
    )


def parse_exponet_xml(data: bytes) -> list[dict]:
    # windows-1251 default for RU feed
    text = None
    for enc in ("cp1251", "utf-8", "windows-1252"):
        try:
            text = data.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = data.decode("cp1251", errors="replace")
    # ElementTree needs unicode; encoding decl may say windows-1251
    text = re.sub(
        r'encoding\s*=\s*["\']windows-1251["\']',
        'encoding="utf-8"',
        text,
        count=1,
        flags=re.I,
    )
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        print(f"  XML parse error: {e}")
        return []
    items = []
    for node in root.findall("item"):
        topics = [t.text.strip() for t in node.findall("topic_id") if t.text]
        row = {
            "title": (node.findtext("title") or ""),
            "description": (node.findtext("description") or ""),
            "date": (node.findtext("date") or ""),
            "date_end": (node.findtext("date_end") or ""),
            "link": (node.findtext("link") or ""),
            "city_name": (node.findtext("city_name") or ""),
            "city_id": (node.findtext("city_id") or ""),
            "country_name": (node.findtext("country_name") or ""),
            "country_id": (node.findtext("country_id") or ""),
            "topics": topics,
            "type": (node.findtext("type") or ""),
        }
        items.append(row)
    return items


def fetch_exponet_url(params: dict) -> list[dict]:
    qs = urllib.parse.urlencode(params)
    url = f"https://www.exponet.ru/content/xml/exhibitions.ru.xml?{qs}"
    try:
        raw = http_get_bytes(url)
    except Exception as e:
        print(f"  exponet fetch {params}: {type(e).__name__}: {e}")
        return []
    return parse_exponet_xml(raw)


def fetch_exponet() -> list[dict]:
    collected: list[dict] = []  # raw item dicts with meta
    seen_raw = set()

    def add_raw(items: list[dict], country: str, topic_hint: str = ""):
        for it in items:
            key = (
                fuzzy_title(it.get("title") or ""),
                it.get("date") or "",
                (it.get("link") or "").rstrip("/").lower(),
            )
            if key in seen_raw:
                continue
            seen_raw.add(key)
            it = dict(it)
            it["_country"] = country
            it["_topic_hint"] = topic_hint
            collected.append(it)

    # Topic × Russia (max 50 each)
    for topic in EXPONET_TOPICS:
        items = fetch_exponet_url(
            {
                "topic": topic,
                "country": "rus",
                "max": 50,
                "maxperiod": 800,
                "showcity": "yes",
                "showcountry": "yes",
            }
        )
        print(f"  exponet topic={topic} country=rus → {len(items)}")
        add_raw(items, "Россия", topic)
        time.sleep(0.2)

    # CIS countries (broad, filter client-side)
    for code, cname in CIS_COUNTRIES.items():
        if code == "rus":
            continue
        items = fetch_exponet_url(
            {
                "country": code,
                "max": 50,
                "maxperiod": 800,
                "showcity": "yes",
                "showcountry": "yes",
            }
        )
        print(f"  exponet country={code} → {len(items)}")
        add_raw(items, cname, "")
        time.sleep(0.2)

    # Extra: topic without country (intl) for key verticals
    for topic in ("agriculture", "infotech", "promexpo", "oil", "building"):
        items = fetch_exponet_url(
            {
                "topic": topic,
                "max": 50,
                "maxperiod": 800,
                "showcity": "yes",
                "showcountry": "yes",
            }
        )
        print(f"  exponet topic={topic} (all countries) → {len(items)}")
        for it in items:
            # infer country from city if known CIS else leave Россия/as-is
            cname = it.get("country_name") or ""
            if not cname:
                city = (it.get("city_name") or "").lower()
                if any(x in city for x in ("минск",)):
                    cname = "Беларусь"
                elif any(x in city for x in ("алмат", "астан", "атырау", "актау")):
                    cname = "Казахстан"
                elif any(x in city for x in ("ташкент", "самарканд")):
                    cname = "Узбекистан"
                elif any(x in city for x in ("баку",)):
                    cname = "Азербайджан"
                elif any(x in city for x in ("тбилис",)):
                    cname = "Грузия"
                elif any(x in city for x in ("бишкек",)):
                    cname = "Кыргызстан"
                else:
                    cname = "Россия"
            add_raw([it], cname, topic)
        time.sleep(0.2)

    out: list[dict] = []
    for it in collected:
        row = exponet_item_to_row(
            it,
            default_country=it.get("_country") or "Россия",
            topic_hint=it.get("_topic_hint") or "",
        )
        if row:
            out.append(row)
    # internal dedupe
    seen = set()
    uniq = []
    for e in out:
        k = (fuzzy_title(e["title"]), e.get("starts_at"), (e.get("organizer_url") or "").rstrip("/").lower())
        k2 = (fuzzy_title(e["title"]), e.get("starts_at"))
        if k in seen or k2 in seen:
            continue
        seen.add(k)
        seen.add(k2)
        uniq.append(e)
    return uniq


def main() -> None:
    print("=== KudaGo + Exponet ingest ===")
    print(f"TODAY={TODAY.isoformat()} MSK")

    print("\n[1/4] Fetch KudaGo…")
    kudago_events = fetch_kudago()
    print(f"  kudago candidates (B2B filtered): {len(kudago_events)}")

    print("\n[2/4] Fetch Exponet XML…")
    exponet_events = fetch_exponet()
    print(f"  exponet candidates: {len(exponet_events)}")

    print("\n[3/4] Reload catalog + merge…")
    existing = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    before = len(existing)
    src_before = Counter(e.get("source") for e in existing)
    print(f"  catalog before merge: {before}")

    from ingest_round3 import dedupe_key as dk

    pre_keys = {dk(e) for e in existing}
    pre_soft = {(fuzzy_title(e["title"]), e.get("starts_at")) for e in existing}

    def is_new(e: dict) -> bool:
        if not e:
            return False
        return dk(e) not in pre_keys and (fuzzy_title(e["title"]), e.get("starts_at")) not in pre_soft

    added_k_list = [e for e in kudago_events if is_new(e)]
    added_e_list = [e for e in exponet_events if is_new(e)]

    existing = merge(existing, kudago_events, "kudago")
    after_k = len(existing)
    existing = merge(existing, exponet_events, "exponet")
    after_e = len(existing)
    added_kudago = after_k - before
    added_exponet = after_e - after_k
    print(f"  added kudago={added_kudago} exponet={added_exponet}")
    print(f"  catalog after merge: {after_e} (was {before})")
    print(f"  prefilter new lists: kudago={len(added_k_list)} exponet={len(added_e_list)}")

    print("\n[4/4] Write catalog + JS…")
    meta = write_catalog(existing)
    print(f"  EVENTS_META: {meta}")

    file_events = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    file_k = [e for e in file_events if e.get("source") == "kudago"]
    file_e = [e for e in file_events if e.get("source") == "exponet"]

    def vert_counts(lst: list[dict]) -> dict:
        return dict(Counter(e.get("vertical") or "?" for e in lst))

    # Prefer lists that match merge deltas; fall back to file sources
    sample_k = added_k_list[:5] if added_k_list else file_k[:5]
    sample_e = added_e_list[:5] if added_e_list else file_e[:5]

    lines = [
        "KudaGo + Exponet ingest report",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')} MSK",
        f"Catalog before: {before}",
        f"Catalog after:  {meta.get('count')} (window.EVENTS_META.count)",
        f"Sources before: kudago={src_before.get('kudago', 0)} exponet={src_before.get('exponet', 0)}",
        "",
        "=== Added by source ===",
        f"kudago:  {added_kudago}  (source=kudago in file: {len(file_k)})",
        f"exponet: {added_exponet}  (source=exponet in file: {len(file_e)})",
        f"TOTAL net new: {added_kudago + added_exponet}",
        "",
        "=== Added / file by vertical ===",
        f"kudago net-new verticals: {vert_counts(added_k_list)}",
        f"exponet net-new verticals: {vert_counts(added_e_list)}",
        f"kudago in-file verticals: {vert_counts(file_k)}",
        f"exponet in-file verticals: {vert_counts(file_e)}",
        "",
        "=== Sample titles: kudago (5) ===",
    ]
    for e in sample_k:
        lines.append(
            f"  - [{e.get('vertical')}] {e.get('title')} | {e.get('starts_at')} | {e.get('city')}"
        )
    if not sample_k:
        lines.append("  (none)")
    lines.append("")
    lines.append("=== Sample titles: exponet (5) ===")
    for e in sample_e:
        lines.append(
            f"  - [{e.get('vertical')}] {e.get('title')} | {e.get('starts_at')} | "
            f"{e.get('city')}, {e.get('country')}"
        )
    if not sample_e:
        lines.append("  (none)")
    lines += [
        "",
        "Notes:",
        "- KudaGo: business-events + education/exhibition with B2B keyword gate; "
        "search API for конференция/форум/семинар/…; lifestyle excluded.",
        "- Exponet: public XML exhibitions.ru.xml; topics agriculture/infotech/promexpo/… "
        "+ CIS countries; CP1251; source=exponet.",
        "- Dedup: organizer_url+title and title+starts_at vs full catalog (incl. exponet_future).",
        "- TimePad not used. No Apify/Parse.",
        "",
    ]
    report = "\n".join(lines)
    out_path = SAMPLES / "ingest_kudago_exponet.txt"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
