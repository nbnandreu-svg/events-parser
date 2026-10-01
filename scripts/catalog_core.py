"""Shared catalog storage, conservative identity and evidence-based filtering."""
from __future__ import annotations
import hashlib
import html
import json
import os
import re
import threading
from collections import Counter
from datetime import date, datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CATALOG_LOCK = threading.RLock()
MOSCOW = timezone(timedelta(hours=3))

def today():
    return datetime.now(MOSCOW).date()

def stamp():
    return datetime.now(MOSCOW).isoformat(timespec='seconds')

def clean(value):
    value = html.unescape(str(value or ''))
    if '<' in value:
        value = BeautifulSoup(value, 'lxml').get_text(' ', strip=True)
    return re.sub(r'\s+', ' ', value).strip()

def canonical_url(value):
    p = urlsplit(html.unescape(str(value or '')))
    if p.scheme not in ('http', 'https') or not p.netloc:
        return ''
    q = [(k, v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in {'erid','fbclid','yclid','gclid'}]
    return urlunsplit((p.scheme, p.netloc.lower().removeprefix('www.'), p.path.rstrip('/'), urlencode(sorted(q)), ''))

def title_key(value):
    text = clean(value).lower().replace('ё', 'е')
    text = re.sub(r'\b20\d{2}\b', ' ', text)
    text = re.sub(r'^(?:xxviii|xxvii|xxvi|xxv|xxiv|xxiii|xxii|xxi|xx|xix|xviii|xvii|xvi|xv|xiv|xiii|xii|xi|ix|viii|vii|vi|iv|iii|ii|x|v|i)\s+', '', text)
    text = re.sub(r'^\d+[- ]?(?:я|й|ая|ый)\s+', '', text)
    text = re.sub(r'\b(?:международн[а-я]*|всероссийск[а-я]*|специализированн[а-я]*)\b', ' ', text)
    text = re.sub(r'\b(?:выставка|конференция|форум)\b', ' ', text)
    text = re.sub(r'(?:18\+|\+18)', ' ', text)
    text = re.sub(r'\bх\b', 'x', text)
    text = re.sub(r'\bежегодн[а-я]*\b', ' ', text)
    text = re.sub(r'биопром\s*:\s*промышленность и технологии для человека', 'биопром', text)
    return re.sub(r'[^a-zа-я0-9]+', ' ', text).strip()

def title_match(a, b):
    raw_a,raw_b=clean(a),clean(b)
    def labels(raw):
        vals=[title_key(raw)]
        if '|' in raw:vals.append(title_key(raw.split('|',1)[0]))
        vals.extend(title_key(x) for x in re.findall(r'[«"]([^»"]{8,})[»"]',raw))
        return {x for x in vals if len(x)>=8}
    if labels(raw_a)&labels(raw_b):return True
    a, b = title_key(a), title_key(b)
    if len(a) < 4 or len(b) < 4:
        return a == b and bool(a)
    if a == b:
        return True
    # Require a substantial shared title; never merge only on "форум"/"конференция".
    return min(len(a),len(b)) >= 12 and SequenceMatcher(None,a,b).ratio() >= .93

def is_hub(url):
    p=urlsplit(canonical_url(url))
    return (not p.netloc or p.path in ('','/events','/expo','/conference','/exhibition','/calendar')
            and (p.netloc.endswith(('expomap.ru','all-events.ru','crocus-expo.ru','ict2go.ru','workevent.ru'))))

def normalize_city(value):
    value=clean(value)
    aliases={'almaty':'Алматы','moscow':'Москва','saint petersburg':'Санкт-Петербург','россия':'','рф':''}
    if value.lower() in aliases:return aliases[value.lower()]
    value=re.sub(r'^(?:Германия|Италия|Франция|Казахстан|Узбекистан),?\s*г\.\s*','',value,flags=re.I)
    # Normalize only the dedicated place field, never speaker/company text.
    cities=('Санкт-Петербург','Нижний Новгород','Ростов-на-Дону','Минеральные Воды','Москва','Красногорск','Екатеринбург','Новосибирск','Казань','Краснодар','Самара','Челябинск','Уфа','Сочи','Пермь','Воронеж','Волгоград','Томск','Тюмень','Иркутск','Тула','Минск','Астана','Алматы','Ташкент','Онлайн')
    for city in cities:
        if re.search(r'(?<![а-я])'+re.escape(city)+r'(?![а-я])',value,re.I):return city
    if re.search(r'крокус',value,re.I):return 'Москва'
    if re.search(r'адрес|\bул\.|улица|переулок|пер\.|информация|предоставляется|уточняется|не указано|центр|пространство|кластер|плаза|plaza|кибердом|аллея',value,re.I):return ''
    return value

def same_edition(a, b):
    if a.get('starts_at') != b.get('starts_at'):
        return False
    if a.get('ends_at')!=b.get('ends_at'):
        # A listing often supplies only the opening day. Two distinct multi-day
        # ranges remain a conflict, not an automatic merge.
        if a.get('ends_at')!=a.get('starts_at') and b.get('ends_at')!=b.get('starts_at'):return False
    if a.get('country') and b.get('country') and a['country'] != b['country']:
        return False
    ac,bc = normalize_city(a.get('city')).lower(),normalize_city(b.get('city')).lower()
    au,bu=canonical_url(a.get('organizer_url')),canonical_url(b.get('organizer_url'))
    online_pair='онлайн' in {ac,bc} and (title_key(a.get('title'))==title_key(b.get('title')) or bool(au and au==bu and not is_hub(au) and SequenceMatcher(None,title_key(a.get('title')),title_key(b.get('title'))).ratio()>.65))
    if ac and bc and ac != bc and not online_pair:return False
    if title_match(a.get('title'),b.get('title')) and len(title_key(a.get('title')))>=4:
        return True
    return bool(au and au==bu and not is_hub(au) and SequenceMatcher(None,title_key(a.get('title')),title_key(b.get('title'))).ratio()>.65)

def short_summary(value, title='', limit=460):
    text=clean(value)
    if re.search(r'transition-(?:property|timing)|(?:font-size|background-color|border-color)\s*:|@media\s*\(|function\s*\(',text,re.I):return ''
    if re.match(r'^Новости\b',text,re.I):return ''
    if re.match(r'^(?:Конференция|Вебинар|Форум|Семинар)\s',text,re.I) and len(text)<180 and not re.search(r'посвящ|обсуд|пройдет|разбер|состо|представ|узна|собер|объедин',text,re.I):return ''
    if re.match(r'^Дата проведения:',text,re.I):return ''
    if re.search(r'информация о международных, национальных|выставки и ярмарки.*проводимых в России|информация о мероприятиях.*всего мира',text,re.I):
        return ''
    # Remove aggregator boilerplate, registration prompts and appended logistics.
    text=re.split(r'Подробности на All.Events|Даты проведения:|Место проведения:|Тип мероприятия:',text,flags=re.I)[0]
    text=re.sub(r'[^.!?]*программа,\s*спикеры,\s*стоимость участия[^.!?]*[.!?]?', '',text,flags=re.I)
    text=re.sub(r'^(?:Conferos.портал мероприятий\s*)?(?:О конференции\s*)?', '',text,flags=re.I)
    text=re.sub(r'https?://\S+','',text)
    text=clean(text)
    if len(text)<45 or text.count('�') or re.search(r'javascript|access denied|captcha|cloudflare|страница не найдена',text,re.I):
        return ''
    if title_key(text)==title_key(title):
        return ''
    sentences=re.findall(r'[^.!?]+[.!?](?:[»”"])?(?=\s|$)',text)
    accepted=[]
    for sent in sentences:
        sent=sent.strip()
        if re.search(r'куки|cookie|согласие на обработку|политик[аи] конфиденциальности|зарегистрируйтесь|успейте купить|all.events\.ru|не упустите шанс|вы когда.нибудь|не дайте|^коллеги|^в эпоху|^эпоха|посвящена рению',sent,re.I):
            continue
        if sum(map(len,accepted))+len(sent)+len(accepted)>limit:break
        accepted.append(sent)
        if len(accepted)>=3:break
    if accepted and len(' '.join(accepted))>=45:
        return ' '.join(accepted)
    if sentences:return ''
    # Do not emit an arbitrary mid-sentence truncation as a finished summary.
    if 45<=len(text)<=limit and not text.endswith(('…','...')):
        return text.rstrip(' ,;:')+'.' if text[-1] not in '.!?' else text
    return ''

def assess_translation_evidence(event, evidence=None):
    """No inference from a name, brand, exhibitor nationality or 'international'."""
    base={'audience':'unknown','translation_status':'unknown','translation_evidence':[]}
    from venue_rules import venue_country
    if venue_country(event) not in ('Россия','РФ','Russian Federation','Russia'):
        return {**base,'translation_status':'outside_russia'}
    if clean(event.get('city')).lower() in {'онлайн','online','онлайн-трансляция'}:
        return {**base,'translation_status':'online_only'}
    if re.search(r'Великобритани|Германи|Франци|Итали|Казахстан|Беларус|Узбекистан|Кыргызстан|Турци|Серби|Лондон|Белград|Париж|Дубай|Стамбул|Алматы|Астана|Ташкент|Минск|Сеул',event.get('city',''),re.I):
        return {**base,'translation_status':'venue_conflict'}
    year=(event.get('starts_at') or '')[:4]
    qualified=[]
    from language_rules import COUNTRY_RE,LANGUAGE_RE,NEGATED,OPERATIONAL_TRANSLATION,PRESENTATION_LANGUAGE,COUNTRIES
    for item in (event.get('language_evidence') or []) if evidence is None else evidence:
        if str(item.get('edition_year','')) != year or not item.get('url'):
            continue
        text=clean(item.get('text'))
        if not text:continue
        language=LANGUAGE_RE.search(text)
        # Country alone is insufficient, but an explicitly identified speaker from
        # a non-Russian-language country is a potential interpretation lead.
        # Country in an old job or education paragraph is not the speaker's
        # current affiliation. Do not treat delegates/exhibitors as speakers.
        actor_header=re.split(r'профессиональная деятельность|(?:ранее |раньше )|работал[аи]?\b|работала\b|образование\s*:|previously|formerly|education\s*:',text,flags=re.I)[0]
        foreign_country=COUNTRY_RE.search(actor_header)
        if item.get('kind')=='foreign_speaker' and foreign_country:
            if re.search(r'многолетн|прошлых лет|в их числе|история компании',actor_header,re.I):continue
            explicit_origin=re.search(r'(?:спикер\w*|докладчик\w*|лектор\w*|speakers?|lecturers?).{0,35}\b(?:из|from)\s+(?:'+COUNTRIES+r')\b',actor_header,re.I)
            explicit_speaker=re.search(r'^(?:(?:иностранн\w*|зарубежн\w*)\s+)?(?:спикер\w*|докладчик\w*|лектор\w*|speakers?|lecturers?)\b.{0,120}(?:'+COUNTRIES+r')\b',actor_header,re.I)
            if not item.get('speaker_name') and not explicit_origin and not explicit_speaker:continue
            if not re.search(r'выступ\w*.{0,35}на русском|доклад.{0,25}на русском|presentation.{0,25}in Russian',text,re.I):
                qualified.append({**item,'text':text[:700],'level':'potential'})
            continue
        if NEGATED.search(text):
            continue
        if OPERATIONAL_TRANSLATION.search(text) and not re.search(r'жестов|сурдоперевод|sign language',text,re.I):
            qualified.append({**item,'text':text[:700],'level':'confirmed'});continue
        if not language:continue
        if re.search(r'синхронн\w*\s+перевод|simultaneous\s+(?:interpretation|translation)',text,re.I):
            qualified.append({**item,'text':text[:700],'level':'confirmed'})
        elif PRESENTATION_LANGUAGE.search(text):
            qualified.append({**item,'text':text[:700],'level':'potential'})
    if qualified:
        return {'audience':'intl','translation_status':'confirmed' if any(x['level']=='confirmed' for x in qualified) else 'potential',
                'translation_evidence':qualified[:4]}
    return base

def assess_translation(event, evidence=None):
    """Marketing selection by declared international status; no programme audit required."""
    from venue_rules import venue_country
    from language_rules import COUNTRY_RE
    base={'audience':'unknown','translation_status':'unknown','translation_evidence':[], 'international_reason':''}
    if venue_country(event) not in ('Россия','РФ','Russia','Russian Federation'):
        return {**base,'translation_status':'outside_russia'}
    fields=[]
    for key in ('title','description','summary','topics','themes','tags'):
        value=event.get(key) or ''
        if isinstance(value,list):value=' '.join(str(v) for v in value)
        if value:fields.append((key,clean(value)))
    text=' '.join(value for _,value in fields)
    cis=r'СНГ|Беларус\w*|Казахстан\w*|Кыргызстан\w*|Киргиз\w*|Узбекистан\w*|Таджикистан\w*|Армени\w*|Азербайджан\w*'
    # Only an explicit CIS-only limitation overrides the marketing marker.
    cis_only=bool(re.search(r'только.{0,50}(?:'+cis+r')',text,re.I) or
                  re.search(r'спикер\w*\s+из\s+(?:'+cis+r')',text,re.I))
    if cis_only and not COUNTRY_RE.search(text):
        return {**base,'international_reason':'Указано только русскоязычное участие'}
    markers=[(r'\bмеждународн\w*|\binternational\b','Заявлен международный статус'),
             (r'\bБРИКС\b|\bBRICS\b|\bШОС\b|межгосударственн\w*|межправительственн\w*','Международная тематика'),
             (r'(?:иностранн\w*|зарубежн\w*)\s+(?:спикер\w*|докладчик\w*|эксперт\w*|участник\w*|делегаци\w*)','Указано иностранное участие'),
             (r'синхронн\w*\s+перевод|simultaneous\s+(?:interpretation|translation)','Указан синхронный перевод')]
    for pattern,reason in markers:
        for field,value in fields:
            match=re.search(pattern,value,re.I)
            if match:
                excerpt=value[max(0,match.start()-70):min(len(value),match.end()+180)]
                return {'audience':'intl','translation_status':'declared','international_reason':reason,
                        'translation_evidence':[{'kind':'declared_marker','field':field,'text':excerpt,
                            'url':event.get('organizer_url') or event.get('url') or '',
                            'edition_year':(event.get('starts_at') or event.get('date') or '')[:4]}]}
    # Existing speaker information is a useful additional marker, never a prerequisite.
    result=assess_translation_evidence(event,evidence)
    if result['audience']=='intl':result['international_reason']='Указаны иностранные спикеры или языки выступлений'
    else:result['international_reason']='Международные признаки в карточке не указаны'
    return result


def stable_id(event):
    identity='|'.join((title_key(event.get('title')),event.get('starts_at',''),event.get('ends_at',''),event.get('country',''),event.get('city','')))
    return 'ev-'+hashlib.sha256(identity.encode()).hexdigest()[:16]

def prepare_catalog(events):
    groups={}
    out=[]
    removed=[]
    for raw in events:
        e=dict(raw)
        try:
            start=date.fromisoformat(e.get('starts_at','')[:10]);end=date.fromisoformat((e.get('ends_at') or e['starts_at'])[:10])
        except (ValueError,TypeError,KeyError):
            removed.append({'reason':'invalid_date','event':e});continue
        if end<start or end<today():
            removed.append({'reason':'past_or_invalid_range','event':e});continue
        e['starts_at'],e['ends_at']=start.isoformat(),end.isoformat()
        e['title']=clean(e.get('title'))
        e['city']=normalize_city(e.get('city'))
        from venue_rules import venue_country
        corrected_country=venue_country(e)
        if corrected_country!=e.get('country'):
            e['country_correction']={'previous':e.get('country'),'basis':'venue_city','city':e['city']}
            e['country']=corrected_country
        if not e['title'] or len(re.sub(r'[^a-zа-я0-9]', '', e['title'].lower()))<4 or e['title'].count('�')>2 or re.fullmatch(r'(?:мероприятие|выставка|конференция)\s*(?:20\d{2})?',e['title'],re.I):
            removed.append({'reason':'invalid_title','event':e});continue
        if (end-start).days>45 and not re.search(r'курс|обучен|серия|сезон|акселератор',e['title'],re.I):
            e['date_status']='tentative'
            e['date_warning']='Длительный период проведения: требуется уточнение'
        kind=clean(e.get('type','')).lower()
        if 'конференц' in kind:e['type']='Конференция'
        elif 'выставк' in kind:e['type']='Выставка'
        elif 'форум' in kind:e['type']='Форум'
        elif kind in {'вебинар','семинар','митап','конгресс','саммит','круглый стол','бизнес-завтрак','нетворкинг','премия'}:e['type']=kind.capitalize()
        e['organizer_url']=canonical_url(e.get('organizer_url')) or e.get('organizer_url','')
        e['description']=short_summary(e.get('description'),e['title'])
        e.update(assess_translation(e))
        e.setdefault('source_urls',[u for u in [e.get('organizer_url')] if u])
        e.setdefault('sources',[s for s in [e.get('source')] if s])
        if e.get('description_checked_at') and e.get('description_source'):
            e['source_urls']=list(dict.fromkeys(e['source_urls']+[e['description_source']]))
        # Direct organiser URLs discovered on a verified listing outrank portal
        # news articles and tracking redirects for the same edition.
        official=next((u for u in e['source_urls'] if urlsplit(u).netloc.removeprefix('www.') in {'conferos.ru','tadvisersummit.ru','itprize.tadviser.ru','globaltechforum.ru'}),None)
        if official:e['organizer_url']=canonical_url(official)
        candidates=groups.setdefault(e['starts_at'],[])
        existing=next((x for x in candidates if same_edition(x,e)),None)
        if existing is not None:
            # Keep every source and its evidence. Never borrow across dates/editions.
            for key in ('source_urls','sources'):
                existing[key]=list(dict.fromkeys(existing.get(key,[])+e.get(key,[])))
            if existing.get('city') and e.get('city') and existing['city']!=e['city']:
                existing['location_alternatives']=list(dict.fromkeys(existing.get('location_alternatives',[])+[existing['city'],e['city']]))
                existing['city']=''
            if e['ends_at']!=existing['ends_at']:
                existing.setdefault('date_alternatives',[]).append({'starts_at':e['starts_at'],'ends_at':e['ends_at'],'url':e.get('organizer_url')})
                if existing['ends_at']==existing['starts_at']:
                    existing['ends_at']=e['ends_at']
            detail_better=bool(e.get('description_checked_at') and not existing.get('description_checked_at'))
            if e['description'] and (detail_better or (bool(e.get('description_checked_at'))==bool(existing.get('description_checked_at')) and len(e['description'])>len(existing.get('description','')))):
                existing['description']=e['description']
                for key in ('description_source','description_checked_at'): 
                    if e.get(key):existing[key]=e[key]
            if is_hub(existing.get('organizer_url')) and not is_hub(e.get('organizer_url')):
                existing['organizer_url']=e['organizer_url']
            if urlsplit(e.get('organizer_url','')).netloc in {'conferos.ru','tadvisersummit.ru','itprize.tadviser.ru','globaltechforum.ru'}:
                existing['organizer_url']=e['organizer_url']
            for key in ('city','country','date_evidence','parent_event_url'):
                if key=='city' and existing.get('location_alternatives'):continue
                if not existing.get(key) and e.get(key):existing[key]=e[key]
            existing['language_evidence']=existing.get('language_evidence',[])+e.get('language_evidence',[])
            existing.update(assess_translation(existing))
            removed.append({'reason':'duplicate','event':e});continue
        candidates.append(e);out.append(e)
    used_ids=set()
    for e in out:
        key=str(e.get('id') or stable_id(e))
        if key in used_ids:
            # A legacy ID can survive a date change and collide with a new edition.
            identity=json.dumps([e['title'],e['starts_at'],e['ends_at'],e.get('city'),e.get('country'),e.get('organizer_url')],ensure_ascii=False)
            key='ev-'+hashlib.sha256(identity.encode()).hexdigest()[:20]
        e['id']=key;used_ids.add(key)
    out.sort(key=lambda x:(x['starts_at'],x['title']))
    return out,removed

def to_browser(e):
    return dict(id=e['id'],title=e['title'],vertical=e.get('vertical') or 'industry',type=e.get('type') or 'Мероприятие',
        city=e.get('city',''),country=e.get('country',''),audience=e.get('audience','unknown'),date=e['starts_at'],endDate=e['ends_at'],
        status=e.get('date_status','confirmed'),url=e.get('organizer_url',''),place=e.get('city',''),summary=e.get('description',''),
        source=e.get('source',''),sources=e.get('sources',[]),translationStatus=e.get('translation_status','unknown'),
        translationEvidence=e.get('translation_evidence',[]),internationalReason=e.get('international_reason',''),translationReview=e.get('translation_review',{}),sourceUrls=e.get('source_urls',[]),parentEventUrl=e.get('parent_event_url',''))

def atomic_text(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(text,encoding='utf-8');os.replace(tmp,path)

def write_catalog(events, root=ROOT, extra_meta=None):
    with CATALOG_LOCK:
        events,removed=prepare_catalog(events)
        russian=[e for e in events if e.get('country') in {'Россия','РФ','Russia','Russian Federation'}]
        review_counts=Counter(e.get('translation_review',{}).get('state','pending') for e in russian)
        meta={'updated_at':stamp(),'count':len(events),'international_count':sum(e['audience']=='intl' for e in events),
              'international_coverage':{'total':len(russian),'states':dict(review_counts)},**(extra_meta or {})}
        # Each file is atomically replaced. Both are generated from the same prepared rows.
        atomic_text(root/'events_upcoming.json',json.dumps(events,ensure_ascii=False,indent=2))
        atomic_text(root/'events-data.js','window.EVENTS = '+json.dumps([to_browser(e) for e in events],ensure_ascii=False)+';\nwindow.EVENTS_META = '+json.dumps(meta,ensure_ascii=False)+';\n')
        if removed:
            archive=root/'audit'/'excluded-latest.json'
            atomic_text(archive,json.dumps(removed,ensure_ascii=False,indent=2))
        return meta
