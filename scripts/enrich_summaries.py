#!/usr/bin/env python3
"""Enrich event summaries for drawer (description ↔ summary).

Quality gate: ≥120 chars, ≥2 sentences, not ≈ title, reject lone
«Международная/межрегиональная выставка» templates.

Sources: «О выставке»/«О мероприятии» <p> (2–4) → JSON-LD Event.description → og/meta;
organizer_url detail pages; Expomap slug/search resolve for generic roots.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from pathlib import Path
from threading import Lock
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent if (_HERE.parent / "events_upcoming.json").exists() else _HERE
JSON_PATH = ROOT / "events_upcoming.json"
JS_PATH = ROOT / "events-data.js"
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CIS_COUNTRIES = {
    "Россия", "Беларусь", "Казахстан", "Узбекистан",
    "Армения", "Азербайджан", "Кыргызстан", "Киргизия",
    "Молдова", "Молдавия", "Таджикистан", "Туркменистан", "Туркмения",
}

GENERIC_URLS = {
    "https://expomap.ru",
    "https://www.expomap.ru",
    "http://expomap.ru",
    "https://expomap.ru/conference",
    "https://www.expomap.ru/conference",
    "https://foodsmi.com/events",
    "https://www.foodsmi.com/events",
    "http://foodsmi.com/events",
}

# Listing hubs / category roots that never carry a single-event description.
LISTING_HUB_RE = re.compile(
    r"^https?://(www\.)?("
    r"foodsmi\.com/events|"
    r"expomap\.ru(/conference)?|"
    r"expocalendar\.ru|"
    r"kudabiz\.com|"
    r"totalexpo\.ru/?$"
    r")/?$",
    re.I,
)

# Known event titles → detail pages (when catalog only has a listing hub).
KNOWN_TITLE_URLS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"русская\s*рыба", re.I), "https://totalexpo.ru/expo/12590.aspx"),
    (re.compile(r"growbox\s*market|гроубокс\s*маркет", re.I), "https://totalexpo.ru/expo/11093.aspx"),
    (re.compile(r"content\s*expo", re.I), "https://totalexpo.ru/expo/9079.aspx"),
    (re.compile(r"txca|texcare\s*asia|china\s*laundry", re.I), "https://totalexpo.ru/expo/8810.aspx"),
    (re.compile(r"wire\s*china", re.I), "https://totalexpo.ru/expo/5582.aspx"),
]

NAV_NOISE = re.compile(
    r"cookie|enable javascript|подпишитесь|все права|copyright|войти|"
    r"личный кабинет|политика конфиденциальности|пользовательск|"
    r"^меню$|^главная$|^контакты$",
    re.I,
)
LISTING_JUNK_RE = re.compile(
    r"если вы хотите посетить данную выставку|"
    r"заполните, пожалуйста, следующую форму|"
    r"информация о международных, национальных, региональных|"
    r"поиск по выставкам и деловым мероприятиям|"
    r"любое использование материалов допускается|"
    r"редакция не несет ответственности|"
    r"в соответствии с вашими пожеланиями, мы орган|"
    r"#rec\d+|"
    r"\.t-btnflex|"
    r"пресс-релиз|"
    r"подпишитесь на новости|"
    r"подписавшись на новости",
    re.I,
)
STALE_YEAR_RE = re.compile(
    r"(выставка|форум|конференция)\s+.{0,80}20(23|24|25)\s+проводится|"
    r"\b20(23|24|25)\s*проводится\s+c\s|"
    r"\d{2}\.\d{2}\.20(23|24|25)\s*//",
    re.I,
)
# Brand in text → title must mention the same brand, otherwise this is a leaked listing blurb.
HUB_BRANDS: list[tuple[re.Pattern[str], re.Pattern[str]]] = [
    (re.compile(r"агравия", re.I), re.compile(r"агравия|agravia", re.I)),
    (re.compile(r"pulpfor", re.I), re.compile(r"pulpfor|пульпфор", re.I)),
    (re.compile(r"финатлон", re.I), re.compile(r"финатлон", re.I)),
    (re.compile(r"tech\s*week", re.I), re.compile(r"tech\s*week", re.I)),
]
MULTI_EVENT_LIST_RE = re.compile(
    r"международн\w+\s+(выставка|форум|конференция)",
    re.I,
)
TEMPLATE_ONLY = re.compile(
    r"^(межрегиональная|международная|сельскохозяйственная|специализированная)\s+"
    r"выставка\.?$",
    re.I,
)

_write_lock = Lock()
_cache: dict[str, str] = {}


def _norm(s: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", (s or "").lower()).strip()


def sentence_count(text: str) -> int:
    parts = re.split(r"(?<=[.!?…])\s+", (text or "").strip())
    return len([p for p in parts if len(p.strip()) >= 20])


def ends_with_ellipsis(text: str) -> bool:
    s = (text or "").rstrip()
    return s.endswith("...") or s.endswith("…")


def is_complete_sentence_end(text: str) -> bool:
    s = (text or "").rstrip()
    if not s:
        return False
    return s[-1] in ".!?»\"')]"


def clip_summary(text: str, limit: int = 800) -> str:
    """Hard length cap that prefers a sentence boundary over mid-word/mid-sentence cuts."""
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    best = -1
    for punct in (". ", "! ", "? ", ".»", '!"', '?"', "… "):
        i = cut.rfind(punct)
        if i > best:
            best = i
    # also lone terminal punct at end of cut
    if cut.rstrip() and cut.rstrip()[-1] in ".!?…" and len(cut.rstrip()) > best:
        candidate = cut.rstrip()
        if len(candidate) >= 120:
            return candidate
    if best >= 120:
        return cut[: best + 1].strip()
    return cut.rstrip()


def is_real_summary(text: Optional[str], title: Optional[str] = None) -> bool:
    s = clean_description(text or "")
    if not is_usable_description(s, title):
        return False
    if len(s) < 120:
        return False
    if s.count("//") >= 2:
        return False
    low = s.lower()
    junk = (
        "услуга оказывается на платной",
        "стоимость 1 регистрации",
        "шенгенской визы",
        "визовом центре",
        "отпечатки всех 10 пальцев",
        "javascript required",
        "Ïîä",
        "âåò",
    )
    if any(j in low or j in s for j in junk):
        return False
    if sentence_count(s) < 2:
        return False
    return True


def _clean(text: str) -> str:
    return clean_description(text)


def clean_description(text: str) -> str:
    """Strip HTML/entities and collapse whitespace. Safe for list + drawer."""
    t = text or ""
    t = re.sub(r"<br\s*/?>", " ", t, flags=re.I)
    t = re.sub(r"</p\s*>", " ", t, flags=re.I)
    t = re.sub(r"<p[^>]*>", " ", t, flags=re.I)
    t = unescape(re.sub(r"<[^>]+>", " ", t))
    t = t.replace("\xa0", " ").replace("\u200b", "").replace("\r", " ")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    t = re.sub(
        r"(?:,\s*){0,3}программа,\s*спикеры,\s*стоимость участия\.?\s*"
        r"подробности на all-events\.ru!?\s*$",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"\s*подробности на all-events\.ru!?\s*$", "", t, flags=re.I)
    t = re.sub(
        r"\s*даты проведения:\s*\d{4}-\d{2}-\d{2}"
        r"(?:\s*[–-]\s*\d{4}-\d{2}-\d{2})?\.\s*"
        r"место проведения:\s*[^.]+."
        r"(?:\s*тип мероприятия:\s*[^.]+.)?\s*$",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"(?:,\s*){2,}$", "", t).strip()
    t = re.split(r"с перечнем экспонатов и разделов", t, maxsplit=1, flags=re.I)[0]
    t = re.split(r"стоимость и условия участия обычно", t, maxsplit=1, flags=re.I)[0]
    t = re.split(r"деловая программа обычно публикуется", t, maxsplit=1, flags=re.I)[0]
    t = re.split(r"программа форума охватывает", t, maxsplit=1, flags=re.I)[0]
    t = t.rstrip(" :,;—–-").strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _title_tokens(s: str) -> set[str]:
    words = re.findall(r"[a-zа-яё0-9]{4,}", _norm(s))
    stop = {
        "мероприятие", "выставка", "форум", "конференция", "семинар",
        "конгресс", "вебинар", "встреча", "митап", "онлайн", "россия",
        "международная", "международный", "специализированная",
        "пройдет", "пройдёт", "состоится",
    }
    return {w for w in words if w not in stop and not w.isdigit()}


def belongs_to_title(title: str, desc: str) -> bool:
    """False when the blurb is clearly about another named event or a listing hub."""
    d = clean_description(desc)
    if not d:
        return False
    if LISTING_JUNK_RE.search(d) or STALE_YEAR_RE.search(d):
        return False
    if len(MULTI_EVENT_LIST_RE.findall(d)) >= 3:
        return False
    for brand, title_ok in HUB_BRANDS:
        if brand.search(d) and not title_ok.search(title or ""):
            return False
    m = re.search(r"«([^»]{8,80})»", d[:240])
    if m:
        quoted = m.group(1)
        looks_event = bool(re.search(
            r"выставк|форум|конференц|конгресс|салон|экспо|недел|20\d{2}",
            quoted,
            re.I,
        ))
        if looks_event:
            qt, tt = _norm(quoted), _norm(title)
            if qt and tt and qt not in tt and tt not in qt:
                qtok, ttok = _title_tokens(quoted), _title_tokens(title)
                if qtok and ttok and not (qtok & ttok):
                    return False
    m2 = re.search(
        r"(?:форум|выставка|конференция)\s+по\s+([а-яёa-z0-9\-]{4,40})",
        d[:200],
        re.I,
    )
    if m2:
        topic_toks = _title_tokens(m2.group(1))
        if topic_toks and not (topic_toks & _title_tokens(title)):
            return False
    return True


def is_usable_description(text: Optional[str], title: Optional[str] = None) -> bool:
    """Display gate: cleaned, on-topic, not a listing leak. One solid paragraph is enough."""
    s = clean_description(text or "")
    if len(s) < 50:
        return False
    if ends_with_ellipsis(s):
        return False
    if not belongs_to_title(title or "", s):
        return False
    if _is_noise_summary(s):
        return False
    if s.count("�") >= 2 or "Ð" in s:
        return False
    nt, ns = _norm(title or ""), _norm(s)
    if nt and (ns == nt or (ns.startswith(nt) and len(ns) < len(nt) + 30)):
        return False
    if TEMPLATE_ONLY.match(s):
        return False
    return True


def fetch_html(url: str, timeout: int = 25) -> tuple[str, str]:
    if not url or not url.startswith("http"):
        raise ValueError("bad url")
    if url in _cache:
        return _cache[url], url
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        final = r.geturl()
        enc = r.headers.get_content_charset() or "utf-8"
    html = None
    candidates = []
    for e in (enc, "utf-8", "cp1251", "windows-1251", "latin-1"):
        try:
            candidates.append(raw.decode(e))
        except Exception:
            continue
    if not candidates:
        html = raw.decode("utf-8", errors="replace")
    else:
        # prefer decode with Cyrillic letters and few replacement chars
        def score(t: str) -> tuple:
            cyr = len(re.findall(r"[А-Яа-яЁё]", t[:5000]))
            bad = t.count("�") + t.count("Ð") + t.count("Ï")
            return (cyr - 5 * bad, -bad)
        html = max(candidates, key=score)
    _cache[url] = html
    _cache[final] = html
    return html, final


def extract_jsonld_event_description(html: str) -> str:
    best = ""
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.S | re.I,
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack: list[Any] = data if isinstance(data, list) else [data]
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
            d = _clean(it.get("description") or "")
            if len(d) > len(best):
                best = d
    return best


def _is_noise_summary(text: str) -> bool:
    t = clean_description(text or "")
    if not t:
        return True
    if NAV_NOISE.search(t):
        return True
    if LISTING_JUNK_RE.search(t):
        return True
    if STALE_YEAR_RE.search(t):
        return True
    if t.count("//") >= 2 or t.lower().count("пресс-релиз") >= 2:
        return True
    if len(re.findall(r"\d{2}\.\d{2}\.\d{4}\s*//", t)) >= 2:
        return True
    if "#rec" in t or ".t-btnflex" in t or "{color:" in t:
        return True
    return False


def extract_totalexpo_description(html: str) -> str:
    """TotalExpo body blurb lives in min-height:130px (full text; og/meta often truncated)."""
    m = re.search(r"min-height:\s*130px[^>]*>(.*?)</div>", html, re.S | re.I)
    if not m:
        return ""
    chunk = m.group(1)
    paras: list[str] = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", chunk, re.S | re.I):
        t = _clean(p)
        if len(t) < 40 or _is_noise_summary(t):
            continue
        if re.match(r"^\*\s*мероприятие может быть отменено", t, re.I):
            continue
        paras.append(t)
        if len(paras) >= 4:
            break
    if not paras:
        # fall back to cleaned chunk (may include thematic list items)
        t = _clean(chunk)
        if len(t) >= 80 and not _is_noise_summary(t):
            return clip_summary(t)
        return ""
    text = " ".join(paras)
    if _is_noise_summary(text):
        return ""
    return text


def _find_about_section_start(html: str) -> int:
    """Earliest heading-like «О выставке»/«О мероприятии» (not mid-word like «по выставке»)."""
    low = html.lower()
    markers = (
        "о выставке",
        "о мероприятии",
        "о форуме",
        "о конференции",
        "о конгрессе",
        "описание выставки",
        "о проекте",
        "about the exhibition",
        "о семинаре",
    )
    best = -1
    for mk in markers:
        # Prefer explicit headings: >О мероприятии</h2> etc.
        for m in re.finditer(
            r"(?:<h[1-6][^>]*>\s*|<[^>]+>)\s*" + re.escape(mk) + r"\s*<",
            low,
        ):
            i = m.start()
            # snap to marker text itself
            j = low.find(mk, i)
            if j < 0:
                j = i
            if best < 0 or j < best:
                best = j
        # Word-boundary plain matches (avoid «по выставке»)
        for m in re.finditer(r"(?<![a-zа-яё])" + re.escape(mk) + r"(?![a-zа-яё])", low):
            i = m.start()
            if best < 0 or i < best:
                # skip mid-sentence false positives without heading nearby
                window = low[max(0, i - 80) : i + len(mk) + 20]
                if re.search(r"<h[1-6]\b", window) or ">о " in window or mk in (
                    "about the exhibition",
                    "описание выставки",
                ):
                    best = i if best < 0 else min(best, i)
                elif best < 0:
                    # keep as weak fallback only if nothing else
                    pass
    if best >= 0:
        return best
    # weak fallback: first word-boundary marker
    for mk in markers:
        m = re.search(r"(?<![a-zа-яё])" + re.escape(mk) + r"(?![a-zа-яё])", low)
        if m:
            return m.start()
    return -1


def extract_about_paragraphs(html: str) -> str:
    """Prefer explicit «О выставке»/«О мероприятии» block (2–4 paragraphs)."""
    # Site-specific full body (TotalExpo) before generic marker scan
    te = extract_totalexpo_description(html)
    if te and not ends_with_ellipsis(te):
        return te

    start = _find_about_section_start(html)
    if start < 0:
        return ""
    chunk = html[start : start + 12000]
    paras: list[str] = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", chunk, re.S | re.I):
        t = _clean(p)
        if len(t) < 40 or _is_noise_summary(t):
            continue
        if re.match(r"^(когда|где|организатор|сайт|телефон)\b", t, re.I):
            continue
        paras.append(t)
        if len(paras) >= 4:
            break
    text = " ".join(paras)
    if _is_noise_summary(text) or ends_with_ellipsis(text):
        return ""
    return text


def extract_og_or_meta(html: str) -> str:
    for pat in (
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
    ):
        m = re.search(pat, html, re.I)
        if m:
            t = _clean(m.group(1))
            if len(t) >= 40:
                return t
    return ""


def _trim_incomplete(text: str) -> str:
    """If text does not end as a sentence, cut at the last sentence boundary."""
    s = (text or "").strip()
    if not s or is_complete_sentence_end(s) or ends_with_ellipsis(s):
        return s
    best = -1
    for punct in (". ", "! ", "? ", ".»"):
        i = s.rfind(punct)
        if i > best:
            best = i
    if best >= 120:
        return s[: best + 1].strip()
    return s


def extract_summary_from_html(html: str) -> str:
    # Priority: О выставке / TotalExpo body → JSON-LD → og/meta
    ranked = [
        extract_about_paragraphs(html),
        extract_jsonld_event_description(html),
        extract_og_or_meta(html),
    ]
    cleaned = []
    for cand in ranked:
        cand = (cand or "").strip()
        if not cand or _is_noise_summary(cand):
            continue
        if ends_with_ellipsis(cand):
            # try to salvage by trimming before ellipsis if a prior sentence exists
            salvage = cand.rstrip(".").rstrip("…").rstrip(".")
            # drop trailing partial clause after last sentence
            cand = _trim_incomplete(salvage + ".")
            if ends_with_ellipsis(cand) or len(cand) < 80:
                continue
        else:
            cand = _trim_incomplete(cand)
        if not cand or _is_noise_summary(cand):
            continue
        cleaned.append(cand)
        if is_real_summary(cand, ""):
            return clip_summary(cand)
    if not cleaned:
        return ""
    # Prefer longest candidate that ends as a complete sentence
    complete = [c for c in cleaned if is_complete_sentence_end(c)]
    pool = complete or cleaned
    pool.sort(key=len, reverse=True)
    return clip_summary(pool[0])


def slugify_ru(title: str) -> str:
    tr = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    s = (title or "").lower()
    s = re.sub(r"\b20\d{2}\b", "", s)
    out = []
    for ch in s:
        if ch in tr:
            out.append(tr[ch])
        elif "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        else:
            out.append("-")
    return re.sub(r"-+", "-", "".join(out)).strip("-")[:80]


def is_generic_url(url: str) -> bool:
    u = (url or "").strip().rstrip("/")
    if not u:
        return True
    if u in {g.rstrip("/") for g in GENERIC_URLS}:
        return True
    return bool(LISTING_HUB_RE.match((url or "").strip().rstrip("/")))


def is_listing_hub(url: str) -> bool:
    """True for organizer_url that is only a multi-event listing / bare root."""
    u = (url or "").strip()
    if not u:
        return True
    if is_generic_url(u):
        return True
    # foodsmi.com without a concrete event slug
    if re.match(r"^https?://(www\.)?foodsmi\.com/?(events)?/?$", u, re.I):
        return True
    # bare expomap / conference roots already covered; also theme/country listings
    if re.search(r"expomap\.ru/(expo/)?(theme|country|city|page)/", u, re.I):
        return True
    return False


def normalize_title_key(title: str) -> str:
    """Normalize for donor matching: drop quotes/year, collapse punctuation."""
    t = (title or "").lower().replace("ё", "е")
    t = t.replace("«", " ").replace("»", " ").replace('"', " ").replace("'", " ")
    t = re.sub(r"\b20\d{2}\b", " ", t)
    t = re.sub(r"[^a-zа-я0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def resolve_known_url(title: str) -> Optional[str]:
    t = title or ""
    for pat, url in KNOWN_TITLE_URLS:
        if pat.search(t):
            return url
    return None


def find_donor_summary(
    event: dict,
    catalog: list[dict],
    *,
    min_len: int = 120,
) -> Optional[str]:
    """Copy a real description from a same/similar-title sibling in the catalog."""
    key = normalize_title_key(event.get("title") or "")
    if not key or len(key) < 4:
        return None
    self_title = (event.get("title") or "").strip()
    self_start = event.get("starts_at") or ""
    best = ""
    for other in catalog:
        ot = (other.get("title") or "").strip()
        if ot == self_title and (other.get("starts_at") or "") == self_start:
            # same row
            if (other.get("organizer_url") or "") == (event.get("organizer_url") or ""):
                continue
        desc = (other.get("description") or "").strip()
        if not is_real_summary(desc, ot):
            continue
        if len(desc) < min_len:
            continue
        ok = normalize_title_key(ot)
        if not ok:
            continue
        if ok == key and belongs_to_title(self_title, desc):
            if len(desc) > len(best):
                best = desc
    return best or None


def resolve_expomap_url(title: str) -> Optional[str]:
    base_title = re.sub(r"\s*20\d{2}\s*", " ", title or "").strip()
    slug = slugify_ru(base_title)
    candidates: list[str] = []
    if slug and len(slug) >= 3:
        candidates.append(f"https://expomap.ru/expo/{slug}/")
        alt = slugify_ru(re.sub(r"\([^)]*\)", "", base_title))
        if alt and alt != slug:
            candidates.append(f"https://expomap.ru/expo/{alt}/")
    q = urllib.parse.quote(base_title[:80])
    candidates.append(f"https://expomap.ru/search/?q={q}")

    for url in candidates:
        try:
            html, final = fetch_html(url, timeout=20)
        except Exception:
            continue
        if re.search(r"/expo/[a-z0-9][a-z0-9-]{2,}/?$", final, re.I):
            if extract_jsonld_event_description(html) or "о выставке" in html.lower():
                return final
        links = re.findall(
            r'href="((?:https://expomap\.ru)?/expo/[a-z0-9][a-z0-9-]{2,}/?)"',
            html,
            re.I,
        )
        for href in links:
            if any(x in href for x in ("/theme/", "/city/", "/country/", "/page/")):
                continue
            full = href if href.startswith("http") else "https://expomap.ru" + href
            return full
    return None


def polish_summary(text: str, event: dict) -> str:
    s = clean_description(text or "")
    if not s:
        return ""
    if is_real_summary(s, event.get("title")):
        return clip_summary(s)
    city = (event.get("city") or "").strip()
    country = (event.get("country") or "").strip()
    starts = (event.get("starts_at") or "").strip()
    ends = (event.get("ends_at") or "").strip()
    etype = (event.get("type") or "").strip()
    extras: list[str] = []
    if starts:
        span = starts if not ends or ends == starts else f"{starts}–{ends}"
        extras.append(f"Даты проведения: {span}.")
    where = ", ".join(x for x in (city, country) if x)
    if where:
        extras.append(f"Место проведения: {where}.")
    if etype and etype.lower() not in s.lower():
        extras.append(f"Тип мероприятия: {etype}.")
    if not extras:
        return s
    combined = s if s.endswith((".", "!", "?", "…")) else s + "."
    combined = (combined + " " + " ".join(extras)).strip()
    return clip_summary(combined)


def fetch_summary_for_event(
    event: dict,
    catalog: Optional[list[dict]] = None,
) -> Optional[str]:
    title = event.get("title") or ""
    url = (event.get("organizer_url") or event.get("url") or "").strip()
    tried: set[str] = set()

    def try_url(u: str) -> Optional[str]:
        u = (u or "").strip()
        if not u or u in tried or is_listing_hub(u):
            return None
        tried.add(u)
        try:
            html, _final = fetch_html(u)
        except Exception:
            return None
        raw = extract_summary_from_html(html)
        if not raw or ends_with_ellipsis(raw):
            return None
        polished = polish_summary(raw, event)
        if ends_with_ellipsis(polished):
            return None
        prev = (event.get("description") or "").strip()
        if prev and len(polished) < len(prev) and not is_complete_sentence_end(polished):
            return None
        if is_real_summary(polished, title):
            return polished
        return None

    # 1) Direct detail URL (skip listing hubs)
    if url and not is_listing_hub(url):
        got = try_url(url)
        if got:
            return got

    # 2) Known title → better detail page (e.g. Русская рыба → totalexpo)
    known = resolve_known_url(title)
    if known:
        got = try_url(known)
        if got:
            return got

    # 3) Expomap resolve for apk / expomap sources / generic hubs
    src = (event.get("source") or "").lower()
    vert = event.get("vertical") or ""
    if (
        "expomap" in src
        or "expomap" in url.lower()
        or vert == "apk"
        or is_listing_hub(url)
    ):
        resolved = resolve_expomap_url(title)
        if resolved:
            got = try_url(resolved)
            if got:
                return got

    # 4) Donor from catalog (same/similar title with real description)
    if catalog is not None:
        donor = find_donor_summary(event, catalog)
        if donor and is_real_summary(donor, title):
            return clip_summary(donor)
        # also try fetching a sibling's detail URL
        key = normalize_title_key(title)
        if key:
            for other in catalog:
                if normalize_title_key(other.get("title") or "") != key:
                    continue
                ou = (other.get("organizer_url") or "").strip()
                if ou and not is_listing_hub(ou):
                    got = try_url(ou)
                    if got:
                        return got

    return None


def load_json_events() -> list[dict]:
    if not JSON_PATH.exists():
        return []
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def load_js_events() -> tuple[list[dict], dict]:
    text = JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"window\.EVENTS\s*=\s*(\[.*\]);\s*window\.EVENTS_META\s*=\s*(\{.*?\});",
        text,
        re.S,
    )
    if not m:
        m2 = re.search(r"window\.EVENTS\s*=\s*(\[.*\]);", text, re.S)
        if not m2:
            return [], {}
        return json.loads(m2.group(1)), {}
    return json.loads(m.group(1)), json.loads(m.group(2))


def write_catalog(events: list[dict]) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    events = [e for e in events if (e.get("ends_at") or "") >= today]
    events.sort(key=lambda e: (e.get("starts_at") or "", e.get("title") or ""))
    JSON_PATH.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
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
            "summary": clean_description(e.get("description") or ""),
            "source": e.get("source") or "",
        })
    meta = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(js_events),
    }
    js = (
        "window.EVENTS = " + json.dumps(js_events, ensure_ascii=False) + ";\n"
        + "window.EVENTS_META = " + json.dumps(meta, ensure_ascii=False) + ";\n"
    )
    JS_PATH.write_text(js, encoding="utf-8")
    return meta


def find_json_index(events: list[dict], js_event: dict) -> Optional[int]:
    title = (js_event.get("title") or "").strip()
    date = js_event.get("date") or ""
    for i, e in enumerate(events):
        if (e.get("title") or "").strip() == title and (e.get("starts_at") or "") == date:
            return i
    for i, e in enumerate(events):
        if (e.get("title") or "").strip() == title:
            return i
    return None


def enrich_by_id(event_id: Any) -> dict:
    try:
        eid = int(event_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad_id"}

    js_events, _meta = load_js_events()
    target = next((e for e in js_events if e.get("id") == eid), None)
    if not target:
        return {"ok": False, "error": "not_found"}

    title = target.get("title")
    existing = target.get("summary") or ""
    if is_real_summary(existing, title):
        return {"ok": True, "summary": existing, "cached": True}

    events = load_json_events()
    idx = find_json_index(events, target)
    base = (
        events[idx]
        if idx is not None
        else {
            "title": title,
            "organizer_url": target.get("url"),
            "city": target.get("city"),
            "country": target.get("country"),
            "starts_at": target.get("date"),
            "ends_at": target.get("endDate"),
            "type": target.get("type"),
            "vertical": target.get("vertical"),
            "source": target.get("source"),
            "description": existing,
        }
    )

    # Pass catalog so listing-hub / fetch_failed paths can donor-copy or reuse sibling URLs
    summary = fetch_summary_for_event(base, catalog=events)
    if not summary or not is_real_summary(summary, title):
        donor = find_donor_summary(base, events)
        if donor and is_real_summary(donor, title):
            summary = clip_summary(donor)
        else:
            return {"ok": False, "summary": "", "error": "fetch_failed"}

    with _write_lock:
        events = load_json_events()
        idx = find_json_index(events, target)
        if idx is not None:
            events[idx]["description"] = summary
            write_catalog(events)
        else:
            js_events, meta = load_js_events()
            for e in js_events:
                if e.get("id") == eid:
                    e["summary"] = summary
                    break
            meta = {
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "count": len(js_events),
            }
            js = (
                "window.EVENTS = " + json.dumps(js_events, ensure_ascii=False) + ";\n"
                + "window.EVENTS_META = " + json.dumps(meta, ensure_ascii=False) + ";\n"
            )
            JS_PATH.write_text(js, encoding="utf-8")

    return {"ok": True, "summary": summary, "cached": False}


def cis_priority_key(e: dict) -> tuple:
    country = e.get("country") or ""
    vert = e.get("vertical") or ""
    cis = 0 if country in CIS_COUNTRIES else 2
    apk = 0 if vert == "apk" else 1
    need = 0 if not is_real_summary(e.get("description"), e.get("title")) else 1
    return (cis, apk, need, e.get("starts_at") or "", e.get("title") or "")


def batch_enrich(
    limit: Optional[int] = None,
    workers: int = 6,
    only_cis: bool = True,
    sleep_s: float = 0.12,
) -> dict:
    events = load_json_events()
    before_cis = [e for e in events if e.get("country") in CIS_COUNTRIES]
    before_real = sum(
        1 for e in before_cis if is_real_summary(e.get("description"), e.get("title"))
    )

    # --- Phase A: donor-copy for empties that have a sibling with real summary ---
    donor_ok = 0
    for e in events:
        if only_cis and e.get("country") not in CIS_COUNTRIES:
            continue
        if is_real_summary(e.get("description"), e.get("title")):
            continue
        donor = find_donor_summary(e, events)
        if donor and is_real_summary(donor, e.get("title")):
            e["description"] = clip_summary(donor)
            donor_ok += 1
    print(f"  donor-copy filled {donor_ok}", flush=True)

    targets = []
    for e in events:
        if only_cis and e.get("country") not in CIS_COUNTRIES:
            continue
        if is_real_summary(e.get("description"), e.get("title")):
            continue
        targets.append(e)
    # Prioritize APK + known tester titles
    PRIORITY_TITLE_RE = re.compile(
        r"growbox|русская\s*рыба|content\s*expo|txca|texcare|wire\s*china",
        re.I,
    )

    def _batch_key(e: dict) -> tuple:
        t = e.get("title") or ""
        pri = 0 if PRIORITY_TITLE_RE.search(t) else 1
        return (pri,) + cis_priority_key(e)

    targets.sort(key=_batch_key)
    if limit:
        targets = targets[:limit]

    stats: dict[str, Any] = {
        "targets": len(targets),
        "ok": 0,
        "fail": 0,
        "donor_ok": donor_ok,
        "before_cis_real": before_real,
        "before_cis_n": len(before_cis),
    }

    # Snapshot catalog for workers (read-only donor lookup); mutations applied after.
    catalog_snap = [dict(x) for x in events]

    def work(ev: dict) -> tuple[str, str, Optional[str]]:
        key = f"{ev.get('title')}|{ev.get('starts_at')}"
        time.sleep(sleep_s)
        try:
            s = fetch_summary_for_event(ev, catalog=catalog_snap)
        except Exception:
            s = None
        return key, ev.get("title") or "", s

    updates: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, e) for e in targets]
        done = 0
        for fut in as_completed(futs):
            key, title, summary = fut.result()
            done += 1
            if summary and is_real_summary(summary, title):
                updates[key] = summary
                stats["ok"] += 1
            else:
                stats["fail"] += 1
            if done % 50 == 0 or done == len(targets):
                print(f"  progress {done}/{len(targets)} ok={stats['ok']} fail={stats['fail']}", flush=True)

    for e in events:
        key = f"{e.get('title')}|{e.get('starts_at')}"
        if key in updates:
            e["description"] = updates[key]

    meta = write_catalog(events)
    after_cis = [e for e in events if e.get("country") in CIS_COUNTRIES]
    after_real = sum(
        1 for e in after_cis if is_real_summary(e.get("description"), e.get("title"))
    )
    stats["after_cis_real"] = after_real
    stats["after_cis_n"] = len(after_cis)
    stats["cis_real_pct"] = (
        round(100.0 * after_real / len(after_cis), 1) if after_cis else 0.0
    )
    stats["updated_at"] = meta["updated_at"]
    stats["count"] = meta["count"]

    examples = []
    for e in after_cis:
        if is_real_summary(e.get("description"), e.get("title")):
            examples.append({
                "title": e["title"],
                "summary": (e.get("description") or "")[:200],
            })
            if len(examples) >= 3:
                break
    stats["examples"] = examples
    return stats


def report_coverage() -> dict:
    events = load_json_events()
    cis = [e for e in events if e.get("country") in CIS_COUNTRIES]
    real = [e for e in cis if is_real_summary(e.get("description"), e.get("title"))]
    nonempty = [e for e in cis if (e.get("description") or "").strip()]
    return {
        "cis_n": len(cis),
        "nonempty": len(nonempty),
        "nonempty_pct": round(100 * len(nonempty) / len(cis), 1) if cis else 0,
        "real": len(real),
        "real_pct": round(100 * len(real) / len(cis), 1) if cis else 0,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--id", type=str, default="")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        print(json.dumps(report_coverage(), ensure_ascii=False, indent=2))
    elif args.id:
        print(json.dumps(enrich_by_id(args.id), ensure_ascii=False, indent=2))
    else:
        stats = batch_enrich(
            limit=args.limit or None,
            workers=args.workers,
            only_cis=not args.all,
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        (SAMPLES / "ingest_report_summary_enrich.txt").write_text(
            json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
