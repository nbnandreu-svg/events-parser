#!/usr/bin/env python3
"""Fetch configured sources, verify detail pages, merge and export one catalog."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from catalog_core import ROOT, CATALOG_LOCK, today, stamp, canonical_url, same_edition, short_summary, write_catalog, atomic_text, assess_translation,prepare_catalog
from event_engine import run_parse, USER_AGENT
from page_facts import extract_facts, related_evidence

def convert(item):
    topics=[t.split(':',1)[1] for t in item.tags if t.startswith('topic:')]
    return dict(title=item.title,starts_at=item.published_at.date().isoformat(),ends_at=(item.date_end or item.published_at).date().isoformat(),
        city=item.location,country=item.country if item.country is not None else ('Россия' if item.region=='РФ' else ''),vertical=(topics or ['industry'])[0],type=item.event_type or 'Мероприятие',
        date_status=item.date_certainty,organizer_url=item.url,source=item.source_id,description=short_summary(item.summary,item.title),
        date_evidence=item.date_evidence,source_checked_at=stamp())

def merge_live(existing,incoming):
    rows=[dict(e) for e in existing];added=updated=0
    for item in incoming:
        old=next((e for e in rows if same_edition(e,item)),None)
        if old is None:rows.append(item);added+=1;continue
        for key in ('source_urls','sources'):
            field='organizer_url' if key=='source_urls' else 'source'
            old[key]=list(dict.fromkeys(old.get(key,[old.get(field,'')])+item.get(key,[item.get(field,'')])))
        if item.get('source','').endswith('_official'):
            old['organizer_url']=item['organizer_url']
        for key in ('date_evidence','source_checked_at','parent_event_url'):
            if item.get(key):old[key]=item[key]
        if item.get('vertical') and item.get('source') in {'ict2go','conferos','tadviser_calendar','ict_moscow','cnews_conferences','comnews_conferences'}:
            old['vertical']=item['vertical']
        if item.get('description') and (item.get('description_checked_at') or not old.get('description')):old['description']=item['description']
        for key in ('description_source','description_checked_at','language_evidence'):
            if item.get(key):old[key]=item[key]
        old.update(assess_translation(old));updated+=1
    return rows,added,updated

async def enrich_rows(events,limit=100,ids=None):
    from catalog_core import is_hub
    candidates=[e for e in events if e.get('organizer_url','').startswith('http') and (e.get('ends_at') or '')>=today().isoformat()]
    if ids:candidates=[e for e in candidates if str(e.get('id')) in ids]
    candidates=[e for e in candidates if not is_hub(e['organizer_url'])]
    import re
    candidates.sort(key=lambda e:(e.get('language_checked_at','')[:10]==today().isoformat(),e.get('language_rule_version',0)>=2,bool(e.get('description_checked_at')),0 if e.get('source') in {'tadviser_calendar','conferos','tadviser'} else 1,
                                   0 if re.search('международ|спикер|перевод|speaker',e.get('title','')+' '+e.get('description',''),re.I) else 1,
                                   bool(e.get('description')),e.get('starts_at','')))
    candidates=candidates[:limit]
    sem=asyncio.Semaphore(5);hosts={};log=[];cache=ROOT/'.cache'/'pages';cache.mkdir(parents=True,exist_ok=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(18,connect=7),follow_redirects=True,headers={'User-Agent':USER_AGENT}) as client:
        async def one(e):
            async with sem:
                url=e['organizer_url'];gate=hosts.setdefault(urlsplit(url).netloc,asyncio.Semaphore(2));result={'title':e['title'],'url':url}
                try:
                    async with gate:r=await client.get(url)
                    result['http_status']=r.status_code;r.raise_for_status()
                    final=str(r.url);key=hashlib.sha256(url.encode()).hexdigest()[:20]
                    (cache/(key+'.html')).write_text(r.text,encoding='utf-8')
                    result['snapshot']=str(Path('.cache/pages')/(key+'.html'))
                    facts=extract_facts(r.text,final,e);result.update(ok=facts['ok'],reason=facts.get('reason',''))
                    if facts.get('ok'):
                        for related in facts.get('related_urls',[]):
                            try:
                                async with gate:pr=await client.get(related)
                                pr.raise_for_status()
                                facts['language_evidence']+=related_evidence(pr.text,str(pr.url),e)
                                result.setdefault('program_pages',[]).append(str(pr.url))
                            except httpx.HTTPError:continue
                    official=facts.get('official_url') if facts.get('ok') else ''
                    if official and canonical_url(official)!=canonical_url(final):
                        try:
                            extra_response=await client.get(official);extra_response.raise_for_status()
                            extra=extract_facts(extra_response.text,str(extra_response.url),e)
                            if extra.get('ok'):
                                for field in ('description','description_source','description_checked_at','city','country','date_start','date_end','date_evidence'):
                                    if extra.get(field):facts[field]=extra[field]
                                facts['language_evidence']+=extra.get('language_evidence',[])
                                for related in extra.get('related_urls',[]):
                                    try:
                                        pr=await client.get(related);pr.raise_for_status()
                                        facts['language_evidence']+=related_evidence(pr.text,str(pr.url),e)
                                        result.setdefault('program_pages',[]).append(str(pr.url))
                                    except httpx.HTTPError:continue
                                result['official_checked']=str(extra_response.url)
                        except httpx.HTTPError:pass
                    if facts.get('ok'):
                        if facts.get('description'):
                            for k in ('description','description_source','description_checked_at'):e[k]=facts[k]
                            result['summary_chars']=len(facts['description'])
                        if facts.get('language_evidence'):e['language_evidence']=facts['language_evidence']
                        e['language_checked_at']=stamp();e['language_rule_version']=2
                        if facts.get('child_event_urls'):e['child_event_urls']=facts['child_event_urls']
                        if facts.get('city'):e['city']=facts['city']
                        if facts.get('country') in ('RU','Россия','Russia'):e['country']='Россия'
                        if facts.get('date_start'):
                            if e['starts_at']!=facts['date_start']:e['date_conflict']={'listing':e['starts_at'],'detail':facts['date_start'],'url':final}
                            e['starts_at']=facts['date_start'];e['ends_at']=facts.get('date_end') or facts['date_start']
                            e['date_evidence']=facts['date_evidence'];e['date_status']='confirmed'
                        e['source_urls']=list(dict.fromkeys(e.get('source_urls',[url])+[url,canonical_url(final)]))
                        e['organizer_url']=canonical_url(final);e.update(assess_translation(e))
                    e['detail_check']={'at':stamp(),'ok':facts['ok'],'reason':facts.get('reason','')}
                except Exception as exc:
                    result.update(ok=False,reason=type(exc).__name__,error=str(exc)[:200])
                    e['detail_check']={'at':stamp(),'ok':False,'reason':type(exc).__name__}
                log.append(result)
        await asyncio.gather(*(one(e) for e in candidates))
    return log

async def refresh_async(source_ids=None,enrich_limit=120,offline=False,review_international=True):
    path=ROOT/'events_upcoming.json';existing=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    existing=[e for e in existing if (e.get('ends_at') or '')>=today().isoformat()];sources=[];incoming=[]
    if not offline:
        items,results=await run_parse(source_ids,kind='event');sources=[r.model_dump(mode='json') for r in results]
        incoming=[convert(i) for i in items if i.published_at]
    events,added,updated=merge_live(existing,incoming)
    events,quality_removed=prepare_catalog(events)
    details=await enrich_rows(events,enrich_limit) if enrich_limit and not offline else []
    international_review={}
    if review_international and not offline:
        from international_scan import scan
        international_review=await scan(events,resume=True)
    parents={canonical_url(url):e for e in events for url in e.get('child_event_urls',[])}
    for e in events:
        parent=parents.get(canonical_url(e.get('organizer_url')))
        if parent and parent['starts_at']==e['starts_at']:e['parent_event_url']=parent['organizer_url']
    for e in events:
        if e.get('source')=='tadviser_calendar' and not e.get('description_checked_at'):e['date_status']='tentative'
    prior_path=ROOT/'audit'/'refresh-latest.json'
    prior=json.loads(prior_path.read_text(encoding='utf-8')) if prior_path.exists() else {}
    runs=prior.get('source_runs') or {s['source_id']:s for s in prior.get('sources',[])}
    runs.update({s['source_id']:{**s,'checked_at':stamp()} for s in sources})
    report={'at':stamp(),'sources':sources,'source_runs':runs,'details':details,'international_review':international_review,'added':added,'updated':updated,'before':len(existing),
            'duplicates_merged':sum(x['reason']=='duplicate' for x in quality_removed),'quality_exclusions':len(quality_removed),
            'source_errors':sum(bool(s.get('error')) for s in runs.values()),'empty_sources':[s['source_id'] for s in runs.values() if not s['fetched']]}
    with CATALOG_LOCK:
        current=json.loads(path.read_text(encoding='utf-8')) if path.exists() else [];by_id={e.get('id'):e for e in current if e.get('id')}
        for e in events:
            cur=by_id.get(e.get('id'))
            if cur and cur.get('description_checked_at','')>e.get('description_checked_at',''):
                for key in ('description','description_source','description_checked_at','language_evidence'):
                    if key in cur:e[key]=cur[key]
        meta=write_catalog(events,extra_meta={'source_errors':report['source_errors']})
    report.update(meta);atomic_text(ROOT/'audit'/'refresh-latest.json',json.dumps(report,ensure_ascii=False,indent=2));return report

def refresh(**kwargs):return asyncio.run(refresh_async(**kwargs))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sources',nargs='*');p.add_argument('--enrich-limit',type=int,default=120);p.add_argument('--offline',action='store_true')
    args=p.parse_args();r=refresh(source_ids=args.sources,enrich_limit=args.enrich_limit,offline=args.offline)
    print(json.dumps({k:v for k,v in r.items() if k not in ('details','sources','source_runs')},ensure_ascii=False,indent=2))
