"""Extract facts from the matching event page, not its navigation or speakers' cities."""
import json
import re
from urllib.parse import urlsplit, urljoin
from bs4 import BeautifulSoup
from catalog_core import clean, short_summary, title_key, canonical_url, stamp

def matching_title(expected, actual):
    a,b=title_key(expected),title_key(actual)
    if not a or not b:return False
    if a in b or b in a:return min(len(a),len(b))>=5
    generic={'форум','конференция','международный','международная','выставка','россия','москва','онлайн','день','day','it','в','и','на','по'}
    aa={x for x in a.split() if len(x)>2 and x not in generic}
    bb={x for x in b.split() if len(x)>2 and x not in generic}
    return bool(aa and len(aa&bb)/len(aa)>=.65)

def schema_events(soup):
    def walk(obj):
        if isinstance(obj,list):
            for x in obj:yield from walk(x)
        elif isinstance(obj,dict):
            typ=obj.get('@type',[]);typ=typ if isinstance(typ,list) else [typ]
            if any(str(x).endswith('Event') for x in typ):yield obj
            for key in ('@graph','itemListElement','item'):
                if key in obj:yield from walk(obj[key])
    for node in soup.select('script[type="application/ld+json"]'):
        try:yield from walk(json.loads(node.string or node.get_text()))
        except (ValueError,TypeError):continue

def language_evidence(soup,url,year):
    evidence=[]
    for node in soup.select('p,li,.languages,.translation,[class*="speaker"],[class*="Speaker"]'):
        blob=clean(node.get_text(' ',strip=True))
        if len(blob)>1000:continue
        if re.search(r'синхронн\w*\s+перевод|simultaneous|рабочи\w*\s+язык|working languages|(?:доклад|выступлен|presentation).{0,80}(?:английск|english|китайск|chinese)',blob,re.I):
            evidence.append({'text':blob,'url':url,'edition_year':year,'checked_at':stamp()})
        speaker_context=bool(re.search(r'(?:спикер|докладчик|эксперт|speaker).{0,60}(?:из\s|from\s)|(?:иностранн|зарубежн).{0,25}(?:спикер|докладчик)',blob,re.I) or re.search('speaker',' '.join(node.get('class',[])),re.I))
        if speaker_context and re.search(r'Китай|КНР|Индия|Индии|Иран|Бразил|Турци|Япони|Кореи|Германи|Франци|Итали|Великобритани|США|Канада|Канады|ОАЭ|Египет|China|India|Iran|Brazil|Turkey|Japan|Korea|Germany|France|Italy|Canada|USA|UAE',blob,re.I):
            evidence.append({'text':blob,'url':url,'edition_year':year,'checked_at':stamp(),'kind':'foreign_speaker'})
    return evidence


def related_evidence(html,url,event):
    soup=BeautifulSoup(html,'lxml')
    year=event['starts_at'][:4]
    headings=' '.join(clean(x.get_text(' ',strip=True)) for x in soup.select('h1,title'))
    years=set(re.findall(r'\b20\d{2}\b',headings))
    if years and year not in years:return []
    path_years=set(re.findall(r'(?<!\d)20\d{2}(?!\d)',urlsplit(url).path))
    if path_years and year not in path_years:return []
    for n in soup.select('script,style,nav,footer,form,.archive'):n.decompose()
    return language_evidence(soup,url,year)


