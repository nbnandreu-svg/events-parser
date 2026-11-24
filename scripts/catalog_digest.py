"""List new, still relevant editions between two published catalog revisions."""
import argparse
import hashlib
from collections import defaultdict
from datetime import date
import json
import re
import subprocess

from catalog_core import ROOT, atomic_text, canonical_url, is_hub, same_edition, title_key, today
from venue_rules import venue_country


TOPICS = ('it', 'apk', 'industry')


def snapshot(revision):
    commit = subprocess.check_output(
        ['git', 'rev-parse', '--verify', '--end-of-options', revision + '^{commit}'],
        cwd=ROOT, text=True,
    ).strip()
    raw = subprocess.check_output(['git', 'show', f'{commit}:events_upcoming.json'], cwd=ROOT)
    return json.loads(raw)


def edition_links(row):
    return {canonical_url(url) for url in [row.get('organizer_url'), *(row.get('source_urls') or [])]
            if url and not is_hub(url)}


def listing_variant(left, right):
    if same_edition(left, right):
        return True
    if left.get('country') and right.get('country') and left['country'] != right['country']:
        return False
    cities = [r.get('city', '').casefold().strip() for r in (left, right)]
    if all(cities) and cities[0] != cities[1] and 'онлайн' not in cities and not any(
            re.search(r'\d|,|\b(?:ул|проспект|улица)\b', city) for city in cities):
        return False
    a, b = sorted((title_key(r['title']) for r in (left, right)), key=len)
    return a == b or (len(a) >= 8 and (b.startswith(a + ' ') or b.endswith(' ' + a)))


def additions(previous, current, topics=TOPICS, as_of=None):
    cutoff = (as_of or today()).isoformat()
    ids = set()
    by_date = defaultdict(list)
    by_link = defaultdict(list)

    def remember(row):
        if row.get('id'):
            ids.add(row['id'])
        by_date[row['starts_at']].append(row)
        for link in edition_links(row):
            by_link[link].append(row)

    for row in previous:
        remember({**row, 'country': venue_country(row)})
    current_ids = {row['id'] for row in current if row.get('id')}
    result = []
    for row in sorted(current, key=lambda r: (r['starts_at'], r['title'])):
        row = {**row, 'country': venue_country(row)}
        if row.get('vertical') not in topics or row['ends_at'] < cutoff:
            continue
        if row.get('id') in ids or any(listing_variant(old, row) for old in by_date[row['starts_at']]):
            continue
        # Stable dedicated event links also identify a short postponement.
        # Annual editions often reuse the same site, so keep distant dates distinct.
        start = date.fromisoformat(row['starts_at'])
        if any(old.get('id') not in current_ids
               and title_key(old['title']) == title_key(row['title'])
               and abs((start - date.fromisoformat(old['starts_at'])).days) <= 35
               for link in edition_links(row) for old in by_link[link]):
            continue
        result.append({key: row.get(key, '') for key in (
            'id', 'vertical', 'title', 'starts_at', 'ends_at', 'city', 'country', 'priority_evidence',
            'date_status', 'organizer_url', 'description')})
        remember(row)
    return result


def mail_groups(rows, topics=TOPICS):
    domestic = {topic: [] for topic in topics}
    foreign = []
    for row in rows:
        country = row.get('country')
        if country in {'Россия', 'РФ', 'RU', 'Russia', 'Russian Federation'}:
            topic = row.get('vertical')
            if topic in domestic and len(domestic[topic]) < 6:
                domestic[topic].append(row)
        elif country and row.get('priority_evidence'):
            foreign.append(row)
    return {'russia': {topic: events for topic, events in domestic.items() if events}, 'abroad': foreign}


def write_release(rows, folder):
    ids = sorted({r['id'] for r in rows})
    if not ids:
        return None
    payload = json.dumps({'ids': ids}, separators=(',', ':'))
    key = hashlib.sha256(payload.encode()).hexdigest()[:12]
    atomic_text(folder / (key + '.json'), payload)
    atomic_text(folder / 'latest.json', payload)
    return key


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', required=True)
    parser.add_argument('--until', default='HEAD')
    parser.add_argument('--topics', choices=TOPICS, nargs='+', default=TOPICS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--write-release', action='store_true',
                      help='Publish the selection against the working catalog before committing.')
    mode.add_argument('--mail', action='store_true', help='Group events for the email digest.')
    args = parser.parse_args()
    current = json.loads((ROOT/'events_upcoming.json').read_text(encoding='utf-8')) if args.write_release else snapshot(args.until)
    rows = additions(snapshot(args.since), current, args.topics)
    if args.write_release:
        key = write_release(rows, ROOT/'releases')
        print(json.dumps({'release':key,'count':len(rows)}))
    else:
        print(json.dumps(mail_groups(rows, args.topics) if args.mail else rows, indent=2, ensure_ascii=False))
