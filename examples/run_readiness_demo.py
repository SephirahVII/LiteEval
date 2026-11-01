"""Exercise the Web-ready backend locally with temporary credentials and no model charges."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liteeval.api import make_handler
from liteeval.previews import PreviewRunner
from liteeval.runner import ExperimentRunner
from liteeval.sessions import BrowserSessions
from liteeval.storage import Store
from liteeval import __version__
from liteeval.migrations import SCHEMA_VERSION


class Agent(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        payload = json.dumps({'answer': body['input']['question']}).encode()
        self.send_response(200);self.send_header('Content-Length', str(len(payload)));self.end_headers();self.wfile.write(payload)
    def log_message(self, *args):pass


def main():
    servers = []
    def start(handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        servers.append((server, thread));return server
    with tempfile.TemporaryDirectory(prefix='liteeval-readiness-') as root:
        store = Store(Path(root)/'demo.db')
        password = secrets.token_urlsafe(24)
        BrowserSessions(store).configure_owner('owner', password)
        agent = start(Agent)
        with patch.dict('os.environ', {'LITEEVAL_PUBLIC_ORIGIN': '', 'LITEEVAL_COOKIE_SECURE': '0'}):
            api = start(make_handler(store, secrets.token_urlsafe(32)))
        base = f'http://127.0.0.1:{api.server_port}'
        opener = build_opener(ProxyHandler({}))
        browser = {'Origin': base}
        def call(method, path, body=None, expected=200):
            request = Request(base+path, json.dumps(body).encode() if body is not None else None,
                              {'Content-Type': 'application/json', **browser}, method=method)
            try:response = opener.open(request, timeout=10)
            except HTTPError as exc:response = exc
            with response:
                value = json.load(response)
                if response.code != expected:raise RuntimeError(f'{method} {path}: {response.code} {value}')
                return value, response.headers
        def request(method, path, body=None, expected=200):return call(method, path, body, expected)[0]
        try:
            login, headers = call('POST', '/v1/auth/session', {'username': 'owner', 'password': password})
            browser.update({'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': login['csrf_token']})
            pid = request('POST', '/v1/projects', {'name': 'Web backend readiness'}, 201)['id']
            did = request('POST', '/v1/datasets', {'project_id': pid, 'name': 'Readiness'}, 201)['id']
            cases = [request('POST', f'/v1/datasets/{did}/cases', {'input': {'question': word}, 'expected': word}, 201) for word in ('alpha', 'beta')]
            aid = request('POST', '/v1/agent-versions', {'project_id': pid, 'label': 'echo', 'config': {'type': 'http', 'endpoint': f'http://127.0.0.1:{agent.server_port}'}}, 201)['id']
            eid = request('POST', '/v1/evaluators', {'project_id': pid, 'name': 'Exact', 'kind': 'exact_match', 'config': {}}, 201)['id']
            exp = request('POST', '/v1/experiments', {'project_id': pid, 'dataset_id': did, 'agent_version_id': aid, 'evaluator_ids': [eid]}, 201)['id']
            queued = request('GET', f'/v1/experiments/{exp}/progress')
            asyncio.run(ExperimentRunner(store).run(exp))
            completed = request('GET', f'/v1/experiments/{exp}/progress')
            page = request('GET', f'/v1/experiments/{exp}/runs?limit=1')
            assert page['total'] == 2 and len(page['items']) == 1 and 'output' not in page['items'][0]
            job = request('POST', '/v1/evaluator-previews', {'project_id': pid, 'evaluator_id': eid, 'run_id': page['items'][0]['run_id']}, 202)['id']
            asyncio.run(PreviewRunner(store).run_next())
            preview = request('GET', f'/v1/evaluator-previews/{job}')
            case = cases[0]
            update = {'input': case['input'], 'expected': 'changed', 'base_revision': 1}
            request('POST', f'/v1/cases/{case["id"]}/revisions', update, 201)
            request('POST', f'/v1/cases/{case["id"]}/revisions', update, 409)
            request('PATCH', f'/v1/datasets/{did}', {'archived': True})
            assert request('GET', '/v1/datasets')['total'] == 0
            rerun = request('POST', f'/v1/experiments/{exp}/rerun', {}, 201)['id']
            asyncio.run(ExperimentRunner(store).run(rerun))
            comparison = request('GET', f'/v1/compare?left={exp}&right={rerun}')
            assert comparison['comparable'] and comparison['counts']['same'] == 2
            request('DELETE', '/v1/auth/session')
            request('GET', '/v1/auth/me', expected=401)
            report = {'version': __version__, 'schema_version': SCHEMA_VERSION, 'session_and_csrf': 'passed',
                      'queued_pending': queued['counts']['pending'], 'terminal_runs': completed['counts']['terminal'],
                      'pagination_total': page['total'], 'preview_outcome': preview['result']['score']['outcome'],
                      'stale_revision_status': 409, 'archived_history_compare': comparison['counts'], 'logout': 'passed'}
            output = Path('data/readiness-report.json');output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f'Report: {output}')
        finally:
            for server, thread in reversed(servers):server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':main()