def extract_facts(html,url,event):
    from event_engine import extract_event_dates
    soup=BeautifulSoup(html,'lxml')
    expected=event.get('title','');year=str(event.get('starts_at',''))[:4]
    titles=[clean(n.get_text(' ',strip=True)) for n in soup.select('h1')]
    if soup.title:titles.append(clean(soup.title.get_text(' ',strip=True)))
    schema=next((o for o in schema_events(soup) if matching_title(expected,o.get('name',''))),None)
    if not any(matching_title(expected,t) for t in titles) and not schema:
        return {'ok':False,'reason':'title_mismatch','page_titles':titles[:3]}
    host=urlsplit(url).netloc.lower()
    about=soup.select_one('section.about') if 'conferos.ru' in host else None
    date_node=soup.select_one('.conference__date') if 'conferos.ru' in host else None
    date_text=clean(date_node.get_text(' ',strip=True)) if date_node else ''
    start=end=None
    if date_text:start,end=extract_event_dates(date_text)
    if not start and schema and schema.get('startDate'):
        from event_engine import parse_date
        start=parse_date(schema['startDate']);end=parse_date(schema.get('endDate')) or start
        date_text=str(schema['startDate'])
    # Only compare years from the actual event header / schema, not historical paragraphs.
    header_years={y for title in titles[:1] for y in re.findall(r'\b20\d{2}\b',title)}
    # "Итоги года и планы 2027" names the planning horizon, not the edition.
    if any(re.search(r'планы\s+20\d{2}',t,re.I) for t in titles[:1]):header_years=set()
    from catalog_core import today
    adjacent_conferos=bool(start and 'conferos.ru' in host and year.isdigit() and abs(start.year-int(year))==1 and start.date()>=today())
    if start and year and str(start.year)!=year and event.get('source') != 'tadviser_calendar' and not adjacent_conferos:
        return {'ok':False,'reason':'edition_mismatch','page_date':start.isoformat()}
    if not start and len(header_years)==1 and year and year not in header_years:
        return {'ok':False,'reason':'edition_mismatch','page_year':next(iter(header_years))}
    for n in soup.select('script,style,noscript,nav,footer,form,.archive,.archive-list'):
        n.decompose()
    if about:
        text=' '.join(clean(x.get_text(' ',strip=True)) for x in about.select('p,li')) or clean(about.get_text(' ',strip=True))
    else:
        text=clean(schema.get('description','')) if schema else ''
        if not text:
            node=soup.select_one('[itemprop="description"],.event-description,.event__description,.event-detail__description,.event_description,.article__text,.news-detail,.content-text,.mw-parser-output')
            if node:text=clean(node.get_text(' ',strip=True))
        if not text:
            heading=next((h for h in soup.select('h2,h3') if re.fullmatch(r'о\s+(?:саммите|мероприятии|конференции|форуме|выставке)',clean(h.get_text(' ',strip=True)),re.I)),None)
            if heading:
                pieces=[]
                for sibling in heading.next_siblings:
                    if getattr(sibling,'name',None) in ('h2','h3'):break
                    if hasattr(sibling,'get_text'):
                        value=clean(sibling.get_text(' ',strip=True))
                        if len(value)>60:pieces.append(value)
                    if len(' '.join(pieces))>1200:break
                text=' '.join(pieces)
        if not text:
            meta=soup.select_one('meta[property="og:description"],meta[name="description"]')
            if meta:text=clean(meta.get('content',''))
        if len(text)<90:
            main=soup.select_one('article,main')
            if main:
                paragraphs=[clean(p.get_text(' ',strip=True)) for p in main.select('p')]
                text=' '.join(p for p in paragraphs if len(p)>60)[:5000]
    # A date-only or stale edition blurb must not become a confident summary.
    wrong_year=re.search(r'\b(20\d{2})\b.{0,45}(?:состоится|пройдет)|(?:состоится|пройдет).{0,45}\b(20\d{2})\b',text[:500],re.I)
    if wrong_year and year not in wrong_year.groups():
        text=''
    description=short_summary(text,expected)
    location='';country=''
    if schema:
        loc=schema.get('location') or {}
        if isinstance(loc,dict):
            address=loc.get('address') or {}
            if isinstance(address,dict):
                location=clean(address.get('addressLocality',''));country=clean(address.get('addressCountry',''))
    evidence=[]
    if start and event.get('source')=='tadviser_calendar':year=str(start.year)
    edition_valid=bool(start and str(start.year)==year or year in header_years)
    if edition_valid:evidence=language_evidence(soup,url,year)
    related=[]
    for a in soup.select('a[href]'):
        label=clean(a.get_text(' ',strip=True));href=urljoin(url,a['href'])
        if a['href'].startswith('#') or href.lower().endswith('.pdf'):continue
        if urlsplit(href).netloc != host:continue
        if re.fullmatch(r'программа(?: конференции| форума)?|спикеры|докладчики|условия участия|program(?:me)?|speakers',label,re.I):
            if canonical_url(href)!=canonical_url(url) and href not in related:related.append(href)
    official=''
    if 'tadviser.ru' in host:
        article=soup.select_one('.mw-parser-output,#mw-content-text')
        if article:
            for a in article.select('a[href]'):
                target=urljoin(url,a['href'])
                if urlsplit(target).netloc.removeprefix('www.')=='conferos.ru' and '/event/' in target:
                    official=target;break
    for a in soup.select('a[href]'):
        label=clean(a.get_text(' ',strip=True))
        if re.search(r'^(?:официальный сайт|сайт мероприятия|сайт организатора|перейти на сайт мероприятия)$',label,re.I):
            target=urljoin(url,a['href'])
            if urlsplit(target).netloc and urlsplit(target).netloc!=host and not official:official=target;break
    children=[]
    if host.removeprefix('www.')=='tadvisersummit.ru':
        children=list(dict.fromkeys(canonical_url(urljoin(url,a['href'])) for a in soup.select('a[href]') if 'conferos.ru/event/' in a['href']))
    return {'ok':True,'description':description,'description_source':url,'description_checked_at':stamp(),'child_event_urls':children,
            'language_evidence':evidence,'date_start':start.date().isoformat() if start else None,
            'date_end':end.date().isoformat() if end else None,'date_evidence':date_text,
            'city':location,'country':country,'official_url':official,'related_urls':related[:2] if edition_valid else []}
