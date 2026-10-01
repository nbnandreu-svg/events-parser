"""Follow source-specific pagination without drifting into archives or another filter."""
import re
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,parse_qs,urlencode

def page_links(soup,base,source):
    root=urlsplit(source['url']);params=set(source.get('pagination_params') or ['page'])
    root_query={k:v for k,v in parse_qsl(root.query) if k not in params}
    result=set()
    for a in soup.select('a[href]'):
        url=urljoin(base,a['href']);p=urlsplit(url)
        if p.netloc.lower().removeprefix('www.')!=root.netloc.lower().removeprefix('www.'):continue
        query=parse_qs(p.query)
        if source.get('pagination') and p.path.rstrip('/')==root.path.rstrip('/'):
            if not any(k in query and query[k][0].isdigit() for k in params):continue
            if any(query.get(k,[None])[0]!=v for k,v in root_query.items()):continue
            # Filter/reset controls can expose archives and must not be followed as pagination.
            if set(query)-params-set(root_query):continue
            # Other PAGEN widgets often paginate an archive on the same page.
            if any(k.startswith('PAGEN_') and k not in params for k in query):continue
            normalized=dict(root_query)
            normalized.update({k:str(int(query[k][0])) for k in params if k in query and int(query[k][0])>1})
            result.add(urlunsplit((root.scheme,root.netloc,root.path,urlencode(sorted(normalized.items())),'')))
        pattern=source.get('discovery_path_pattern')
        if pattern and re.fullmatch(pattern,p.path):
            years=[int(x) for x in re.findall(r'20\d{2}',p.path)]
            if years and min(years)<int(source.get('min_year',2026)):continue
            result.add(urlunsplit((p.scheme,p.netloc,p.path,p.query,'')))
    def rank(url):
        q=parse_qs(urlsplit(url).query)
        for name in params:
            if q.get(name) and q[name][0].isdigit():return int(q[name][0]),url
        return 0,url
    return sorted(result,key=rank)

def detail_links(soup,base,source):
    selector=source.get('detail_link_selector')
    if not selector:return []
    h=urlsplit(source['url']).netloc.removeprefix('www.')
    return list(dict.fromkeys(urljoin(base,a['href']) for a in soup.select(selector) if a.get('href')
           and urlsplit(urljoin(base,a['href'])).netloc.removeprefix('www.')==h))
