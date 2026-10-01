"""Source-specific list readers; importing this file makes no network calls."""
import re
from datetime import datetime
from urllib.parse import urljoin

def parse_special(source,soup):
    from event_engine import clean_text, make_item, extract_event_dates
    sid=source['id']
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
        for a in soup.select('a[href]'):
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
            if not start or o['id'] not in links:continue
            city=(o.get('city') or {}).get('title','')
            item=make_item(source=source,title=o['title'],url=links[o['id']],published_at=start,date_end=end,
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
