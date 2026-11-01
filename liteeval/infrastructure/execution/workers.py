"""Worker heartbeats and advisory credential readiness without exporting secrets."""
import asyncio
import json
import os
import re
from pathlib import Path
import shutil
import time
from liteeval.domain.values import encoded, uid

ONLINE_SECONDS = 15


def required_refs(config, judge=False):
    headers = config.get('headers_env', {})
    refs = list(headers.values()) if isinstance(headers, dict) else []
    auth = config.get('auth', {})
    if isinstance(auth, dict) and auth.get('token_env'):
        refs.append(auth['token_env'])
    if config.get('api_key_env'):
        refs.append(config['api_key_env'])
    elif judge and not config.get('connection_id'):
        refs.append('LITEEVAL_JUDGE_API_KEY')
    return {r for r in refs if isinstance(r, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}', r)}


def supports(worker, agent):
    caps = worker['capabilities']
    if agent.get('environment', {}).get('type') == 'docker':
        return caps['docker_cli']
    if agent['type'] == 'http':
        return True
    return caps['codex_cli']


def workers_from_db(db):
    stamp = time.time()
    result = []
    for row in db.execute('SELECT * FROM workers ORDER BY heartbeat_at DESC,id'):
        result.append({'id': row['id'], 'heartbeat_at': row['heartbeat_at'],
                       'online': row['stopped_at'] is None and row['heartbeat_at'] > stamp-ONLINE_SECONDS,
                       'stopped_at': row['stopped_at'], 'capabilities': json.loads(row['capabilities_json']),
                       'credential_status': json.loads(row['credential_status_json'])})
    return result


def readiness(db, agent, evaluator_configs):
    refs = required_refs(agent)
    configs = [agent]+[config for kind, config in evaluator_configs if kind == 'llm_judge']
    connection_ids = {conf['connection_id'] for conf in configs if conf.get('connection_id')}
    encrypted_ids = {cid for cid in connection_ids if db.execute('SELECT key_ciphertext FROM model_connections WHERE id=?', (cid,)).fetchone()[0]}
    encrypted = bool(encrypted_ids)
    for kind, config in evaluator_configs:
        if kind == 'llm_judge':
            refs.update(required_refs(config, judge=True))
    online = [w for w in workers_from_db(db) if w['online']]
    ready = [w for w in online if supports(w, agent) and all(w['credential_status'].get(r, False) for r in refs)
             and all(w['credential_status'].get('connection:'+cid, False) for cid in encrypted_ids)]
    state = 'unknown' if not online else 'ready' if len(ready) == len(online) else 'partial' if ready else 'missing'
    return {'state': state, 'checked_on': 'worker', 'required_env': sorted(refs),
            'online_workers': len(online), 'ready_workers': len(ready),
            'connection_ids': sorted(connection_ids), 'encrypted_credentials': encrypted,
            'advisory': True, 'routing_guaranteed': False}


def status(store):
    with store.connect() as db:
        db.execute('BEGIN')
        workers = workers_from_db(db)
        queued = db.execute("SELECT COUNT(*) FROM experiments WHERE status='queued' AND cancel_requested=0").fetchone()[0]
        previews = db.execute("SELECT COUNT(*) FROM evaluator_previews WHERE status='queued'").fetchone()[0]
    return {'workers': workers, 'online_workers': sum(w['online'] for w in workers),
            'queued_experiments': queued, 'queued_previews': previews,
            'capability_checks': 'CLI availability only; authentication, Docker Engine and images need live validation'}


class WorkerRegistry:
    def __init__(self, store, worker_id=None):
        self.store = store
        self.id = worker_id or uid()

    def heartbeat(self):
        with self.store.connect() as db:
            refs = set()
            for row in db.execute('SELECT config_json FROM agent_versions'):
                refs.update(required_refs(json.loads(row[0])))
            for row in db.execute("SELECT config_json FROM evaluator_revisions WHERE kind='llm_judge' AND archived_at IS NULL"):
                refs.update(required_refs(json.loads(row[0]), judge=True))
            executable = os.getenv('LITEEVAL_CODEX_BINARY', 'codex')
            capabilities = {'http': True, 'codex_cli': bool(shutil.which(executable) or
                            executable.lower().endswith('.py') and Path(executable).is_file()),
                            'docker_cli': bool(shutil.which('docker'))}
            credential_status = {ref: bool(os.getenv(ref)) for ref in sorted(refs)}
            from liteeval.application.connections import cipher
            from cryptography.fernet import InvalidToken
            for row in db.execute('SELECT id,key_ciphertext FROM model_connections WHERE archived_at IS NULL'):
                try:
                    if row['key_ciphertext']:cipher().decrypt(row['key_ciphertext'].encode())
                    credential_status['connection:'+row['id']] = True
                except (ValueError, InvalidToken):credential_status['connection:'+row['id']] = False
            stamp = time.time()
            db.execute('DELETE FROM workers WHERE heartbeat_at<?', (stamp-86400,))
            db.execute('INSERT INTO workers VALUES (?,?,NULL,?,?) ON CONFLICT(id) DO UPDATE SET '
                       'heartbeat_at=excluded.heartbeat_at,stopped_at=NULL,capabilities_json=excluded.capabilities_json,credential_status_json=excluded.credential_status_json',
                       (self.id, stamp, encoded(capabilities), encoded(credential_status)))

    async def monitor(self):
        while True:
            await asyncio.sleep(5)
            self.heartbeat()

    def stop(self):
        with self.store.connect() as db:
            db.execute('UPDATE workers SET stopped_at=? WHERE id=?', (time.time(), self.id))
