"""Source-specific list readers; importing this file makes no network calls."""
import json
import re
from datetime import datetime
from urllib.parse import urljoin

def expoafisha_cards(source, soup):
    from catalog_core import today
    from event_engine import CITY_HINTS, MONTHS_RU, make_item
    from venue_rules import venue_country

    months = {name[:3]: index for index, name in enumerate(MONTHS_RU, 1)}
    rows = []
    for card in soup.select('.ev'):
        link = card.select_one('h3 a[href]')
        dates = card.select_one('.ev-date')
        if not link or not dates:
            continue
        raw = dates.get_text(' ', strip=True).lower()
        match = re.fullmatch(r'(\d{1,2})(?:\s+([а-я]+))?(?:\s*[–—-]\s*(\d{1,2})\s+([а-я]+))?(?:\s+(20\d{2}))?', raw)
        if not match:
            continue
        first, month, last, end_month, year = match.groups()
        start_m = months.get((month or end_month or '')[:3])
        end_m = months.get((end_month or month or '')[:3])
        if not start_m or not end_m:
            continue
        group = card.find_parent('section', class_='kl-grp')
        heading = group.select_one('h2') if group else None
        group_year = re.search(r'\b20\d{2}\b', heading.get_text()) if heading else None
        start_y = int(group_year.group()) if group_year else today().year
        end_y = int(year) if year else start_y + int(end_m < start_m)
        if year and not group_year:
            start_y = end_y - int(end_m < start_m)
        try:
            start = datetime(start_y, start_m, int(first))
            end = datetime(end_y, end_m, int(last or first))
        except ValueError:
            continue
        if end < start:
            continue
        venue = card.select_one('.v')
        city = venue.get_text(' ', strip=True).split('·')[0].strip() if venue else ''
        label = card.select_one('.bdg--format')
        row = make_item(source=source, title=link.get_text(' ', strip=True), url=urljoin(source['url'], link['href']),
                        published_at=start, date_end=end, location=city, event_type=label.get_text(strip=True) if label else '',
                        date_evidence=f'{raw} ({start_y})')
        row.country = venue_country({'city': city}) or ('Россия' if city in CITY_HINTS else '')
        rows.append(row)
    return rows

def jugru_conferences(source, soup):
    from event_engine import make_item, parse_date

    payload = soup.select_one('script#__NEXT_DATA__')
    if not payload:
        return []
    page = json.loads(payload.get_text())['props']['pageProps']
    rows = []
    for season in [page.get('currentSeason') or {}, *(page.get('futureSeasons') or [])]:
        for conference in season.get('conferences', []):
            version = conference.get('version') or {}
            dates = version.get('dates') or {}
            start = parse_date(dates.get('startDate'))
            end = parse_date(dates.get('endDate')) or start
            title = (version.get('title') or {}).get('ru')
            url = (version.get('url') or {}).get('ru')
            if not (title and url and start and end >= start):
                continue
            place = ((version.get('venue') or {}).get('ru') or {}).get('city', '')
            formats = {p.get('type') for p in version.get('periods', [])}
            if not place and formats == {'online'}:
                place = 'Онлайн'
            rows.append(make_item(source=source, title=title, url=url, published_at=start,
                                  date_end=end, location=place, event_type='конференция',
                                  summary=(version.get('description') or {}).get('ru', ''),
                                  date_evidence=f'{start.date()} — {end.date()}'))
    return rows


def ite_exhibitions(source, soup):
    from event_engine import extract_event_dates, make_item

    rows = []
    seen = set()
    for title in soup.select('p.line-clamp-2.text-t1'):
        card = title.find_parent('div', class_='rounded-2xl')
        if not card:
            continue
        label = card.find('p', string='Даты')
        link = card.select_one('a[href^="https://"]')
        if not label or not link:
            continue
        raw = label.parent.get_text(' ', strip=True).replace('/', ' ')
        if not re.search(r'\b20\d{2}\b', raw):
            continue
        start, end = extract_event_dates(' '.join(raw.split()))
        if not start or (link['href'], start) in seen:
            continue
        seen.add((link['href'], start))
        city = label.parent.parent.find_next_sibling('div')
        city_label = city.select_one('p') if city else None
        description = title.find_next_sibling('p')
        rows.append(make_item(source=source, title=title.get_text(' ', strip=True),
                              url=link['href'], published_at=start, date_end=end,
                              location=city_label.get_text(strip=True) if city_label else '',
                              summary=description.get_text(' ', strip=True) if description else '',
                              event_type='выставка', date_evidence=raw))
    return rows


