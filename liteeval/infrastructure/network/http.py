"""Bounded HTTP transport with explicit timeout and no credential-carrying redirects."""
import asyncio
import json
import urllib.error
import urllib.request
from liteeval.infrastructure.security.secrets import checked_url

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


async def responses_stream(url, body, headers, on_event, timeout=120, max_bytes=32_000_000):
    """Read provider SSE without imposing a short whole-rollout HTTP deadline."""
    import threading
    loop = asyncio.get_running_loop()
    stopped = threading.Event()

    def consume():
        checked_url(url)
        request = urllib.request.Request(url, json.dumps({**body, 'stream': True}, ensure_ascii=False, allow_nan=False).encode(),
                                         {'Content-Type': 'application/json', **headers}, method='POST')
        events, final, size = [], None, 0
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
                if 'text/event-stream' not in response.headers.get('Content-Type', ''):
                    return json.loads(response.read(max_bytes))
                for line in response:
                    if stopped.is_set(): break
                    size += len(line)
                    if size > max_bytes: raise ValueError('Response stream exceeds size limit')
                    if not line.startswith(b'data:'): continue
                    data = line[5:].strip()
                    if data == b'[DONE]': break
                    event = json.loads(data); events.append(event)
                    kind = event.get('type', '')
                    if kind in ('response.created', 'response.in_progress', 'response.output_item.added', 'response.output_item.done',
                                'response.function_call_arguments.done', 'response.completed', 'response.failed', 'response.incomplete'):
                        public = event
                        if kind in ('response.completed', 'response.created', 'response.in_progress'):
                            r = event.get('response', {})
                            public = {'type': kind, 'response': {k: r.get(k) for k in ('id', 'status', 'model', 'usage')}}
                        loop.call_soon_threadsafe(on_event, public)
                    if kind in ('response.completed', 'response.failed', 'response.incomplete'):
                        final = event.get('response')
        except urllib.error.HTTPError as exc:
            raise HTTPFailure(exc.code, exc.read(10000).decode('utf-8', errors='replace')) from None
        if final is None: raise RuntimeError('Response stream ended without a terminal response')
        return {**final, '_sse_events': events}

    try:
        return await asyncio.to_thread(consume)
    finally:
        stopped.set()
