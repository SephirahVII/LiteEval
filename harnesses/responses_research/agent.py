"""Standalone Responses research command using the LiteEval agent-events protocol."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from client import ResponsesClient
from research import ResponsesResearch, encoded


def send(event_type, **payload):
    print(encoded({'schema_version': 1, 'type': event_type, **payload}), flush=True)


class Workspace:
    def write(self, name, content):
        if name not in ('response.md', 'sources.json'):
            raise ValueError('Unsupported research submission')
        Path(name).write_bytes(content)


async def main():
    request = json.load(sys.stdin)
    client = ResponsesClient(os.environ['MODEL_BASE_URL'], os.environ['MODEL_ID'],
                             os.environ['MODEL_API_KEY'], json.loads(os.getenv('MODEL_PARAMETERS', '{}')))
    exchange_dir = Path('.harness-output')
    exchange_dir.mkdir(exist_ok=True)
    calls = 0

    def emit(kind, payload):
        send('event', kind=kind, payload=payload)

    async def respond(body):
        nonlocal calls
        calls += 1
        started = time.monotonic()
        raw = None
        emit('model_request', {'api': 'responses'})
        try:
            raw = await client.respond(body, lambda event: emit('provider_event', event))
            path = exchange_dir / f'responses-{calls}.json'
            path.write_text(encoded({'request': body, 'response': raw}), encoding='utf-8')
            send('exchange', path=path.as_posix())
            return raw
        finally:
            send('usage', record={'status': 'succeeded' if raw is not None else 'error',
                'usage': (raw.get('usage') or {}) if raw else {},
                'requested_model': client.config['model'], 'provider_reported_model': raw.get('model') if raw else None,
                'latency_ms': int((time.monotonic() - started) * 1000)})

    result = await ResponsesResearch().run(request, {'runtime': Workspace(), 'respond': respond,
                                                     'model': client.config['model'], 'emit': emit})
    send('result', result=result)


if __name__ == '__main__':
    asyncio.run(main())
