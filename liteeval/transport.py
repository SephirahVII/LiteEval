"""Bounded HTTP transport with explicit timeout and no credential-carrying redirects."""
import asyncio
import json
import urllib.error
import urllib.request
from .security import checked_url

class HTTPFailure(RuntimeError):
    def __init__(self,status,raw):
        super().__init__(f'HTTP {status}')
        self.status,self.raw,self.retryable=status,raw,status==429 or status>=500

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None

def request_json(url,body,headers=None,timeout=30,max_bytes=4_000_000):
    checked_url(url)
    data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
    if len(data)>4_000_000:raise ValueError('Request exceeds 4 MB')
    req=urllib.request.Request(url,data,{'Content-Type':'application/json',**(headers or {})},method='POST')
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=timeout) as response:
            data=response.read(max_bytes+1)
    except urllib.error.HTTPError as exc:
        raw=exc.read(max_bytes).decode('utf-8',errors='replace')
        raise HTTPFailure(exc.code,raw) from None
    if len(data)>max_bytes:raise ValueError('Response exceeds size limit')
    return json.loads(data,parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))

async def post_json(url,body,headers=None,timeout=30):
    if not 0.05<=float(timeout)<=300:raise ValueError('timeout_seconds must be 0.05..300')
    return await asyncio.wait_for(asyncio.to_thread(request_json,url,body,headers,float(timeout)),timeout=float(timeout)+0.25)
