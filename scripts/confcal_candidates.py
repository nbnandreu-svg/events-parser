"""Discover Russian IT listings for organizer verification before publication."""
import json
import time
from datetime import timedelta

import httpx

from catalog_core import ROOT, atomic_text, today
from datetime import date
from urllib.parse import urlsplit


def verified_rows(path):
    candidates = {r['id']:r for r in json.loads((ROOT/'.cache/confcal-candidates.json').read_text(encoding='utf-8'))}
    rows = []
    for review in json.loads(path.read_text(encoding='utf-8')):
        candidate = candidates[review['candidate_id']]
        evidence = review['date_evidence'].strip()
        start, end = map(date.fromisoformat, (review['starts_at'], review['ends_at']))
        url = urlsplit(review['organizer_url'])
        if url.scheme != 'https' or not url.hostname or not evidence or end < start:
            raise ValueError('Invalid organizer verification')
        rows.append(dict(title=review['title'], starts_at=start.isoformat(), ends_at=end.isoformat(),
                         country=review['country'], city=review['city'], type=review['type'],
                         vertical='it', source='confcal_verified', organizer_url=review['organizer_url'],
                         source_urls=[review['organizer_url'], f"https://confcal.rpa.icu/event/{candidate['id']}"],
                         date_evidence=evidence, date_status='confirmed'))
    return rows


def discover():
    endpoint = 'https://confcal.rpa.icu/mcp'
    headers = {'Accept': 'application/json, text/event-stream'}
    with httpx.Client(headers=headers, timeout=25) as client:
        def request(method, params, number=None):
            payload = {'jsonrpc':'2.0', 'method':method, 'params':params}
            if number is not None:
                payload['id'] = number
            response = client.post(endpoint, json=payload)
            response.raise_for_status()
            if response.headers.get('mcp-session-id'):
                client.headers['mcp-session-id'] = response.headers['mcp-session-id']
            time.sleep(1.1)
            if not response.content:
                return {}
            if 'text/event-stream' in response.headers.get('content-type', ''):
                result = json.loads(next(line[5:] for line in response.text.splitlines() if line.startswith('data:')))
            else:
                result = response.json()
            if result.get('error') or result.get('result', {}).get('isError'):
                raise ValueError('ConfCal could not complete ' + method)
            return result['result']

        request('initialize', {'protocolVersion':'2025-06-18', 'capabilities':{},
                'clientInfo':{'name':'events-parser','version':'1'}}, 1)
        request('notifications/initialized', {})
        records = {}
        offset = 0
        while True:
            arguments = {'topics':['devops','data','security','ai'], 'limit':50, 'offset':offset,
                         'date_from':today().isoformat(), 'date_to':(today()+timedelta(days=90)).isoformat()}
            result = request('tools/call', {'name':'search_events','arguments':arguments}, offset+2)
            page = json.loads(result['content'][0]['text'])['items']
            previous = len(records)
            records.update({item['id']:item for item in page})
            if len(page) < 50:
                break
            if previous == len(records):
                raise ValueError('ConfCal repeated a page')
            offset += len(page)
    return list(records.values())


if __name__ == '__main__':
    rows = discover()
    atomic_text(ROOT/'.cache/confcal-candidates.json', json.dumps(rows,ensure_ascii=False))
    print(f'{len(rows)} candidates for verification')
