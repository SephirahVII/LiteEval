import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

from liteeval.api import make_handler
from liteeval.sessions import BrowserSessions, AuthError, token_hash
from liteeval.storage import Store
from liteeval.migrations import SCHEMA_VERSION


class BrowserAPITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'test.db')
        self.sessions = BrowserSessions(self.store)
        self.password = 'test-only-owner-password'
        self.sessions.configure_owner('owner', self.password)
        self.admin = 'test-only-admin-token-long-enough'
        self.env = patch.dict('os.environ', {'LITEEVAL_PUBLIC_ORIGIN': '', 'LITEEVAL_COOKIE_SECURE': '0'})
        self.env.start()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, self.admin))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = build_opener(ProxyHandler({}))
        self.pid = self.store.create_project('Project')['id']
        self.did = self.store.create_dataset(self.pid, 'Cases')['id']

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.env.stop(); self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        request = Request(self.base+path, json.dumps(body).encode() if body is not None else None,
                          {'Content-Type': 'application/json', **(headers or {})}, method=method)
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            value = json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
            return response.code, value, response.headers

    def login(self):
        status, value, headers = self.request('POST', '/v1/auth/session',
            {'username': 'owner', 'password': self.password}, {'Origin': self.base})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0], value['csrf_token'], headers

    def browser_headers(self):
        cookie, csrf, _ = self.login()
        return {'Cookie': cookie, 'Origin': self.base, 'X-CSRF-Token': csrf}

    def test_session_cookie_csrf_and_logout(self):
        cookie, csrf, response_headers = self.login()
        self.assertIn('HttpOnly', response_headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', response_headers['Set-Cookie'])
        self.assertEqual(response_headers['Cache-Control'], 'no-store')
        status, me, _ = self.request('GET', '/v1/auth/me', headers={'Cookie': cookie})
        self.assertEqual(status, 200); self.assertEqual(me['csrf_token'], csrf)
        with self.store.connect() as db:
            session = dict(db.execute('SELECT * FROM browser_sessions').fetchone())
        self.assertNotIn(cookie.split('=', 1)[1], json.dumps(session))
        headers = {'Cookie': cookie, 'Origin': self.base, 'X-CSRF-Token': csrf}
        self.assertEqual(self.request('POST', '/v1/projects', {'name': 'Browser'}, headers)[0], 201)
        status, _, out = self.request('DELETE', '/v1/auth/session', headers=headers)
        self.assertEqual(status, 200); self.assertIn('Max-Age=0', out['Set-Cookie'])
        self.assertEqual(self.request('GET', '/v1/projects', headers={'Cookie': cookie})[0], 401)

    def test_browser_write_requires_both_origin_and_csrf(self):
        cookie, csrf, _ = self.login()
        variants = [{'Cookie': cookie}, {'Cookie': cookie, 'Origin': self.base},
                    {'Cookie': cookie, 'X-CSRF-Token': csrf},
                    {'Cookie': cookie, 'Origin': 'https://attacker.example', 'X-CSRF-Token': csrf}]
        for headers in variants:
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.request('POST', '/v1/projects', {'name': 'Denied'}, headers)[0], 403)
        self.assertEqual(self.store.list_items('projects')['total'], 1)

    def test_login_requires_origin_and_is_rate_limited(self):
        body = {'username': 'owner', 'password': self.password}
        self.assertEqual(self.request('POST', '/v1/auth/session', body)[0], 403)
        for _ in range(5):
            self.assertEqual(self.request('POST', '/v1/auth/session', {**body, 'password': 'wrong'}, {'Origin': self.base})[0], 401)
        self.assertEqual(self.request('POST', '/v1/auth/session', body, {'Origin': self.base})[0], 429)

    def test_expiry_idle_and_owner_rotation_revoke_sessions(self):
        token = self.sessions.login('owner', self.password, 'direct')
        with self.store.connect() as db:
            db.execute('UPDATE browser_sessions SET last_seen=?', (time.time()-8*3600-1,))
        with self.assertRaises(AuthError): self.sessions.authenticate(token)
        token = self.sessions.login('owner', self.password, 'direct')
        with self.store.connect() as db:
            db.execute('UPDATE browser_sessions SET expires_at=0 WHERE token_hash=?', (token_hash(token),))
        with self.assertRaises(AuthError): self.sessions.authenticate(token)
        token = self.sessions.login('owner', self.password, 'direct')
        self.sessions.configure_owner('owner', 'a-new-test-only-password')
        with self.assertRaises(AuthError): self.sessions.authenticate(token)

    def test_secure_cookie_under_explicit_https_origin(self):
        with patch.dict('os.environ', {'LITEEVAL_PUBLIC_ORIGIN': 'HTTPS://eval.example'}):
            self.server.RequestHandlerClass = make_handler(self.store, self.admin)
        status, _, headers = self.request('POST', '/v1/auth/session',
            {'username': 'owner', 'password': self.password}, {'Origin': 'https://eval.example'})
        self.assertEqual(status, 200); self.assertIn('; Secure', headers['Set-Cookie'])

    def test_bearer_compatibility_and_invalid_bearer_does_not_fall_back(self):
        key = self.store.issue_key(self.pid)['token']
        headers = {'Authorization': 'Bearer '+key}
        self.assertEqual(self.request('POST', '/v1/datasets', {'project_id': self.pid, 'name': 'SDK'}, headers)[0], 201)
        cookie, _, _ = self.login()
        self.assertEqual(self.request('GET', '/v1/projects', headers={'Authorization': 'Bearer wrong', 'Cookie': cookie})[0], 401)
        foreign = self.store.create_project('Foreign')['id']
        self.assertEqual(self.request('POST', '/v1/datasets', {'project_id': foreign, 'name': 'Denied'}, headers)[0], 403)

    def test_archive_and_revision_conflict_over_http(self):
        headers = self.browser_headers()
        case = self.store.add_case(self.did, {}, 'old')
        body = {'input': {}, 'expected': 'new', 'base_revision': 1}
        route = f'/v1/cases/{case["id"]}/revisions'
        self.assertEqual(self.request('POST', route, body, headers)[0], 201)
        self.assertEqual(self.request('POST', route, body, headers)[0], 409)
        self.assertEqual(self.request('POST', route, {**body, 'base_revision': None}, headers)[0], 400)
        self.assertEqual(self.request('PATCH', f'/v1/datasets/{self.did}', {'archived': True}, headers)[0], 200)
        status, datasets, _ = self.request('GET', '/v1/datasets?archived=only', headers=headers)
        self.assertEqual(status, 200); self.assertEqual(datasets['total'], 1)
        self.assertEqual(self.request('POST', f'/v1/datasets/{self.did}/cases', {'input': {}}, headers)[0], 409)

    def test_project_key_cannot_configure_or_preview_evaluators(self):
        eid = self.store.add_evaluator(self.pid, 'exact', 'exact_match', {})['id']
        headers = {'Authorization': 'Bearer '+self.store.issue_key(self.pid)['token']}
        self.assertEqual(self.request('PATCH', f'/v1/evaluators/{eid}', {'archived': True}, headers)[0], 403)
        body = {'project_id': self.pid, 'evaluator_id': eid, 'case': {'input': {}, 'expected': 'ok'}, 'run': {'output': 'ok'}}
        self.assertEqual(self.request('POST', '/v1/evaluator-previews', body, headers)[0], 403)
        self.assertEqual(self.request('POST', '/v1/evaluator-previews', body, self.browser_headers())[0], 202)

    def test_health_pagination_and_csv_contract(self):
        self.assertEqual(self.request('GET', '/v1/health')[1]['schema_version'], SCHEMA_VERSION)
        self.store.add_case(self.did, {'text': '中文,quoted'}, {'a': 1})
        headers = {'Authorization': 'Bearer '+self.admin}
        status, data, out = self.request('GET', f'/v1/datasets/{self.did}/export?format=csv', headers=headers)
        self.assertEqual(status, 200); self.assertIn('attachment', out['Content-Disposition'])
        other = self.store.create_dataset(self.pid, 'CSV')['id']
        # Exported UTF-8 BOM is accepted by the import path.
        self.assertEqual(self.store.import_cases(other, csv_text=data.decode('utf-8'))['imported'], 1)
        self.assertEqual(self.request('GET', '/v1/datasets?limit=0', headers=headers)[0], 400)

    def test_legacy_revision_warning_and_no_unexpected_json_shapes(self):
        case = self.store.add_case(self.did, {})
        headers = {'Authorization': 'Bearer '+self.admin}
        status, _, out = self.request('POST', f'/v1/cases/{case["id"]}/revisions', {'input': {}}, headers)
        self.assertEqual(status, 201); self.assertIn('base_revision', out['Warning'])
        self.assertEqual(self.request('POST', '/v1/projects', [], headers)[0], 400)


if __name__ == '__main__':
    unittest.main()
