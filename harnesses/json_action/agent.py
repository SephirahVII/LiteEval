"""One structured decision using a Chat Completions endpoint."""
import json
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


def main():
    request = json.load(sys.stdin)
    model = os.environ['MODEL_ID']
    endpoint = os.environ['MODEL_BASE_URL']
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid model base URL')
    messages = request.get('state') or [
        {'role': 'system', 'content': request['instructions']},
        {'role': 'user', 'content': json.dumps({'prompt': request['prompt'], 'input_files': request['input_files']})}]
    messages.extend({'role': 'user', 'content': text} for text in request['messages'])
    body = {'model': model, 'messages': messages, **json.loads(os.getenv('MODEL_PARAMETERS', '{}'))}
    started = time.monotonic()
    response = None
    send('event', kind='model_request', payload={'api': 'chat'})
    try:
        http = urllib.request.Request(endpoint.rstrip('/') + '/chat/completions', json.dumps(body).encode(),
            {'Authorization': 'Bearer ' + os.environ['MODEL_API_KEY'], 'Content-Type': 'application/json'}, method='POST')
        with urllib.request.build_opener(NoRedirect).open(http, timeout=180) as stream:
            data = stream.read(4_000_001)
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
            'latency_ms': int((time.monotonic() - started) * 1000)})
    content = response['choices'][0]['message']['content']
    messages.append({'role': 'assistant', 'content': content})
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
    send('result', result={'schema_version': 1, 'output': action, 'state': messages})


if __name__ == '__main__':
    main()
