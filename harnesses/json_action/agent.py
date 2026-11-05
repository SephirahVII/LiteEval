"""One structured decision using a Chat Completions endpoint."""
import json
import http.client
import hashlib
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(event_type, **payload):
    print(json.dumps({'schema_version': 1, 'type': event_type, **payload}, ensure_ascii=False), flush=True)


def post(request, model, timing=None):
    """Retry transient transport failures; record unknown usage for each retry."""
    opener=urllib.request.build_opener(NoRedirect)
    for attempt in range(1,4):
        began=time.monotonic()
        if timing is not None:
            timing['started']=began
        send('event',kind='model_request',payload={'api':'chat','attempt':attempt})
        try:
            with opener.open(request,timeout=180) as stream:
                return stream.read(4_000_001)
        except (urllib.error.URLError,TimeoutError,ConnectionError,http.client.IncompleteRead) as exc:
            status=exc.code if isinstance(exc,urllib.error.HTTPError) else None
            transient=status is None or status in (429,500,502,503,504)
            if not transient or attempt==3:
                raise RuntimeError(f'Chat request failed ({status or type(exc).__name__}) after {attempt} attempt(s)') from None
            send('usage',record={'status':'error','usage':{},'requested_model':model,
                                'latency_ms':int((time.monotonic()-began)*1000)})
            send('event',kind='provider_event',payload={'event':'model_request_retry','attempt':attempt,'http_status':status,'error_type':type(exc).__name__})
            retry_after=15*attempt if status==429 else attempt
            if status==429 and exc.headers:
                try:
                    retry_after=float(exc.headers.get('Retry-After',retry_after))
                except (TypeError,ValueError):
                    pass
            time.sleep(min(60,max(1,retry_after)))


HISTORY=Path('.harness-state/conversation.json')


def load_messages(request):
    state=request.get('state')
    if state is None:
        return [{'role':'system','content':request['instructions']},
                {'role':'user','content':json.dumps({'prompt':request['prompt'],'input_files':request['input_files']})}]
    if not isinstance(state,dict) or state.get('schema_version')!=1:
        raise ValueError('Invalid Agent conversation handle')
    body=HISTORY.read_bytes()
    if hashlib.sha256(body).hexdigest()!=state.get('history_sha256'):
        raise ValueError('Agent conversation history digest mismatch')
    messages=json.loads(body)
    if not isinstance(messages,list):
        raise ValueError('Invalid Agent conversation history')
    return messages


def save_messages(messages):
    body=json.dumps(messages,ensure_ascii=False).encode('utf-8')
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_bytes(body)
    return {'schema_version':1,'history_sha256':hashlib.sha256(body).hexdigest(),'message_count':len(messages)}


def main():
    request = json.load(sys.stdin)
    model = os.environ['MODEL_ID']
    endpoint = os.environ['MODEL_BASE_URL']
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid model base URL')
    messages = load_messages(request)
    messages.extend({'role': 'user', 'content': text} for text in request['messages'])
    body = {'model': model, 'messages': messages, **json.loads(os.getenv('MODEL_PARAMETERS', '{}'))}
    timing = {'started':time.monotonic()}
    response = None
    try:
        http = urllib.request.Request(endpoint.rstrip('/') + '/chat/completions', json.dumps(body).encode(),
            {'Authorization': 'Bearer ' + os.environ['MODEL_API_KEY'], 'Content-Type': 'application/json'}, method='POST')
        data = post(http,model,timing)
        if len(data) > 4_000_000:
            raise ValueError('Model response exceeds size limit')
        response = json.loads(data)
        path = Path('.harness-output/chat.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'request': body, 'response': response}, ensure_ascii=False), encoding='utf-8')
        send('exchange', path=path.as_posix())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Chat request failed with HTTP {exc.code}') from None
    finally:
        send('usage', record={'status': 'succeeded' if response else 'error',
            'usage': (response.get('usage') or {}) if response else {}, 'requested_model': model,
            'provider_reported_model': response.get('model') if response else None,
            'latency_ms': int((time.monotonic() - timing['started']) * 1000)})
    message = response['choices'][0]['message']
    content = message.get('content') or ''
    messages.append({'role': 'assistant', **message})
    text = content.strip()
    lines = text.splitlines()
    if len(lines) > 2 and lines[0] in ('```', '```json') and lines[-1] == '```':
        text = '\n'.join(lines[1:-1])
    try:
        action = json.loads(text)
        if not isinstance(action, dict) or len(text.encode()) > 12_000:
            raise ValueError('Invalid action')
    except ValueError:
        action = {'action': 'invalid'}
    send('result', result={'schema_version': 1, 'output': action, 'state': save_messages(messages)})


if __name__ == '__main__':
    main()
