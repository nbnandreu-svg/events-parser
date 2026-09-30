"""Resumable catalog-wide language/programme review, with explicit coverage states."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import io
import json
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit,urljoin
import httpx
from bs4 import BeautifulSoup
from catalog_core import ROOT,stamp,today,clean,canonical_url,is_hub,assess_translation,write_catalog,atomic_text
from page_facts import extract_facts,related_evidence,matching_title,schema_events
from event_engine import USER_AGENT,parse_html,load_sources

AGGREGATORS={'ict2go.ru','expomap.ru','workevent.ru','all-events.ru','kudabiz.ru','exponet.ru','totalexpo.ru','crocus-expo.ru','tadviser.ru'}
RULE_VERSION=3
PROGRAM=re.compile(r'программ|расписани|спикер|докладчик|лектор|program|speaker|lecturer|schedule',re.I)
SKIP=re.compile(r'архив|archive|волонтер|волонтёр|спонсор|политик|cookie|privacy',re.I)

def host(url):return urlsplit(url).netloc.lower().removeprefix('www.')

def same_organisation(a,b):
    x,y=host(a),host(b)
    return x==y or x.endswith('.'+y) or y.endswith('.'+x)

def fingerprint(e):
    return hashlib.sha256(json.dumps([e.get(k) for k in ('title','starts_at','ends_at','organizer_url')],ensure_ascii=False).encode()).hexdigest()[:20]

def finalize_review(event,row):
    verdict=assess_translation(event,row.get('evidence',[]))
    row['classification']=verdict
    if verdict['translation_status'] in {'outside_russia','venue_conflict','online_only'}:row['state']='outside_scope'
    elif verdict['audience']=='intl':row['state']='qualified'
    elif row.get('programme_pages'):row['state']='reviewed_no_signal'
    elif any(p.get('reason')=='pdf_needs_ocr' for p in row.get('pages',[])):row['state']='programme_unreadable'
    elif any(p.get('matched') for p in row.get('pages',[])):
        row['state']='partially_unavailable' if row.get('errors') else 'no_programme_found'
    elif row.get('errors'):row['state']='unavailable'
    elif row.get('pages'):row['state']='identity_or_edition_mismatch'
    else:row['state']='unresolved_url'
    row['fingerprint']=fingerprint(event)
    return row

def official_links(soup,url,event):
    links=[]
    for a in soup.select('a[href]'):
        href=urljoin(url,a['href']);label=clean(a.get_text(' ',strip=True))
        if urlsplit(href).scheme not in ('http','https') or host(href)==host(url):continue
        if re.search(r'сайт мероприятия|сайт организатора|официальн\w* сайт|официальн\w* веб.сайт|organizer.?s? website|event website',label,re.I):
            links.append(href)
    # Workevent's website can be represented only by an icon or by Next.js data.
    if host(url)=='workevent.ru':
        for script in soup.select('script'):
            raw=script.string or script.get_text();m=re.search(r'self\.__next_f\.push\((\[.*\])\)',raw,re.S)
            if not m:continue
            try:data=json.loads(m.group(1))[1]
            except (ValueError,TypeError,IndexError):continue
            if not isinstance(data,str):continue
            for m in re.finditer(r'\{"id":\d+',data):
                try:obj,_=json.JSONDecoder().raw_decode(data[m.start():])
                except ValueError:continue
                if obj.get('website') and matching_title(event['title'],obj.get('title','')):links.append(obj['website'])
    return list(dict.fromkeys(u for u in links if urlsplit(u).scheme in ('http','https')))[:3]

def program_links(soup,url,event):
    links=[];year=event['starts_at'][:4]
    for a in soup.select('a[href]'):
        raw=a['href'];label=clean(a.get_text(' ',strip=True));href=urljoin(url,raw)
        if not raw or raw.startswith('#') or urlsplit(href).scheme not in ('http','https'):continue
        years=set(re.findall(r'\b20\d{2}\b',label+' '+urlsplit(href).path))
        if years and year not in years:continue
        if SKIP.search(label+' '+href):continue
        if not PROGRAM.search(label):continue
        # HTML subpages stay on the organiser. PDF programmes may use its CDN.
        if not same_organisation(href,url) and '.pdf' not in urlsplit(href).path.lower():continue
        if canonical_url(href)==canonical_url(url):continue
        base_path=urlsplit(url).path.strip('/').split('/')
        target_path=urlsplit(href).path.strip('/').split('/')
        # An event landing must not inherit another event's programme from global navigation.
        if base_path[0] and target_path[0]!=base_path[0] and re.search(r'20\d{2}',base_path[0]):
            if re.search(r'20\d{2}',target_path[0]) or not re.search(r'program|speaker|lectur|schedule',target_path[0],re.I):continue
        links.append(href)
    page_heading=' '.join(clean(n.get_text(' ',strip=True)) for n in soup.select('title,h1'))
    if PROGRAM.search(page_heading):
        for frame in soup.select('iframe[src]'):
            href=urljoin(url,frame['src'])
            if urlsplit(href).scheme not in ('http','https'):continue
            if re.search(r'google|youtube|yandex|vimeo|recaptcha|doubleclick|vk\.com',host(href),re.I):continue
            links.append(href)
    return sorted(set(links),key=lambda u:(0 if re.search('speaker|lector|lectur|spiker',u,re.I) else 1,u))[:5]

class Fetcher:
    def __init__(self,client):
        self.client=client;self.gates={};self.failures=Counter();self.memo={};self.listings={};self.indexes={};self.network=0;self.cached=0
        self.cache=ROOT/'.cache'/'international';self.cache.mkdir(parents=True,exist_ok=True)
    async def get(self,url):
        key=canonical_url(url)
        if key in self.memo:return await self.memo[key]
        task=asyncio.create_task(self._get(url));self.memo[key]=task
        return await task
    async def _get(self,url):
        h=host(url);gate=self.gates.setdefault(h,asyncio.Semaphore(2));key=hashlib.sha256(url.encode()).hexdigest()[:20]
        metadata=self.cache/(key+'.json');binary=self.cache/(key+'.bin')
        if metadata.exists() and binary.exists() and time.time()-metadata.stat().st_mtime<21600:
            self.cached+=1;meta=json.loads(metadata.read_text(encoding='utf-8'));return meta,binary.read_bytes()
        old=ROOT/'.cache'/'pages'/(key+'.html')
        if old.exists() and time.time()-old.stat().st_mtime<21600:
            self.cached+=1;return {'url':url,'content_type':'text/html','cached':True},old.read_bytes()
        async with gate:
            if self.failures[h]>=3:raise RuntimeError('host_unavailable')
            try:
                self.network+=1
                response=await self.client.get(url);response.raise_for_status()
                if len(response.content)>18_000_000:raise RuntimeError('file_too_large')
                meta={'url':str(response.url),'content_type':response.headers.get('content-type',''),'at':stamp()}
                binary.write_bytes(response.content);atomic_text(metadata,json.dumps(meta))
                return meta,response.content
            except (httpx.TransportError,httpx.HTTPStatusError) as exc:
                if not isinstance(exc,httpx.HTTPStatusError) or exc.response.status_code in {401,403,429,502,503,504}:self.failures[h]+=1
                raise

async def expomap_index(fetcher):
    root='https://expomap.ru/expo/country/russia/'
    queue=[root];seen=set();events=[]
    while queue and len(seen)<12:
        url=queue.pop(0)
        if url in seen:continue
        seen.add(url)
        try:meta,data=await fetcher.get(url)
        except Exception:continue
        soup=BeautifulSoup(data.decode('utf-8',errors='replace'),'lxml')
        events.extend(schema_events(soup))
        for a in soup.select('a[href]'):
            href=urljoin(url,a['href'])
            if urlsplit(href).path==urlsplit(root).path and re.search(r'[?&]page=\d+',href) and href not in seen and href not in queue:queue.append(href)
        queue.sort(key=lambda u:int((re.search(r'[?&]page=(\d+)',u) or [None,'1'])[1]))
    return events

def parse_document(meta,data,event,primary=False):
    url=meta['url'];result={'url':url,'evidence':[],'links':[],'official':[],'matched':False,'reason':''}
    if 'pdf' in meta.get('content_type','') or data.startswith(b'%PDF'):
        from pypdf import PdfReader
        doc=PdfReader(io.BytesIO(data));text='\n'.join(p.extract_text() or '' for p in doc.pages[:60])
        if not text.strip():return {**result,'reason':'pdf_needs_ocr'}
        years=set(re.findall(r'\b20\d{2}\b',text[:2500]));year=event['starts_at'][:4]
        if years and year not in years:return {**result,'reason':'edition_mismatch'}
        from html import escape
        html='<title>Программа '+year+'</title>'+''.join('<p>'+escape(x)+'</p>' for x in re.split(r'\n\s*\n',text))
        # Use short contextual windows when PDF text has no paragraph boundaries.
        for m in re.finditer(r'перевод|translation|interpretation|язык доклада|language of',text,re.I):
            html+='<p>'+escape(text[max(0,m.start()-120):m.end()+220])+'</p>'
        result['evidence']=related_evidence(html,url,event);result['matched']=True;return result
    encoding='utf-8'
    charset=re.search(br'charset\s*=\s*["\x27]?([a-zA-Z0-9-]+)',data[:5000])
    if charset and charset.group(1).lower() in {b'windows-1251',b'cp1251',b'koi8-r',b'utf-8'}:encoding=charset.group(1).decode('ascii')
    html=data.decode(encoding,errors='replace');soup=BeautifulSoup(html,'lxml')
    heading=clean(soup.title.get_text(' ',strip=True)) if soup.title else ''
    if re.search(r'access denied|страница не найдена|page not found|captcha|just a moment',heading,re.I):return {**result,'reason':'challenge_or_missing'}
    if primary:
        result['official']=official_links(soup,url,event)
        facts=extract_facts(html,url,event);result['matched']=facts.get('ok',False);result['reason']=facts.get('reason','')
        if not result['matched']:return result
        result['evidence']=facts.get('language_evidence',[])
        result['facts']=facts
    else:
        headings=' '.join(n.get_text(' ',strip=True) for n in soup.select('title,h1'))
        years=set(re.findall(r'\b20\d{2}\b',headings+' '+urlsplit(url).path))
        if years and event['starts_at'][:4] not in years:return {**result,'reason':'edition_mismatch'}
        result['evidence']=related_evidence(html,url,event);result['matched']=True
    result['links']=program_links(soup,url,event)
    return result

async def review_event(event,fetcher):
    row={'id':event['id'],'title':event['title'],'pages':[],'errors':[],'evidence':[],'state':'unresolved_url'}
    urls=list(dict.fromkeys([event.get('organizer_url',''),*event.get('source_urls',[])]))
    urls=[u for u in urls if urlsplit(u).scheme in ('http','https')]
    details=[u for u in urls if not is_hub(u)]
    if not details and any(host(u)=='expomap.ru' for u in urls):
        if 'expomap' not in fetcher.indexes:fetcher.indexes['expomap']=asyncio.create_task(expomap_index(fetcher))
        candidates=await fetcher.indexes['expomap']
        details=[o['url'] for o in candidates if o.get('url') and str(o.get('startDate',''))[:10]==event['starts_at'] and matching_title(event['title'],o.get('name',''))]
    # Recover an official URL from a Crocus listing instead of skipping the event.
    if not details and any(host(u)=='crocus-expo.ru' for u in urls):
        for u in urls[:1]:
            try:
                meta,data=await fetcher.get(u)
                src={**next(s for s in load_sources('event') if s['id']=='crocus_expo'),'url':u}
                if u not in fetcher.listings:fetcher.listings[u]=asyncio.create_task(asyncio.to_thread(parse_html,src,data.decode('utf-8',errors='replace')))
                candidates=await fetcher.listings[u]
                details=[i.url for i in candidates if matching_title(event['title'],i.title) and i.published_at and str(i.published_at.date())==event['starts_at']]
            except Exception as exc:row['errors'].append({'url':u,'error':type(exc).__name__})
    seen=set();programme_count=0;matched=False
    queue=[(u,True,0) for u in details[:2]]
    while queue and len(seen)<9:
        url,primary,depth=queue.pop(0);key=canonical_url(url)
        if key in seen:continue
        seen.add(key)
        try:
            meta,data=await fetcher.get(url)
            result=await asyncio.to_thread(parse_document,meta,data,event,primary)
            row['pages'].append({'url':meta['url'],'matched':result['matched'],'reason':result['reason'],'type':meta.get('content_type',''),'primary':primary})
            row['evidence'].extend(result['evidence'])
            if result['matched']:
                matched=True
                if primary and host(meta['url']) not in AGGREGATORS:row['resolved_official_url']=meta['url']
                if not primary:programme_count+=1
                if depth<2:
                    queue.extend((u,False,depth+1) for u in result['links'])
            # An aggregator can expose the official link even when its title is abbreviated.
            queue[0:0]=[(u,True,depth+1) for u in result['official'] if depth<2 and canonical_url(u) not in seen]
        except Exception as exc:
            row['errors'].append({'url':url,'error':type(exc).__name__,'detail':str(exc)[:160]})
    proof=list({(p['url'],p['text']):p for p in row['evidence']}.values());row['evidence']=proof
    verdict=assess_translation(event,proof)
    row['classification']=verdict;row['at']=stamp();row['programme_pages']=programme_count
    if verdict['audience']=='intl':row['state']='qualified'
    elif programme_count:row['state']='reviewed_no_signal'
    elif matched:row['state']='no_programme_found'
    elif row['errors']:row['state']='unavailable'
    elif row['pages']:row['state']='identity_or_edition_mismatch'
    return finalize_review(event,row)

async def scan(events,limit=None,resume=True):
    ledger_path=ROOT/'audit'/'international-coverage.json'
    ledger=json.loads(ledger_path.read_text(encoding='utf-8')) if resume and ledger_path.exists() else {}
    targets=[e for e in events if e.get('country') in {'Россия','РФ','Russia','Russian Federation'} and e.get('ends_at','')>=today().isoformat()]
    targets.sort(key=lambda e:(0 if re.search(r'международ|international|world|зарубеж|иностран|спикер',e['title']+' '+e.get('description',''),re.I) else 1,e['starts_at']))
    if limit is not None:targets=targets[:limit]
    sem=asyncio.Semaphore(12);done=0
    async with httpx.AsyncClient(timeout=httpx.Timeout(12,connect=4),follow_redirects=True,headers={'User-Agent':USER_AGENT},limits=httpx.Limits(max_connections=24)) as client:
        fetcher=Fetcher(client)
        async def one(e):
            nonlocal done
            async with sem:
                old=ledger.get(e['id'])
                if old and old.get('version')==RULE_VERSION and old.get('at','')[:10]==today().isoformat() and old.get('fingerprint',fingerprint(e))==fingerprint(e) and e.get('language_checked_at','')<=old.get('at',''):row=finalize_review(e,old)
                else:row=await review_event(e,fetcher);row['version']=RULE_VERSION;ledger[e['id']]=row
                e['translation_review']={k:row.get(k) for k in ('state','at','programme_pages')}
                e['language_rule_version']=RULE_VERSION;e['language_checked_at']=row.get('at')
                if row['state'] in {'qualified','reviewed_no_signal','no_programme_found','outside_scope','partially_unavailable'}:
                    e['language_evidence']=row['evidence'];e.update(row['classification'])
                if row.get('resolved_official_url'):
                    e['source_urls']=list(dict.fromkeys(e.get('source_urls',[e.get('organizer_url','')])+[row['resolved_official_url']]))
                    e['organizer_url']=canonical_url(row['resolved_official_url']);row['fingerprint']=fingerprint(e)
                done+=1
                if done%25==0:
                    atomic_text(ledger_path,json.dumps(ledger,ensure_ascii=False,indent=2))
                    print(json.dumps({'done':done,'total':len(targets),'states':dict(Counter(x['state'] for x in ledger.values())),'network':fetcher.network,'cache':fetcher.cached},ensure_ascii=False),flush=True)
        await asyncio.gather(*(one(e) for e in targets))
        atomic_text(ledger_path,json.dumps(ledger,ensure_ascii=False,indent=2))
        stats={'at':stamp(),'targeted':len(targets),'states':dict(Counter(ledger[e['id']]['state'] for e in targets)),
               'network_requests':fetcher.network,'cached_pages':fetcher.cached,'unavailable_hosts':dict(fetcher.failures)}
    atomic_text(ROOT/'audit'/'international-scan-summary.json',json.dumps(stats,ensure_ascii=False,indent=2));return stats

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int);p.add_argument('--fresh',action='store_true');args=p.parse_args()
    events=json.loads((ROOT/'events_upcoming.json').read_text(encoding='utf-8'))
    original_fingerprints={e['id']:fingerprint(e) for e in events}
    stats=asyncio.run(scan(events,args.limit,not args.fresh))
    report_path=ROOT/'audit'/'refresh-latest.json';report=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    # A long review must not replace edits/new events saved by another request.
    current=json.loads((ROOT/'events_upcoming.json').read_text(encoding='utf-8'));scanned={e['id']:e for e in events}
    fields=('translation_review','language_rule_version','language_checked_at','language_evidence','audience','translation_status','translation_evidence','source_urls','organizer_url')
    for e in current:
        reviewed=scanned.get(e['id'])
        if reviewed and original_fingerprints.get(e['id'])==fingerprint(e):
            for field in fields:
                if field=='source_urls':e[field]=list(dict.fromkeys(e.get(field,[])+reviewed.get(field,[])))
                elif field in reviewed:e[field]=reviewed[field]
    stats['catalog']=write_catalog(current,extra_meta={'source_errors':report.get('source_errors',0),'international_reviewed':stats['targeted']})
    print(json.dumps(stats,ensure_ascii=False,indent=2))