def parse_special(source,soup):
    from event_engine import clean_text, make_item, extract_event_dates
    sid=source['id']
    if sid == 'seymartec_official':
        heading = soup.select_one('.conference-head h1')
        info = next((n for n in soup.select('.conference-head .info') if re.search(r'\b20\d{2}\b', n.get_text(' ',strip=True))), None)
        if not heading or not info:
            return []
        parts = [p.strip() for p in info.get_text(' ', strip=True).split('|')]
        start, end = extract_event_dates(parts[0])
        if not start or not re.search(r'\b20\d{2}\b', parts[0]):
            return []
        from venue_rules import venue_country
        city = parts[-1] if len(parts) > 1 else ''
        row = make_item(source=source, title=heading.get_text(' ',strip=True), url=source['url'],
                        published_at=start, date_end=end, location=city, event_type='форум', date_evidence=parts[0])
        row.country = venue_country({'city':city}) or ('Россия' if city in {'Челябинск','Екатеринбург','Москва'} else '')
        return [row]
    if sid == 'dlg_official':
        import calendar
        months = {m:i for i,m in enumerate(calendar.month_name) if m}
        cities = {'Hanover':'Ганновер','Cologne':'Кельн','Hamburg':'Гамбург'}
        countries = {'Germany':'Германия','Belgium':'Бельгия','Netherlands':'Нидерланды'}
        rows = []
        for card in soup.select('.teaser-event'):
            link = card.select_one('.teaser__title a[href]')
            when = card.select_one('.teaser__date')
            if not link or not when:
                continue
            raw, _, place = when.get_text(' ',strip=True).partition('|')
            match = re.fullmatch(r'(\d{1,2})(?:\s+([A-Za-z]+))?(?:\s*[/–—-]\s*(\d{1,2}))?\s+([A-Za-z]+)\s+(20\d{2})', raw.strip())
            if not match:
                continue
            first, first_month, last, month, year = match.groups()
            if month not in months or (first_month and first_month not in months):
                continue
            start_month = months[first_month or month]
            start = datetime(int(year) - int(start_month > months[month]), start_month, int(first))
            end = datetime(int(year), months[month], int(last or first))
            city, _, country = place.strip().partition(',')
            badge = card.select_one('.badge')
            row = make_item(source=source, title=link.get_text(' ', strip=True), url=urljoin(source['url'],link['href']),
                            published_at=start,date_end=end,location=cities.get(city,city),
                            event_type='выставка' if badge and badge.get_text(strip=True)=='Fair' else 'конференция',date_evidence=raw)
            row.country = countries.get(country.strip(),country.strip()) or ('Германия' if city in cities else '')
            rows.append(row)
        return rows
    if sid == 'jugru_official':
        return jugru_conferences(source, soup)
    if sid == 'ite_group_official':
        return ite_exhibitions(source, soup)
    if sid == 'agravia':
        from event_engine import guess_location
        for node in soup.select('.t396__artboard .tn-atom'):
            raw = node.get_text(' ', strip=True)
            if not re.fullmatch(r'\d{1,2}(?:\s*[-–—]\s*\d{1,2})?\s+[а-яА-Я]+\s+20\d{2}', raw):
                continue
            board = node.find_parent(class_='t396__artboard').get_text(' ', strip=True)
            start, end = extract_event_dates(raw.lower())
            if start and 'AGRAVIA' in board.upper():
                return [make_item(source=source, title=f'AGRAVIA {start.year}', url=source['url'],
                                  published_at=start, date_end=end, location=guess_location(board),
                                  event_type='выставка', date_evidence=raw)]
        return []
    if sid=='bioprom_official':
        # The organiser publishes the event date in a Tilda hero, outside H1.
        for node in soup.select('strong,.tn-atom'):
            blob=clean_text(node.get_text(' ',strip=True))
            if len(blob)>100 or not re.search(r'\d{1,2}\s*[–—-]\s*\d{1,2}\s+октября\s+20\d{2}',blob,re.I):continue
            start,end=extract_event_dates(blob)
            if not start:continue
            row=make_item(source=source,title=f'БИОПРОМ {start.year}',url=source['url'],published_at=start,date_end=end,
                location='Геленджик',event_type='форум',date_evidence=blob)
            row.country='Россия'
            return [row]
        return []
    if sid.startswith('expomap_') or sid=='expoafisha':
        from page_facts import schema_events
        from event_engine import parse_date
        rows=[]
        formats={}
        if sid=='expoafisha':
            for card in soup.select('.ev'):
                link=card.select_one('h3 a[href]');label=card.select_one('.bdg--format')
                if link and label:formats[urljoin(source['url'],link['href']).split('#')[0]]=clean_text(label.get_text(' ',strip=True))
        for obj in schema_events(soup):
            start=parse_date(obj.get('startDate'));end=parse_date(obj.get('endDate')) or start
            if not start or not obj.get('name') or not obj.get('url'):continue
            loc=obj.get('location') or {};addr=loc.get('address') or {} if isinstance(loc,dict) else {}
            country=addr.get('addressCountry','') if isinstance(addr,dict) else ''
            if isinstance(country,dict):country=country.get('name','')
            from event_engine import guess_event_type_from_text
            item=make_item(source=source,title=clean_text(obj['name']),url=urljoin(source['url'],obj['url']),published_at=start,date_end=end,
                summary=clean_text(obj.get('description','')),location=addr.get('addressLocality','') if isinstance(addr,dict) else '',
                event_type=formats.get(urljoin(source['url'],obj['url']).split('#')[0]) or source.get('event_type') or guess_event_type_from_text(str(obj['name'])+' '+str(obj.get('description',''))),date_evidence=f"{obj['startDate']} {obj.get('endDate','')}")
            item.country='Россия' if country in ('RU','Russia','Россия') else country
            rows.append(item)
        if sid=='expoafisha' and not rows:
            return expoafisha_cards(source, soup)
        return rows
    if sid in {'calendario','kudabiz'}:
        from event_engine import guess_location,guess_event_type_from_text
        nodes=soup.select('.item-content__info' if sid=='calendario' else 'article.card')
        rows=[]
        for node in nodes:
            title_node=node.select_one('.item-content__title a' if sid=='calendario' else 'h3 a')
            if not title_node:continue
            date_node=node.select_one('.item-content__date' if sid=='calendario' else 'time,.card-date,.card-meta')
            blob=clean_text(date_node.get_text(' ',strip=True)) if date_node else clean_text(node.get_text(' ',strip=True))
            date_blob=re.sub(r'\b(?:пн|вт|ср|чт|пт|сб|вс),\s*','',blob,flags=re.I)
            start,end=extract_event_dates(date_blob)
            if not start:continue
            title=clean_text(title_node.get_text(' ',strip=True))
            desc=node.select_one('.item-content__description' if sid=='calendario' else '.card-description')
            city_node=node.select_one('.item-content__address' if sid=='calendario' else '.card-city')
            place=clean_text(city_node.get_text(' ',strip=True)).lstrip('📍 ') if city_node else ''
            city=guess_location(place) or (place if sid=='kudabiz' else '')
            if not city and sid=='calendario' and ' / ' in blob:city=guess_location(blob.split(' / ',1)[-1])
            if 'онлайн' in clean_text(node.get_text(' ',strip=True)).lower() and not city:city='Онлайн'
            labels=' '.join(x.get_text(' ',strip=True) for x in node.select('.item-content__nameplate,.card-cat'))
            rows.append(make_item(source=source,title=title,url=urljoin(source['url'],title_node['href']),published_at=start,date_end=end,
                location=city,summary=clean_text(desc.get_text(' ',strip=True)) if desc else '',event_type=guess_event_type_from_text(labels+' '+title+' '+blob),date_evidence=blob))
        return rows
    if sid=='confec_it':
        cards = soup.select('article.row')
        if cards:
            from event_engine import MONTHS_RU, guess_event_type_from_text
            months = {name[:3]: name for name in MONTHS_RU}
            rows = []
            for card in cards:
                if card.select_one('.badge-past'):
                    continue
                link = card.select_one('h3 a[href]')
                fields = [card.select_one('.date-tile .' + part) for part in ('d', 'm', 'y')]
                if not link or not all(fields):
                    continue
                day, month, year = [field.get_text(strip=True) for field in fields]
                if not re.fullmatch(r'\d{1,2}(?:[–-]\d{1,2})?', day) or not re.fullmatch(r'20\d{2}',year) or month[:3] not in months:
                    continue
                date_text = f'{day} {months[month[:3]]} {year}'
                start, end = extract_event_dates(date_text)
                if not start:
                    continue
                title = link.get_text(' ',strip=True)
                city_node = card.select_one('.card-meta > span:not([class]):has(svg circle)')
                desc = card.select_one('.row-body p')
                summary = desc.get_text(' ',strip=True) if desc else ''
                rows.append(make_item(source=source,title=title,url=urljoin(source['url'],link['href']),
                                      published_at=start,date_end=end,date_evidence=date_text,
                                      location=city_node.get_text(' ',strip=True) if city_node else '',
                                      event_type=guess_event_type_from_text(title+' '+summary),summary=summary))
            return rows
        if soup.select('a.event-item'):return []
        h=soup.select_one('h1')
        if not h:return []
        # CONFEC JSON-LD has contradictory fallback dates. Read the visible date field.
        date_text=''
        for label in soup.find_all(string=lambda text:text and text.strip()=='Дата'):
            parent=label.parent.parent
            text=clean_text(parent.get_text(' ',strip=True))
            if len(text)<150:date_text=text;break
        start,end=extract_event_dates(date_text)
        if not start:return []
        title=clean_text(h.get_text(' ',strip=True))
        from event_engine import guess_location,guess_event_type_from_text
        body=soup.select_one('main,article,.event-description,.event-content')
        blob=clean_text((body or soup).get_text(' ',strip=True))
        return [make_item(source=source,title=title,url=source['url'],published_at=start,date_end=end,location=guess_location(blob[:1500]),
            event_type=guess_event_type_from_text(title),date_evidence=date_text)]
    if sid=='workevent':
        import json
        from event_engine import parse_date,guess_event_type_from_text
        links={}
        visible_paths={}
        for a in soup.select('a[href]'):
            path=a['href'].rstrip('/')
            if re.fullmatch(r'/event/[^/?#]+',path):
                visible_paths[path]=urljoin(source['url'],a['href'])
            m=re.search(r'/event/.+-(\d+)$',a['href'])
            if m:links[int(m.group(1))]=urljoin(source['url'],a['href'])
        objects={}
        decoder=json.JSONDecoder()
        for script in soup.select('script'):
            text=script.string or script.get_text()
            m=re.search(r'self\.__next_f\.push\((\[.*\])\)',text,re.S)
            if not m:continue
            try:parts=json.loads(m.group(1));decoded=parts[1]
            except (ValueError,IndexError,TypeError):continue
            if not isinstance(decoded,str):continue
            for match in re.finditer(r'\{"id":\d+',decoded):
                try:o,_=decoder.raw_decode(decoded[match.start():])
                except ValueError:continue
                if o.get('start_date') and o.get('title'):objects[o['id']]=o
        rows=[]
        for o in objects.values():
            start=parse_date(o['start_date']);end=parse_date(o.get('end_date')) or start
            path=o.get('path') or '/event/'+str(o.get('slug') or '')
            url=visible_paths.get(path.rstrip('/')) or links.get(o['id'])
            if not start or not url or o.get('is_cancelled'):continue
            city=(o.get('city') or {}).get('title','')
            item=make_item(source=source,title=o['title'],url=url,published_at=start,date_end=end,
                location=city,event_type=o.get('format_label','мероприятие'),date_evidence=f"{o['start_date']} {o.get('end_date','')}")
            russian={'Москва','Санкт-Петербург','Казань','Новосибирск','Екатеринбург','Сочи','Краснодар','Нижний Новгород','Томск','Самара'}
            item.country='Россия' if city in russian else ''
            rows.append(item)
        return rows
    if sid=='all_events':
        from event_engine import parse_date,guess_event_type_from_text
        rows=[]
        for node in soup.select('[itemscope][itemtype$="Event"]'):
            n=node.select_one('[itemprop="name"]');d=node.select_one('[itemprop="startDate"]');a=node.select_one('a[itemprop="url"],a[href*="/events/"]')
            if not n or not d or not a:continue
            raw=d.get('content') or d.get('datetime') or d.get_text(' ',strip=True)
            ed=node.select_one('[itemprop="endDate"]');endraw=(ed.get('content') or ed.get_text(' ',strip=True)) if ed else raw
            start=parse_date(raw);end=parse_date(endraw) or start
            if not start:continue
            city=node.select_one('[itemprop="addressLocality"]')
            rows.append(make_item(source=source,title=clean_text(n.get_text(' ',strip=True)),url=urljoin(source['url'],a['href']),
                published_at=start,date_end=end,location=clean_text(city.get_text(' ',strip=True)) if city else '',
                event_type=guess_event_type_from_text(clean_text(node.get_text(' ',strip=True))),date_evidence=f'{raw} {endraw}'))
        return rows
    if sid=='tadviser_calendar':
        months={'янв':1,'фев':2,'мар':3,'апр':4,'май':5,'июн':6,'июл':7,'авг':8,'сен':9,'окт':10,'ноя':11,'дек':12}
        items=[];seen=set()
        for table in soup.select('table'):
            if 'calendar_table' in table.get('class',[]):continue
            month=None;year=datetime.now().year
            for row in table.find_all('tr',recursive=False) + [r for body in table.find_all('tbody',recursive=False) for r in body.find_all('tr',recursive=False)]:
                cells=row.find_all('td',recursive=False)
                if len(cells)!=3:continue
                date_text=clean_text(cells[0].get_text(' ',strip=True))
                found=re.search('|'.join(months),date_text,re.I)
                if found:
                    new_month=months[found.group().lower()]
                    if month and new_month<month:year+=1
                    month=new_month
                day=re.search(r'\b\d{1,2}\b',date_text)
                a=cells[2].select_one('a[href]')
                if not month or not day or not a:continue
                title=clean_text(a.get_text(' ',strip=True));url=urljoin(source['url'],a['href'])
                kind=clean_text(cells[1].get_text(' ',strip=True))
                if not re.search(r'конференц|форум|вебинар|саммит|выставк|семинар|митап',kind,re.I):continue
                try:dt=datetime(year,month,int(day.group()))
                except ValueError:continue
                key=(title,dt,url)
                if key in seen:continue
                seen.add(key)
                items.append(make_item(source=source,title=title,url=url,published_at=dt,date_end=dt,event_type=kind,
                    date_certainty='tentative',date_evidence=f'{date_text} (месяц группы {month}, год требует проверки)'))
        return items
    if sid=='globaltech_official':
        text=clean_text(soup.get_text(' ',strip=True))
        # Anchor to the venue block, not speaker bios or archive sections.
        match=re.search(r'МЕСТО ПРОВЕДЕНИЯ(.{0,650})',text,re.I)
        blob=match.group(1) if match else ''
        if not re.search(r'20\d{2}',blob):return []
        start,end=extract_event_dates(blob)
        if not start:return []
        return [make_item(source=source,title=f'GLOBAL TECH FORUM {start.year}',url=source['url'],published_at=start,date_end=end,
            location='Москва' if re.search(r'\bМосква\b',blob) else '',event_type='форум',date_evidence=blob)]
    if sid=='protei_webinars':
        items=[];seen=set()
        # The public page renders repeated responsive cards: preserve each dated session once.
        for text_node in soup.find_all(string=re.compile(r'\b\d{2}\.\d{2}\.20\d{2}\b')):
            raw=str(text_node)
            if len(raw)>200:continue
            for node in list(text_node.parents)[:6]:
                title=node.select_one('h2,h3,h4')
                blob=clean_text(node.get_text(' ',strip=True))
                if not title or len(blob)>2600:continue
                dates=re.findall(r'\b\d{2}\.\d{2}\.20\d{2}\b',blob)
                if len(set(dates))!=1:continue
                start,end=extract_event_dates(dates[0]);name=clean_text(title.get_text(' ',strip=True))
                if not start or (name,start) in seen:break
                seen.add((name,start))
                items.append(make_item(source=source,title=name,url=source['url']+'#session-'+start.strftime('%Y%m%d'),
                    published_at=start,date_end=end,location='Онлайн',event_type='вебинар',summary=blob,date_evidence=dates[0]))
                break
        return items
    return None
