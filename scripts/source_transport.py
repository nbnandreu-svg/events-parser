"""A bounded native TLS fallback for legacy HTTPS sources; certificates stay verified."""
import asyncio
import os
import re
import shutil
from urllib.parse import urlsplit

def decode_page(data):
    m=re.search(br'charset\s*=\s*["\x27]?([\w-]+)',data[:8192],re.I)
    encodings=[m.group(1).decode('ascii')] if m else []
    for encoding in [*encodings,'utf-8','cp1251']:
        try:return data.decode(encoding)
        except (UnicodeError,LookupError):continue
    return data.decode('utf-8',errors='replace')

async def curl_text(client,url):
    executable=shutil.which('curl.exe') or shutil.which('curl')
    if not executable:raise RuntimeError('curl is not installed')
    if urlsplit(url).scheme not in {'https','http'}:raise ValueError('unsupported source scheme')
    gates=getattr(client,'_parser_host_limits',None)
    if gates is None:gates={};client._parser_host_limits=gates
    gate=gates.setdefault(urlsplit(url).netloc.removeprefix('www.'),asyncio.Semaphore(2))
    async with gate:
        kwargs={'creationflags':0x08000000} if os.name=='nt' else {}
        proc=await asyncio.create_subprocess_exec(executable,'--fail','--location','--silent','--show-error',
            '--max-time','22','--connect-timeout','8','--max-filesize','20000000','--user-agent',client.headers.get('User-Agent','EventsParser/1.0'),
            url,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,**kwargs)
        try:body,error=await asyncio.wait_for(proc.communicate(),timeout=25)
        except asyncio.TimeoutError:
            proc.kill();await proc.communicate();raise RuntimeError('curl source timeout')
        if proc.returncode:raise RuntimeError('curl: '+error.decode('utf-8',errors='replace')[:250])
        return decode_page(body)
