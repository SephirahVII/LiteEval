"""Bounded streaming Responses client for the standalone research Harness."""
import asyncio
import json
import urllib.request
import urllib.error
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def checked_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment or parsed.query):
        raise ValueError('Invalid Responses base URL')
    return url


class ResponsesClient:
    def __init__(self, base_url, model, api_key, parameters=None):
        self.config = {'base_url': checked_url(base_url), 'model': model}
        self.api_key = api_key
        self.parameters = parameters or {}

    async def respond(self, body, on_event=lambda event: None):
        payload = {**body, **self.parameters, 'model': self.config['model']}
        if 'max_tokens' in payload:
            payload['max_output_tokens'] = payload.pop('max_tokens')
        return await responses_stream(self.config['base_url'].rstrip('/') + '/responses', payload,
                                      {'Authorization': 'Bearer ' + self.api_key}, on_event)


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
                    data = response.read(max_bytes + 1)
                    if len(data) > max_bytes: raise ValueError('Response exceeds size limit')
                    return json.loads(data)
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
            raise RuntimeError(f'Responses request failed with HTTP {exc.code}') from None
        if final is None: raise RuntimeError('Response stream ended without a terminal response')
        return {**final, '_sse_events': events}

    try:
        return await asyncio.to_thread(consume)
    finally:
        stopped.set()
