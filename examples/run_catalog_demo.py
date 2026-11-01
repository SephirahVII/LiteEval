"""Real local API/CLI-Worker flow for immutable versions, revisions and preflight."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liteeval import __version__
from liteeval.interfaces.http.api import make_handler
from liteeval.infrastructure.persistence.migrations import SCHEMA_VERSION
from liteeval.infrastructure.persistence.store import Store


class Agent(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        raw = json.dumps({'answer': body['input']['question']}).encode()
        self.send_response(200);self.send_header('Content-Length', str(len(raw)));self.end_headers();self.wfile.write(raw)
    def log_message(self, *args):pass


def main():
    servers = []
    worker = None
    def start(handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        servers.append((server, thread));return server
    with tempfile.TemporaryDirectory(prefix='liteeval-catalog-') as root:
        store = Store(Path(root)/'catalog.db')
        token = secrets.token_urlsafe(32)
        agent = start(Agent)
        with patch.dict('os.environ', {'LITEEVAL_PUBLIC_ORIGIN': '', 'LITEEVAL_COOKIE_SECURE': '0'}):
            api = start(make_handler(store, token))
        base = f'http://127.0.0.1:{api.server_port}'
        opener = build_opener(ProxyHandler({}))
        def call(method, path, body=None, expected=200, key=None):
            headers = {'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'}
            if key:headers['Idempotency-Key'] = key
            request = Request(base+path, json.dumps(body).encode() if body is not None else None, headers, method=method)
            try:response = opener.open(request, timeout=10)
            except HTTPError as exc:response = exc
            with response:
                value = json.load(response)
                if response.code != expected:raise RuntimeError(f'{method} {path}: {response.code} {value}')
                return value
        def wait_for(fetch, predicate):
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                if worker and worker.poll() is not None:
                    raise RuntimeError('Worker exited: '+worker.communicate()[1])
                value = fetch()
                if predicate(value):return value
                time.sleep(.05)
            raise RuntimeError('Timed out waiting for Worker')
        try:
            pid = call('POST', '/v1/projects', {'name': 'Catalog integration'}, 201)['id']
            did = call('POST', '/v1/datasets', {'project_id': pid, 'name': 'Business regression'}, 201)['id']
            records = [{'input': {'question': word}, 'expected': word, 'metadata': {'critical': True}} for word in ('alpha', 'beta')]
            invalid = call('POST', f'/v1/datasets/{did}/import-preview', {'records': records+[{'input': 'invalid'}]})
            assert not invalid['valid'] and invalid['invalid_count'] == 1
            valid = call('POST', f'/v1/datasets/{did}/import-preview', {'records': records})
            assert valid['valid']
            call('POST', f'/v1/datasets/{did}/import', {'records': records}, 201)
            release = call('POST', f'/v1/datasets/{did}/versions', {'name': 'business-v1'}, 201)
            eid = call('POST', '/v1/evaluators', {'project_id': pid, 'name': 'Exact', 'kind': 'exact_match', 'config': {}}, 201)['id']
            revision = call('POST', f'/v1/evaluators/{eid}/revisions', {'name': 'Exact updated', 'config': {}, 'base_revision': 1}, 201)
            assert call('GET', f'/v1/evaluators/{eid}/revisions')['total'] == 2
            aid = call('POST', '/v1/agent-versions', {'project_id': pid, 'label': 'echo', 'config': {'type': 'http', 'endpoint': f'http://127.0.0.1:{agent.server_port}'}}, 201)['id']
            worker = subprocess.Popen([sys.executable, '-m', 'liteeval.interfaces.cli.main', '--db', store.path, 'worker'], cwd=ROOT,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            ready = wait_for(lambda: call('GET', '/v1/runtime/status'), lambda r: r['online_workers'] == 1)
            payload = {'project_id': pid, 'dataset_version_id': release['id'], 'agent_version_id': aid, 'evaluator_ids': [eid], 'name': 'baseline'}
            checked = call('POST', '/v1/experiments/preflight', payload)
            assert checked['ready'] and checked['credential_readiness']['state'] == 'ready'
            def execute(body):
                check = call('POST', '/v1/experiments/preflight', body)
                exp = call('POST', '/v1/experiments', {**body, 'preflight_hash': check['preflight_hash']}, 201, secrets.token_hex(16))
                progress = wait_for(lambda: call('GET', f'/v1/experiments/{exp["id"]}/progress'), lambda r: r['status'] in {'completed', 'failed', 'cancelled'})
                assert progress['status'] == 'completed'
                return exp
            baseline = execute(payload)
            frozen_case = call('GET', f'/v1/dataset-versions/{release["id"]}/cases')['items'][0]
            call('POST', f'/v1/cases/{frozen_case["id"]}/revisions', {'input': frozen_case['input'], 'expected': 'changed', 'base_revision': 1}, 201)
            candidate = execute({**payload, 'name': 'candidate'})
            comparison = call('GET', f'/v1/compare?left={baseline["id"]}&right={candidate["id"]}')
            assert comparison['comparable'] and comparison['counts']['same'] == 2
            different = execute({**payload, 'evaluator_ids': [revision['id']], 'name': 'new scoring revision'})
            incompatible = call('GET', f'/v1/compare?left={baseline["id"]}&right={different["id"]}')
            assert not incompatible['comparable']
            live = {k: v for k, v in payload.items() if k != 'dataset_version_id'}
            live['dataset_id'] = did
            old_check = call('POST', '/v1/experiments/preflight', live)
            call('POST', f'/v1/cases/{frozen_case["id"]}/revisions', {'input': frozen_case['input'], 'expected': 'edited again', 'base_revision': 2}, 201)
            stale = call('POST', '/v1/experiments', {**live, 'preflight_hash': old_check['preflight_hash']}, 409)
            assert stale['error'] == 'stale_preflight'
            report = {'version': __version__, 'schema_version': SCHEMA_VERSION, 'invalid_import_count': invalid['invalid_count'],
                      'published_cases': release['case_count'], 'evaluator_revisions': 2, 'online_workers': ready['online_workers'],
                      'preflight_worker_state': checked['credential_readiness']['state'], 'published_version_compare': comparison['counts'],
                      'different_evaluator_revision_comparable': incompatible['comparable'], 'stale_preflight_status': 409}
            target = Path('data/catalog-report.json');target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f'Report: {target}')
        finally:
            if worker:
                if worker.poll() is None:worker.terminate()
                worker.communicate(timeout=10)
            for server, thread in reversed(servers):server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':main()
