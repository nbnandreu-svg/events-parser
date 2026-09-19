#!/usr/bin/env python3
"""Events Calendar ingest round2 — workevent ISO, tsenovik, agrozentr, exponet topics, kudabiz, seeds."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)
TODAY = date(2026, 9, 19)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

MONTHS_RU = {
    "января": 1, "январь": 1, "янв": 1,
    "февраля": 2, "февраль": 2, "фев": 2,
    "марта": 3, "март": 3, "мар": 3,
    "апреля": 4, "апрель": 4, "апр": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июнь": 6, "июн": 6,
    "июля": 7, "июль": 7, "июл": 7,
    "августа": 8, "август": 8, "авг": 8,
    "сентября": 9, "сентябрь": 9, "сен": 9, "сент": 9,
    "октября": 10, "октябрь": 10, "окт": 10,
    "ноября": 11, "ноябрь": 11, "ноя": 11, "нояб": 11,
    "декабря": 12, "декабрь": 12, "дек": 12,
}

# Soft recurring formats: never default unknown → industry
SOFT_TYPES = {
    "Бизнес-завтрак", "Бизнес-ужин", "Нетворкинг", "Митап",
    "Мастер-класс", "Круглый стол", "Семинар", "Вебинар",
    "Встреча", "Мастермайнд",
}

# Curated sources: if no keyword hit, prefer their vertical over dropping/industry
APK_SOURCES = {
    "foodsmi", "tsenovik", "agroinvestor", "agroday", "agrozentr", "agravia",
    "dairyunion", "souzmoloko", "milknews", "feedvet", "sibagroweek",
    "rynok_apk", "agbz", "interagromash", "agrobvk", "meat", "niva",
    "exponet_agri", "zivot",
}
IT_SOURCES = {"ict2go", "jugru", "it_site", "sviaz"}

# Lifestyle / culture / kids / entertainment — exclude from B2B catalog
LIFESTYLE_RE = re.compile(
    r"арт[-\s]?завтрак|арт[-\s]?ужин|книжн\w*\s+завтрак|библейск|"
    r"завтрак\s+с\s+шампанск|акварельн|завтрак\s+с\s+искусств|"
    r"утро\s+в\s+котельническ|завтрак\s+на\s+траве|фитнес[-\s]?завтрак|"
    r"тур[-\s]?завтрак|завтрак\s+с\s+астролог|квартирник|"
    r"театр\w*\s+ужин|ужин\s+с\s+искусствовед|ужин\s+в\s+темноте|"
    r"ужин\s+для\s+новых\s+друзей|ужин\s+среди\s+своих|завтрак\s+для\s+новых\s+друзей|"
    r"ужин\s+с\s+шеф|гончарн|керамическ|"
    r"рисован|живопис|лепк[аеиу]|вышив|вязан|мозаик|витраж|свечевар|мыловар|"
    r"хастл|парные\s+танц|йог[аеиу]|медитац|макияж|девичник|"
    r"день\s+рожден|для\s+детей|детск\w*\s+(?:творческ|мастер)|творческ\w*\s+мастер|"
    r"психоанализ|мегалит|кинематограф|стол\s+по\s+живопис|"
    r"при[её]мные\s+дети|школьн\w*\s+сред|германист|"
    r"музыкальн\w*\s+перфоманс|моноспектакл|zero\s*waste|"
    r"истори[яи]\s+(порнограф|проституц|адюльтер|инквизиц|масон)|"
    r"марк\s+шагал|зигмунд\s+фрейд|романовы|dolce|&?\s*gabbana|tiffany|"
    r"кусама|кондитерск|шоколад|клубника\s+в\s+шоколад|"
    r"сад\s+феи|фестиваль\s+шаров|сильная\?\s*слабая|"
    r"будуар|дзеннетворкинг|4\s*энерги|путь\s+к\s+твоему\s+проявлен|"
    r"книжн\w*\s+клуб|арт\s*-\s*чаепитие|чаепитие|"
    r"образовательн\w*\s+завтрак\s+с\s+директор|"
    r"интеллектуальн\w*\s+завтрак|беседа\s+по\s+мотивам\s+повест|"
    r"завтрак\s+у\s+тиффани|лекция\s+и\s+\(?поздний\)?\s*завтрак|"
    r"камерн\w*\s+лекция|позывной|мужской\s+ужин\s+в\s+ресторан|"
    r"ужин\s+для\s+женщин|модн\w*\s+дом|эстетика\s+90|история\s+модн|"
    r"художники\s+и\s+их\s+музы|лев\s+толстой|дали\s+и\s+пикассо|"
    r"визуальн\w*\s+культур|код\s+идентичности|карельского\s+перешейк|"
    r"флористик|дегустац|винн\w*\s+ужин|камерн\w*\s+арт|"
    r"второй\s+завтрак\s+с\s+|завтрак\s+с\s+леной|"
    r"женщин\w*,\s*которые\s+пока\s+незнаком|"
    r"мастер[-\s]?класс.*(?:кружка|бокал|посуда|хризантем)|миядзаки|"
    r"создание\s+первой\s+игры|флаувау|аллохори|цифровая\s+живопись|"
    r"романтизм|скандальн\w*\s+романов|укради\s+меня|кражи\s+картин|"
    r"русский\s+культурный\s+код|герой\s+романтизма|древний\s+рим|"
    r"нейрофлирт|любов[ьи].*секс|сексуальност|"
    r"свечеварен|диффузор|марципан|анемон|артбук|солнечн\w*\s+зайчик|"
    r"подсолнух|ботан\w*\s+украшен|головные\s+уборы|дневник\s+путешествен|"
    r"дом\s+мечты|внутренн\w*\s+саботаж|фестиваля\s+театра|"
    r"\b(?:0|3|5|6|7|8|9|10|12)\s*\+(?!\d)|дети\s+\d|"
    r"благотворительн\w*\s+ужин(?![^\n]{0,60}промышлен)",
    re.I,
)

APK_RE = re.compile(
    r"агро|сельхоз|сельск|садовод|питомниковод|озеленен|животнов|ветерин|"
    r"молочн|зерн|пищев|пищ[её]в|питани[ея]|ферм(?:ер|а|ы|енн)|скотов|"
    r"птицевод|птицефабр|комбикорм|урожа|полевод|"
    r"(?<![а-яё])дня?\s+поля(?![а-яё])|(?<![а-яё])полевы|(?<![а-яё])поля(?![а-яё])|"
    r"мясн(?:ая|ой|ое|ые|ых|ый)|рыбн|рыбалк|"
    r"food\s*tech|foodtech|dairy|(?<![a-z])meat(?![a-z])|agro|horeca|rural|"
    r"хлеб(?:озавод|пекар)|выпечк|хлебопекар|мук[аи]|молоко|теплич|растениевод|"
    r"кормвет|пропротеин|worldfood|агропрод|югагро|агросалон|агрорусь|агравия|"
    r"iagri|минводыагро|сибagro|сибирск\w*\s+аграрн|золотая\s+нива|агроволга|"
    r"feedvet|seafood|fishery|(?<![a-z])fish(?![a-z])|свиновод|аквакультур|"
    r"пчел(?:овод|ы)|мясоперераб|молокоперераб|growbox|united\s+agri|"
    r"про\s+растен|про\s+озелен|осень\s+на\s+даче|цветы\s+кубан|"
    r"декоративн\w*\s+питомник|niva|нива|петерфуд|интекпром|"
    r"fresh\s+market|eima|фазенда|огород|фазенда|огород|осень\s+на\s+даче|дачн\w*\s+(?:участ|хозяйств)|сад[,\s]+огород|"
    r"resto\s*expo|ресторанн\w*\s+форум|технологи\w*\s+напитк|производств\w*\s+напитк|кондитерск\w*\s+(?:производ|технол|выстав)|"
    r"зож[-\s]?экспо|здоровое\s+питани|охот\w*\s+и\s+рыбал",
    re.I,
)

IT_RE = re.compile(
    r"(?<![а-яёa-z0-9])(?:it|ит|айти|ии|ai|ml|qa|sre|xml|s3|api|saas|llm)(?![а-яёa-z0-9])|"
    r"pro\s*it|proit|"
    r"информационн\w*\s+технолог|кибербезопас|информационн\w*\s+безопасност|"
    r"software|devops|javascript|typescript|python|golang|kubernetes|"
    r"artificial|машинн\w*\s+обучен|data\s*science|облачн|cloud|"
    r"цифров\w*\s+трансф|телеком|highload|frontend|backend|"
    r"blockchain|блокчейн|нейросет|нейро(?!хирург|лог|флирт)|mlops|"
    r"разработчик|программист|positive\s*technologies|axoft|"
    r"робототех|электронн\w*\s+архив|видеоаналитик|"
    r"искусственн\w*\s+интеллект|ит[-\s]?завтрак|нейрозавтрак|"
    r"хакатон|хранен\w*\s+и\s+защит\w*\s+данных|инфратим|"
    r"навыки\s+сильного\s+разработчик|цифров\w*\s+след",
    re.I,
)

INDUSTRY_RE = re.compile(
    r"производств|промышленн|завод|фабрик|металло|станко|машиностроен|"
    r"композитн|строител|энергет|электросет|нефте|газодобы|логистик|"
    r"вэд|таможен|экспорт|импорт|инвестиц|предпринимат|"
    r"(?<![а-яё])мсп(?![а-яё])|продаж|ритейл|торговл|финанс|страхован|"
    r"недвижим|кадр\w*|рекрут|(?<![а-яёa-z])hr(?![а-яёa-z])|"
    r"медицинск\w*\s+(?:клиник|сет)|клиник\w*.{0,40}(?:бизнес|выручк|прибыл)|"
    r"оптимизац\w*\s+расход|торговых\s+предприят|платеж|"
    r"(?<![а-яё])сбп(?![а-яё])|универсальн\w*\s+qr|инфраструктур|"
    r"бизнес[-\s]?разбор|собственник\w*|family\s*office|"
    r"управлени\w*\s+капитал|"
    r"партнёрск\w*\s+завтрак|партнерск\w*\s+завтрак|"
    r"стартап[-\s]?завтрак|l&d|управленческ\w*\s+навык|"
    r"маркетплейс|коллаборац|кооперац|финансирован\w*\s+бизнес|"
    r"строимсибирь|металлообработ|онлайн[-\s]?и\s+офлайн[-\s]?торговл|"
    r"мужского\s+комитет|cre\s*awards|модн\w*\s+бизнес|"
    r"время:hr|психологи\w*\s+денег|защит\w*\s+брендов|крупн\w*\s+клиент|"
    r"закрытый\s+бизнес|для\s+предпринимател|для\s+собственник|"
    r"для\s+руководител|промышленност|монтаж\w*\s+композит|"
    r"анатоми\w*\s+провала\s+в\s+бизнесе|отделы\s+тянут\s+бизнес|"
    r"кофе\s+с\s+кориц|"
    r"здравоохран|медицин(?:а|е|ой|ый|ых|ском)|ликвидн|денежн\w*\s+рын|"
    r"ставк\w*\s+денежн|капитальн\w*\s+ремонт\w*\s+скважин|"
    r"бизнес[-\s]?ужин|бизнес[-\s]?завтрак|приглашаем\s+на\s+бизнес",
    re.I,
)

EXPLICIT_BIZ_MEAL_RE = re.compile(
    r"бизнес[-\s]?(?:завтрак|ужин)|деловом?\s+(?:завтрак|ужин|обед)|"
    r"партнёрск\w*\s+завтрак|партнерск\w*\s+завтрак|стартап[-\s]?завтрак|"
    r"инвестиционн\w*\s+(?:бизнес[-\s]?)?завтрак|ит[-\s]?завтрак|нейрозавтрак|"
    r"инфратим[-\s]?завтрак",
    re.I,
)

added_by_source: Counter = Counter()
stats: dict[str, Any] = {}


def fetch(url: str, dest: Optional[Path] = None, timeout: int = 25, encoding: Optional[str] = None) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        enc = encoding or r.headers.get_content_charset() or "utf-8"
    if dest:
        dest.write_bytes(data)
    for e in ([enc, "utf-8", "cp1251"] if encoding is None else [encoding, "utf-8", "cp1251"]):
        try:
            return data.decode(e)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def strip_tags(s: str) -> str:
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def vertical_for(
    title: str,
    extra: str = "",
    *,
    etype: str = "",
    source: str = "",
) -> Optional[str]:
    """Return it/apk/industry, or None to exclude non-B2B (lifestyle/unknown soft).

    Soft formats (завтрак/ужин/нетворкинг/…) never default to industry.
    Explicit «бизнес-завтрак/ужин» without lifestyle → industry.
    Curated hard types (выставка/конференция/…) still default to industry.
    """
    title = title or ""
    extra = extra or ""
    blob = f"{title} {extra}"
    soft = (etype in SOFT_TYPES) or (source == "timepad")

    # Lifestyle on title always; on description only for soft/timepad (avoid expo false drops)
    life_title = bool(LIFESTYLE_RE.search(title))
    life_extra = bool(LIFESTYLE_RE.search(extra)) if soft else False

    apk = bool(APK_RE.search(blob))
    it = bool(IT_RE.search(blob))
    it_title = bool(IT_RE.search(title))
    apk_title = bool(APK_RE.search(title))
    ind = bool(INDUSTRY_RE.search(blob))
    explicit = bool(EXPLICIT_BIZ_MEAL_RE.search(title))

    # Lifestyle in TITLE always excludes (art/book/bible breakfasts etc.)
    if life_title:
        if explicit and re.search(r"промышленност|производств|инфраструктур", blob, re.I):
            return "industry"
        return None
    if life_extra and not apk_title and not it_title and not explicit:
        return None

    # Prefer title-level vertical signals
    if it_title:
        return "it"
    if apk_title:
        return "apk"
    if it:
        return "it"
    if apk:
        return "apk"
    if ind or explicit:
        return "industry"

    if source in APK_SOURCES:
        return "apk"
    if source in IT_SOURCES:
        return "it"
    if soft:
        return None
    return "industry"


def parse_dot_date(s: str) -> Optional[date]:
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_ru_date_range(text: str, default_year: int = 2026) -> Optional[tuple[date, date]]:
    """Parse ranges like '21-23 января 2026', '21 сентября - 25 сентября', '6–9 октября 2026'."""
    t = text.lower().replace("ё", "е")
    t = t.replace("–", "-").replace("—", "-").replace("−", "-")
    # with year at end
    m = re.search(
        r"(\d{1,2})\s*(?:-|\s+по\s+)?\s*(\d{1,2})?\s*"
        r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
        r"(?:\s+(\d{4}))?"
        r"(?:\s*-\s*(\d{1,2})\s*"
        r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
        r"(?:\s+(\d{4}))?)?",
        t,
    )
    if not m:
        # single day: 11 февраля 2026
        m2 = re.search(
            r"(\d{1,2})\s+"
            r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
            r"(?:\s+(\d{4}))?",
            t,
        )
        if not m2:
            return None
        d1 = int(m2.group(1))
        mon = None
        for k, v in MONTHS_RU.items():
            if m2.group(2).startswith(k[:3]) or m2.group(2).startswith(k):
                mon = v
                break
        # better match
        mon = month_num(m2.group(2))
        if not mon:
            return None
        y = int(m2.group(3) or default_year)
        try:
            dd = date(y, mon, d1)
            return dd, dd
        except ValueError:
            return None

    d1 = int(m.group(1))
    d2 = int(m.group(2)) if m.group(2) else None
    mon1 = month_num(m.group(3))
    y1 = int(m.group(4)) if m.group(4) else None
    d3 = int(m.group(5)) if m.group(5) else None
    mon2 = month_num(m.group(6)) if m.group(6) else None
    y2 = int(m.group(7)) if m.group(7) else None

    if not mon1:
        return None

    if d3 and mon2:  # "21 сентября - 25 сентября"
        y = y2 or y1 or default_year
        try:
            a = date(y if not y1 else y1, mon1, d1)
            b = date(y, mon2, d3)
            return a, b
        except ValueError:
            return None

    y = y1 or default_year
    if d2 is None:
        d2 = d1
    try:
        a = date(y, mon1, d1)
        b = date(y, mon1, d2)
        if b < a:  # cross month unlikely without mon2
            return a, a
        return a, b
    except ValueError:
        return None


def month_num(token: str) -> Optional[int]:
    token = token.lower().replace("ё", "е")
    for k, v in MONTHS_RU.items():
        if token.startswith(k) or k.startswith(token[: max(3, len(token))]):
            return v
    # prefix match
    for k, v in MONTHS_RU.items():
        if token[:4] == k[:4]:
            return v
    return None


def parse_exponet_date_cell(cell: str) -> Optional[tuple[date, date]]:
    """'16.09<br>19.09.2026' or '29.09<br>02.10.2026'"""
    nums = re.findall(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", cell)
    if not nums:
        return None
    year = 2026
    for a, b, y in nums:
        if y:
            year = int(y)
    try:
        d1 = date(year, int(nums[0][1]), int(nums[0][0]))
        if len(nums) >= 2:
            y2 = int(nums[1][2]) if nums[1][2] else year
            # handle year rollover Dec->Jan
            mo2, da2 = int(nums[1][1]), int(nums[1][0])
            if mo2 < int(nums[0][1]) and not nums[1][2]:
                y2 = year  # same year usually for exhibition
            # Sep 29 - Oct 2
            d2 = date(y2, mo2, da2)
            if d2 < d1 and mo2 < int(nums[0][1]):
                d2 = date(year + 1, mo2, da2) if not nums[1][2] else d2
        else:
            d2 = d1
        # fix: if second has full year use it for both year context
        if len(nums) >= 2 and nums[1][2]:
            y_end = int(nums[1][2])
            d1 = date(y_end if int(nums[0][1]) <= int(nums[1][1]) else y_end, int(nums[0][1]), int(nums[0][0]))
            # if start month > end month, start is previous year — rare
            if int(nums[0][1]) > int(nums[1][1]):
                d1 = date(y_end, int(nums[0][1]), int(nums[0][0]))  # same year display quirk
            d2 = date(y_end, int(nums[1][1]), int(nums[1][0]))
            # correct cross-month same year: 29.09 - 02.10.2026
            if d1 > d2:
                d1 = date(y_end, int(nums[0][1]), int(nums[0][0]))
                if d1 > d2:
                    d1 = date(y_end - 1 if int(nums[0][1]) > int(nums[1][1]) else y_end, int(nums[0][1]), int(nums[0][0]))
        return d1, d2
    except ValueError:
        return None


def make_event(
    title: str,
    starts: date,
    ends: date,
    url: str,
    source: str,
    city: str = "",
    country: str = "Россия",
    etype: str = "Выставка",
    extra_vert: str = "",
    description: str = "",
) -> Optional[dict]:
    title = strip_tags(title).strip()
    if not title or len(title) < 3:
        return None
    if ends < TODAY:
        return None
    vert = vertical_for(
        title,
        f"{extra_vert} {city} {description}",
        etype=etype,
        source=source,
    )
    if vert is None:
        return None
    return {
        "title": title,
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
        "city": city.strip().strip(","),
        "country": country,
        "vertical": vert,
        "type": etype,
        "date_status": "confirmed",
        "organizer_url": url,
        "source": source,
        "description": (description or "")[:200],
    }


def slugify_ru(title: str) -> str:
    table = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
        "я": "ya",
    })
    s = title.lower().replace("ё", "е").translate(table)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60]


# ---------------- parsers ----------------

def parse_workevent(html: str, source: str = "workevent") -> list[dict]:
    scripts = [
        s for s in re.findall(r"<script>(self\.__next_f\.push\(.*?\))</script>", html, re.S)
        if "start_date" in s and "events" in s
    ]
    out = []
    id_map = {}
    for l in re.findall(r'href="(/event/[^"]+)"', html):
        m = re.search(r"-(\d+)$", l)
        if m:
            id_map[int(m.group(1))] = "https://workevent.ru" + l

    if not scripts:
        stats[f"{source}_no_rsc"] = True
        return out

    inner_m = re.search(r'push\(\[1,"(.*)"\]\)\s*$', scripts[0], re.S)
    if not inner_m:
        return out
    try:
        decoded = json.loads('"' + inner_m.group(1).replace("\n", "\\n") + '"')
    except json.JSONDecodeError as e:
        stats[f"{source}_json_err"] = str(e)
        return out

    i = decoded.find('"events":[')
    if i < 0:
        return out
    i += len('"events":')
    depth = 0
    end = None
    for k, ch in enumerate(decoded[i:], i):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if not end:
        return out
    events = json.loads(decoded[i:end])
    stats[f"{source}_raw"] = len(events)

    for e in events:
        title = e.get("title") or ""
        sd = e.get("start_date")
        ed = e.get("end_date") or sd
        if not sd:
            continue
        try:
            starts = date.fromisoformat(sd[:10])
            ends = date.fromisoformat(ed[:10])
        except ValueError:
            continue
        eid = e.get("id")
        url = id_map.get(eid) or f"https://workevent.ru/event/{slugify_ru(title)}-{eid}"
        city = (e.get("city") or {}).get("title") or ""
        ind = (e.get("industry") or {}).get("title") or ""
        inds = " ".join((x.get("title") or "") for x in (e.get("industries") or []) if isinstance(x, dict))
        etype = e.get("format_label") or "Выставка"
        ev = make_event(title, starts, ends, url, source, city=city, etype=etype, extra_vert=f"{ind} {inds}")
        if ev:
            out.append(ev)
    return out


def parse_tsenovik_calendar_table(html: str, source: str = "tsenovik") -> list[dict]:
    """Embedded yearly agro calendar table (Russian month ranges)."""
    out = []
    # find rows with date-like first cell and title
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S | re.I):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        if len(cells) < 2:
            continue
        date_s = strip_tags(cells[0])
        title = strip_tags(cells[1])
        if not re.search(r"\d", date_s):
            continue
        if len(title) < 4:
            continue
        # skip headers
        if title.lower() in {"название", "мероприятие"}:
            continue
        city = strip_tags(cells[2]) if len(cells) > 2 else ""
        links = re.findall(r'href="(https?://[^"]+)"', tr)
        url = links[0] if links else "https://www.tsenovik.ru/vystavki/"
        # bitrix tracking links — unwrap
        m = re.search(r"[?&]url=([^&]+)", url)
        if m:
            from urllib.parse import unquote
            url = unquote(m.group(1))
        # year: prefer 2026 in title else from context; ЮгАгро 2025 labeled but Nov dates → use 2026 if upcoming season
        year = 2026
        ym = re.search(r"20(\d{2})", title)
        # keep year from title only if 2026/2027
        if ym and int("20" + ym.group(1)) >= 2026:
            year = int("20" + ym.group(1))
        rng = parse_ru_date_range(date_s + f" {year}", default_year=year)
        if not rng:
            rng = parse_ru_date_range(date_s, default_year=year)
        if not rng:
            continue
        # if title says 2025 but date is Nov and we're in Sep 2026, skip past year
        starts, ends = rng
        if "2025" in title and ends.year < 2026:
            continue
        # force year 2026 for table without year when month>=9 and we're in 2026
        ev = make_event(title, starts, ends, url, source, city=city, extra_vert="агро сельхоз", etype="Выставка")
        if ev:
            out.append(ev)
    return out


def parse_tsenovik_news_rows(html: str, source: str = "tsenovik_news") -> list[dict]:
    """DD.MM.YYYY news-date-time paired with following link — publish dates; keep only if title looks like dated event."""
    out = []
    for m in re.finditer(
        r'<span class="news-date-time">([^<]+)</span>\s*<a href="([^"]+)">([^<]+)</a>',
        html,
    ):
        ds, href, title = m.group(1), m.group(2), unescape(m.group(3))
        d = parse_dot_date(ds)
        if not d:
            continue
        url = href if href.startswith("http") else "https://www.tsenovik.ru" + href
        # These are news publish dates; only keep if clearly an upcoming named exhibition in title with year
        if not re.search(r"20(2[6-9]|[3-9]\d)", title):
            continue
        ev = make_event(title, d, d, url, source, extra_vert="агро", etype="Новость/выставка")
        if ev:
            out.append(ev)
    return out


def parse_agrozentr(html: str, source: str = "agrozentr") -> list[dict]:
    out = []
    for m in re.finditer(
        r'class="event-card__date">([^<]+)</div>\s*'
        r'<div class="event-card__title">([^<]+)</div>(.*?)</div>\s*</div>',
        html,
        re.S,
    ):
        date_s, title, rest = m.group(1), m.group(2), m.group(3)
        rng = parse_ru_date_range(date_s, default_year=2026)
        if not rng:
            continue
        href = re.search(r'href="(https?://[^"]+)"', rest)
        loc = re.search(r'event-card__location">\s*(.*?)</div>', rest, re.S)
        city = strip_tags(loc.group(1)) if loc else ""
        url = href.group(1) if href else "https://agrozentr.ru/"
        ev = make_event(title, rng[0], rng[1], url, source, city=city, extra_vert="агро", etype="Выставка")
        if ev:
            out.append(ev)
    # also table rows
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S | re.I):
        if "month-row" in tr:
            continue
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        if len(cells) < 2:
            continue
        date_s = strip_tags(cells[0])
        if not re.search(r"\d{1,2}.*(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)", date_s, re.I):
            continue
        title = strip_tags(re.sub(r"<img[^>]*>", "", cells[1]))
        title = re.sub(r"\s+", " ", title).strip()
        # prefer <b>title</b>
        bm = re.search(r"<b>([^<]+)</b>", cells[1])
        if bm:
            title = bm.group(1).strip()
        rng = parse_ru_date_range(date_s, 2026)
        if not rng or not title:
            continue
        href = re.search(r'href="(https?://[^"]+)"', tr)
        url = href.group(1) if href else "https://agrozentr.ru/"
        city = strip_tags(cells[3]) if len(cells) > 3 else (strip_tags(cells[2]) if len(cells) > 2 else "")
        if city.startswith("http") or "lazyload" in city or len(city) > 80:
            city = ""
        ev = make_event(title, rng[0], rng[1], url, source, city=city[:60], extra_vert="агро")
        if ev:
            out.append(ev)
    return out


def parse_exponet_topic(html: str, source: str = "exponet_future") -> list[dict]:
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S | re.I):
        if not re.search(r"\d{2}\.\d{2}\.\d{4}", tr):
            continue
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        if len(tds) < 2:
            continue
        rng = parse_exponet_date_cell(tds[0])
        if not rng:
            continue
        titles = re.findall(r"<b>([^<]+)</b>", tds[1])
        if not titles:
            continue
        title = unescape(titles[0]).strip()
        city = ""
        cm = re.search(r"\(г\.\s*([^)]+)\)", tds[1])
        if cm:
            city = strip_tags(cm.group(1))
        href = re.search(r'HREF="([^"]+)"', tds[1], re.I)
        if href:
            h = href.group(1)
            url = h if h.startswith("http") else "https://www.exponet.ru" + h
        else:
            url = "https://www.exponet.ru/"
        desc = ""
        parts = re.split(r"<br\s*/?>", tds[1], flags=re.I)
        if len(parts) > 1:
            desc = strip_tags(parts[-1])[:120]
        ev = make_event(title, rng[0], rng[1], url, source, city=city, description=desc, etype="Выставка")
        if ev:
            out.append(ev)
    return out


def parse_kudabiz_list(html: str, source: str = "kudabiz") -> list[dict]:
    out = []
    for art in re.findall(r"<article class=\"card[^\"]*\">(.*?)</article>", html, re.S):
        href = re.search(r'href="(/event/[^"]+)"', art)
        time_m = re.search(r'<time[^>]+datetime="([^"]+)"', art)
        title_m = re.search(r'class="card-title"[^>]*>\s*<a[^>]*>([^<]+)</a>', art)
        city_m = re.search(r'class="card-city"[^>]*>([^<]+)<', art)
        cat_m = re.search(r'class="card-cat"[^>]*>([^<]+)<', art)
        if not (href and time_m and title_m):
            continue
        try:
            dt = datetime.fromisoformat(time_m.group(1))
            d = dt.date()
        except ValueError:
            d = date.fromisoformat(time_m.group(1)[:10])
        city = strip_tags(city_m.group(1)).replace("📍", "").strip() if city_m else ""
        cat = cat_m.group(1) if cat_m else "Мероприятие"
        url = "https://www.kudabiz.ru" + href.group(1)
        ev = make_event(title_m.group(1), d, d, url, source, city=city, etype=cat, extra_vert=cat)
        if ev:
            out.append(ev)
    return out


def parse_dairyunion(html: str, source: str = "dairyunion") -> list[dict]:
    out = []
    # paired time + title fields
    times = list(re.finditer(r'field="li_time__[^"]*"[^>]*>([^<]+)<', html))
    for tm in times:
        date_s = strip_tags(tm.group(1))
        rng = parse_ru_date_range(date_s, 2026)
        if not rng:
            continue
        # look ahead for title
        chunk = html[tm.end() : tm.end() + 800]
        title_m = re.search(r'field="li_title__[^"]*"[^>]*>([^<]+)<', chunk)
        if not title_m:
            continue
        title = unescape(title_m.group(1)).strip()
        link_m = re.search(r'href="(https?://[^"]+)"', chunk)
        url = link_m.group(1) if link_m else "https://dairyunion.ru/calendar"
        ev = make_event(title, rng[0], rng[1], url, source, extra_vert="молоч агро", etype="Форум")
        if ev:
            out.append(ev)
    return out


def parse_zivot_seed(html: str, source: str = "zivot_seed") -> list[dict]:
    """Extract from agrokalendar article plain-text patterns."""
    out = []
    text = strip_tags(html)
    # patterns: Name: Дата: 26–29 мая 2026 Место: ...
    for m in re.finditer(
        r"([A-Za-zА-Яа-яЁё0-9 «»\"\-]{5,80}?)\s*:\s*Дата:\s*([^М\n]{5,40}?)\s*Место:\s*([^\n]{3,60})",
        text,
    ):
        title, date_s, place = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        rng = parse_ru_date_range(date_s, 2026)
        if not rng:
            continue
        city = place.split(",")[0].strip()
        ev = make_event(title, rng[0], rng[1], "https://zivotnovodstvo.ru/agrokalendar-2026", source, city=city, extra_vert="агро")
        if ev:
            out.append(ev)
    # looser: Name 2026: 9–11 июня, City
    for m in re.finditer(
        r"([A-Za-zА-Яа-яЁё0-9 «»\"\-]{5,60}?)\s*:\s*"
        r"(\d{1,2}\s*[–\-]\s*\d{1,2}\s+[а-яё]+(?:\s+\d{4})?|\d{1,2}\s+[а-яё]+\s+\d{4})"
        r"(?:,\s*([А-Яа-яЁёA-Za-z\-\s]+))?",
        text,
        re.I,
    ):
        title, date_s, city = m.group(1).strip(), m.group(2), (m.group(3) or "").strip()
        if len(title) < 5 or "http" in title.lower():
            continue
        rng = parse_ru_date_range(date_s, 2026)
        if not rng:
            continue
        ev = make_event(title, rng[0], rng[1], "https://zivotnovodstvo.ru/agrokalendar-2026", source, city=city, extra_vert="агро")
        if ev:
            out.append(ev)
    return out


def parse_single_event_site(html: str, title_fallback: str, url: str, source: str, extra: str = "агро") -> list[dict]:
    out = []
    # datetime attrs
    times = re.findall(r'datetime="(\d{4}-\d{2}-\d{2})', html)
    if times:
        dates = sorted(set(date.fromisoformat(t) for t in times))
        starts, ends = dates[0], dates[-1]
        title_m = re.search(r"<title>([^<]+)", html)
        title = strip_tags(title_m.group(1) if title_m else title_fallback).split("|")[0].split("—")[0].strip()
        ev = make_event(title or title_fallback, starts, ends, url, source, extra_vert=extra)
        if ev:
            out.append(ev)
            return out
    # Russian range in body near 2026
    for m in re.finditer(
        r"(\d{1,2}\s*[–\-]\s*\d{1,2}\s+[а-яё]+\s+2026|\d{1,2}\s+[а-яё]+\s+2026)",
        html,
        re.I,
    ):
        rng = parse_ru_date_range(m.group(1), 2026)
        if rng and rng[1] >= TODAY:
            ev = make_event(title_fallback, rng[0], rng[1], url, source, extra_vert=extra)
            if ev:
                out.append(ev)
                return out
    # DD.MM.YYYY
    dots = [parse_dot_date(x) for x in re.findall(r"\d{2}\.\d{2}\.\d{4}", html)]
    dots = sorted({d for d in dots if d and d >= TODAY})
    if dots:
        ev = make_event(title_fallback, dots[0], dots[-1], url, source, extra_vert=extra)
        if ev:
            out.append(ev)
    return out


def load_html(name: str, encoding: Optional[str] = None) -> str:
    p = SAMPLES / name
    if not p.exists():
        return ""
    raw = p.read_bytes()
    if encoding:
        return raw.decode(encoding, errors="replace")
    for e in ("utf-8", "cp1251"):
        try:
            return raw.decode(e)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def dedupe_key(e: dict) -> str:
    return (e.get("organizer_url") or "").strip().lower() + "|" + (e.get("title") or "").strip().lower()


def merge(existing: list[dict], new_events: list[dict], source_label: str) -> list[dict]:
    seen = {dedupe_key(e) for e in existing}
    # also title-only soft? no — stick to organizer_url|title
    added = 0
    for e in new_events:
        k = dedupe_key(e)
        if k in seen:
            continue
        # also skip if same title+start already present from any source
        soft = (e["title"].strip().lower(), e["starts_at"])
        if any((x["title"].strip().lower(), x["starts_at"]) == soft for x in existing):
            continue
        existing.append(e)
        seen.add(k)
        added += 1
        added_by_source[source_label] += 1
    stats[f"added_{source_label}"] = added
    return existing


def write_outputs(events: list[dict]) -> None:
    events = [e for e in events if e.get("ends_at", "") >= TODAY.isoformat()]
    events.sort(key=lambda e: (e.get("starts_at") or "", e.get("title") or ""))
    path = ROOT / "events_upcoming.json"
    path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")

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
            "status": e.get("date_status") or "confirmed",
            "url": e.get("organizer_url") or "",
            "place": e.get("city") or "—",
        })
    js = "window.EVENTS = " + json.dumps(js_events, ensure_ascii=False) + ";\n"
    (ROOT / "events-data.js").write_text(js, encoding="utf-8")


def main():
    existing = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    before_n = len(existing)
    before_apk = sum(1 for e in existing if e.get("vertical") == "apk")
    before_by = Counter(e.get("source") for e in existing)
    print(f"BEFORE N={before_n} apk={before_apk} by_source={dict(before_by)}")

    # --- 1. Workevent ---
    we_files = ["workevent_2026.html"] + [
        f"workevent_{x}.html"
        for x in ["it", "construction", "hr", "finansy", "reklama_i_marketing"]
    ]
    we_all = []
    for fn in we_files:
        html = load_html(fn)
        if not html:
            continue
        part = parse_workevent(html, "workevent")
        print(f"  workevent {fn}: parsed {len(part)}")
        we_all.extend(part)
    # dedupe within workevent harvest
    we_dedup = {}
    for e in we_all:
        we_dedup[dedupe_key(e)] = e
    existing = merge(existing, list(we_dedup.values()), "workevent")

    # --- 2. Tsenovik calendar table (from any month page — same table) + news pairing ---
    ts_cal = []
    ts_news = []
    for m in range(1, 13):
        html = load_html(f"tsenovik_m{m:02d}.html")
        if not html:
            html = load_html("tsenovik.html")
        if not html:
            continue
        cal = parse_tsenovik_calendar_table(html, "tsenovik")
        news = parse_tsenovik_news_rows(html, "tsenovik")
        ts_cal.extend(cal)
        ts_news.extend(news)
        print(f"  tsenovik m{m:02d}: cal={len(cal)} news_kept={len(news)}")
    # unique cal
    cal_u = {dedupe_key(e): e for e in ts_cal}
    news_u = {dedupe_key(e): e for e in ts_news}
    print(f"  tsenovik unique cal={len(cal_u)} news={len(news_u)}")
    # save evidence snippet
    (SAMPLES / "tsenovik_parse_note.txt").write_text(
        "Calendar table rows use Russian month ranges (e.g. '27–29 октября'), not DD.MM.YYYY.\n"
        "news-date-time DD.MM.YYYY are article publish dates on /vystavki/ news list; "
        "row/link pairing now works but most fail ends_at>=today or are not event dates.\n"
        f"cal_unique_upcoming={len(cal_u)} news_unique_upcoming={len(news_u)}\n",
        encoding="utf-8",
    )
    existing = merge(existing, list(cal_u.values()), "tsenovik")
    existing = merge(existing, list(news_u.values()), "tsenovik")

    # --- 3. Agrozentr ---
    html = load_html("agrozentr.html")
    az = parse_agrozentr(html, "agrozentr")
    print(f"  agrozentr: {len(az)}")
    existing = merge(existing, az, "agrozentr")

    # --- 4. Exponet topics ---
    for tag, src in [
        ("exponet_agriculture.html", "exponet_future"),
        ("exponet_building.html", "exponet_future"),
        ("exponet_transport.html", "exponet_future"),
    ]:
        html = load_html(tag, encoding="cp1251")
        if not html:
            continue
        # save utf8 sample evidence
        (SAMPLES / tag.replace(".html", "_utf8.html")).write_text(html, encoding="utf-8")
        part = parse_exponet_topic(html, "exponet_future")
        print(f"  {tag}: {len(part)}")
        existing = merge(existing, part, f"exponet:{tag}")

    # --- 5. KudaBiz list pages (datetime present) ---
    kb = []
    for fn in ["kudabiz.html"] + [f"kudabiz_p{i}.html" for i in range(2, 12)]:
        html = load_html(fn)
        if html:
            part = parse_kudabiz_list(html)
            print(f"  {fn}: {len(part)}")
            kb.extend(part)
    kb_u = {dedupe_key(e): e for e in kb}
    existing = merge(existing, list(kb_u.values()), "kudabiz")

    # --- 6. Dairyunion ---
    html = load_html("dairyunion.html")
    du = parse_dairyunion(html)
    print(f"  dairyunion: {len(du)}")
    existing = merge(existing, du, "dairyunion")

    # --- 7. Zivot seed ---
    html = load_html("zivot_agrokalendar.html") or load_html("zivot_agrokalendar2.html")
    zv = parse_zivot_seed(html)
    print(f"  zivot_seed: {len(zv)}")
    existing = merge(existing, zv, "zivot_seed")

    # --- 8. Single-event APK sites ---
    singles = [
        ("rfd.html", "Всероссийский день поля 2026", "https://russian-field-day.ru/", "russian_field_day"),
        ("niva.html", "Золотая Нива 2026", "https://niva-expo.ru/", "niva_expo"),
        ("donpole.html", "День Донского поля 2026", "https://don-pole.ru/ru/", "don_pole"),
        ("agrosalon.html", "АГРОСАЛОН 2026", "https://www.agrosalon.ru/", "agrosalon"),
        ("agravia.html", "AGRAVIA / iAGRI 2026", "https://www.agros-expo.com/", "agravia"),
        ("sibagro.html", "Сибирская аграрная неделя 2026", "https://sibagroweek.ru/", "sibagroweek"),
        ("agrovolga.html", "АГРОВОЛГА 2026", "https://agrovolga.org/", "agrovolga"),
        ("feedvet.html", "КормВетГрейн Экспо 2026", "https://www.feedvet-expo.ru/", "feedvet"),
        ("milknews.html", "Мероприятия Milknews 2026", "https://milknews.ru/education/2026/", "milknews"),
    ]
    for fn, title, url, src in singles:
        html = load_html(fn)
        if not html:
            print(f"  {src}: missing html")
            continue
        part = parse_single_event_site(html, title, url, src)
        print(f"  {src}: {len(part)}")
        existing = merge(existing, part, src)

    # final filter + write
    write_outputs(existing)
    final = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
    vert = Counter(e.get("vertical") for e in final)
    src = Counter(e.get("source") for e in final)
    print("\n==== RESULT ====")
    print(f"N={len(final)} (before {before_n}, delta {len(final)-before_n})")
    print(f"verticals: {dict(vert)}")
    print(f"apk delta: {vert.get('apk',0) - before_apk} (now {vert.get('apk',0)})")
    print(f"by source: {dict(src)}")
    print(f"added_by_source: {dict(added_by_source)}")
    print(f"stats: {stats}")


if __name__ == "__main__":
    main()
