"""Fill missing catalog descriptions from the matching public event page."""
import argparse
import asyncio
from collections import defaultdict
import json
from urllib.parse import urlsplit

import httpx

from catalog_core import ROOT, canonical_url, is_hub, short_summary, stamp, write_catalog
from event_engine import USER_AGENT
from page_facts import extract_facts
from source_transport import decode_page


async def fill(limit):
    path = ROOT / 'events_upcoming.json'
    records = json.loads(path.read_text(encoding='utf-8'))
    candidates = [row for row in records if not row.get('description')]
    candidates.sort(key=lambda row: (row['country'] != 'Россия', row['vertical'] == 'other', row['starts_at']))
    if limit:
        candidates = candidates[:limit]
    gate = asyncio.Semaphore(18)
    hosts = defaultdict(lambda: asyncio.Semaphore(3))
    status = {}
    recovered = {}
    skipped_hosts = set()

    async def read(row, client):
        listed = [row.get('organizer_url'), *(row.get('source_urls') or [])]
        urls = list(dict.fromkeys(canonical_url(u) for u in listed if u))
        initial_count = len(urls)
        for url in urls:
            host = urlsplit(url).netloc
            if not host or host in skipped_hosts or is_hub(url):
                continue
            try:
                async with hosts[host], gate:
                    if host in skipped_hosts:
                        continue
                    response = await client.get(url)
                    if response.status_code == 429:
                        skipped_hosts.add(host)
                        continue
                    response.raise_for_status()
                    if 'html' not in response.headers.get('content-type',''):
                        continue
                    facts = extract_facts(decode_page(response.content), str(response.url), row)
                    summary = short_summary(facts.get('description',''),row['title'])
                    if facts.get('ok') and summary:
                        recovered[row['id']] = dict(description=summary, description_source=str(response.url),
                                                     description_checked_at=stamp())
                        return
                    official = canonical_url(facts.get('official_url'))
                    if facts.get('ok') and official and official not in urls and len(urls) == initial_count:
                        urls.append(official)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
                status[host] = type(error).__name__

    async with httpx.AsyncClient(follow_redirects=True,timeout=httpx.Timeout(12,connect=5),
                                 headers={'User-Agent':USER_AGENT}) as client:
        tasks = [read(row, client) for row in candidates]
        for number, task in enumerate(asyncio.as_completed(tasks), 1):
            await task
            if number % 250 == 0:
                print(f'Checked {number}/{len(tasks)}, found {len(recovered)}', flush=True)
    if recovered:
        current = json.loads(path.read_text(encoding='utf-8'))
        for row in current:
            if row['id'] in recovered and not row.get('description'):
                row.update(recovered[row['id']])
        audit = ROOT/'audit/refresh-latest.json'
        meta = json.loads(audit.read_text(encoding='utf-8')) if audit.exists() else {}
        write_catalog(current,extra_meta={'source_errors':meta['source_errors']} if 'source_errors' in meta else {})
    print(f'Checked {len(candidates)}, filled {len(recovered)}. Rate-limited hosts: {sorted(skipped_hosts)}. Errors: {status}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=0)
    asyncio.run(fill(parser.parse_args().limit))
