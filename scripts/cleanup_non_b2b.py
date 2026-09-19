#!/usr/bin/env python3
import json, re, sys
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from enrich_summaries import write_catalog

DENY = re.compile(
    r"книжн|литератур|библ(ий|иоте)|библейск|арт[-\s]?завтрак|арт[-\s]?ужин|"
    r"вышив|флористик|handmade|хендмейд|йога|эзотерик|астролог|таро|"
    r"мамочк|мам(а|ы|ин)|детск(ий|ая|ое|ие)\s+(сад|кружок|праздник)|"
    r"школьн\w*\s+кружок|квиз\s+вечер|квартирник|акварель|"
    r"завтрак\s+у\s+тиффани|завтрак\s+на\s+траве|фитнес[-\s]?завтрак|"
    r"тур[-\s]?завтрак|музыкальн\w*\s+перфоманс|перфоманс|"
    r"долче|dolce|gabbana|tiffany|дали\s+и\s+пикассо|"
    r"левина?\s+толст|лев\s+толст|кус[аá]ма|живопис(ь|и)\s+для\s+взрослых|"
    r"с\s+искусством|высотк|котельническ|"
    r"архитектур\w*\s+ар[-\s]?деко|для\s+новых\s+друзей|"
    r"мастер-класс\s+«?арт",
    re.I,
)
B2B = re.compile(
    r"бизнес|предпринимат|руководител|инвест|нетворкинг|стартап|startup|"
    r"\bhr\b|продаж|b2b|финанс|юридич|маркетинг|логистик|производств|"
    r"\bit\b|айти|ии\b|\bai\b|saas|devops|разработ|цифров|технолог|"
    r"агро|ферм|пищев|экспорт|\bвэд\b|кадр|positive\s*technologies",
    re.I,
)
IT_RE = re.compile(
    r"(?:\b(?:it|айти|devops|saas|fintech|стартап|startup|разработ|программ|"
    r"python|frontend|backend|нейросет|\bai\b|digital|цифров|кибер|облачн|"
    r"cloud|api\b|xml\b|видеоаналитик|сколково|hr[\s-]?tech|"
    r"электронн\w*\s+архив|нейрозавтрак)\b)|"
    r"искусственн\w*\s+интеллект|\bии\b|иt[- ]?завтрак|инвестиции\s+в\s+ии|"
    r"positive\s*technologies|axoft|tltgames",
    re.I,
)
APK_RE = re.compile(
    r"агро|ферм|сельхоз|сельск\w*\s+хоз|пищев|молоч|мясн|рыбн|зерн|"
    r"ветерин|комбикорм|теплич|ресторан|отел|хорека",
    re.I,
)

events = json.loads((ROOT / "events_upcoming.json").read_text(encoding="utf-8"))
before = len(events)
kept = []
dropped = 0
drop_examples = []
for e in events:
    title = e.get("title") or ""
    desc = e.get("description") or ""
    blob = f"{title} {desc}"
    et = e.get("type") or ""
    src = e.get("source") or ""
    deny_hit = bool(DENY.search(blob) or DENY.search(title))
    b2b_hit = bool(B2B.search(title) or B2B.search(blob[:400]))
    if deny_hit and not b2b_hit:
        dropped += 1
        if len(drop_examples) < 10:
            drop_examples.append(title[:70])
        continue
    if src == "timepad" or et in {"Бизнес-завтрак", "Бизнес-ужин", "Митап", "Нетворкинг"}:
        if IT_RE.search(blob):
            e["vertical"] = "it"
        elif APK_RE.search(blob):
            e["vertical"] = "apk"
        else:
            e["vertical"] = e.get("vertical") if e.get("vertical") in {"it", "apk", "industry"} else "industry"
    kept.append(e)

print("before", before, "after", len(kept), "dropped", dropped)
print("drop_ex:")
for x in drop_examples:
    print(" -", x)
for et in ["Бизнес-завтрак", "Бизнес-ужин"]:
    rows = [e for e in kept if e.get("type") == et]
    print(et, len(rows), dict(Counter(e.get("vertical") for e in rows)))
    for e in rows[:3]:
        print(" ", e.get("vertical"), "|", (e.get("title") or "")[:60])

meta = write_catalog(kept)
print("meta", meta)
(ROOT / "samples" / "cleanup_non_b2b.txt").write_text(
    json.dumps(
        {"before": before, "after": len(kept), "dropped": dropped, "examples": drop_examples},
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
