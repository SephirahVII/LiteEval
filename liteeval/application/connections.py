"""Project-scoped immutable endpoints and encrypted, replaceable provider credentials."""
import os
from urllib.parse import urlsplit
from cryptography.fernet import Fernet, InvalidToken
from liteeval.infrastructure.security.secrets import checked_url, register_secret
from liteeval.domain.values import now, uid
from liteeval.domain.values import pagination


def cipher():
    value = os.getenv('LITEEVAL_SECRET_KEY', '')
    try:return Fernet(value.encode('ascii'))
    except (ValueError, UnicodeError):
        raise ValueError('Set LITEEVAL_SECRET_KEY to a Fernet key on both API and Worker') from None


def encrypt(key):
    if not isinstance(key, str) or not 1 <= len(key) <= 16384 or '\r' in key or '\n' in key:
        raise ValueError('api_key must be a nonempty string without newlines (max 16384)')
    return cipher().encrypt(key.encode()).decode('ascii')


def public(row):
    return {k: row[k] for k in ('id', 'project_id', 'name', 'kind', 'endpoint', 'model',
            'key_version', 'archived_at', 'created_at', 'updated_at')} | {'has_api_key': bool(row['key_ciphertext'])}


def get(store, connection_id):
    with store.connect() as db:return public(store.required(db, 'model_connections', connection_id))


def create(store, body):
    if set(body)-{'project_id', 'name', 'kind', 'endpoint', 'model', 'api_key'}:
        raise ValueError('Unsupported model connection field')
    name = body['name'];kind = body['kind'];endpoint = body['endpoint'];model = body.get('model')
    if not isinstance(name, str) or not name.strip() or len(name) > 200:raise ValueError('Connection name required (max 200)')
    if kind not in {'http_agent', 'openai_compatible'}:raise ValueError('kind must be http_agent or openai_compatible')
    checked_url(endpoint)
    if urlsplit(endpoint).query:raise ValueError('Save endpoint without query parameters; put credentials in api_key')
    if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 200):raise ValueError('Invalid model')
    if kind == 'openai_compatible' and not model:raise ValueError('openai_compatible requires model')
    encrypted = encrypt(body['api_key']) if body.get('api_key') is not None else None
    connection_id = uid();stamp = now()
    with store.connect() as db:
        store.required(db, 'projects', body['project_id'])
        db.execute('INSERT INTO model_connections VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (connection_id, body['project_id'], name.strip(), kind, endpoint.rstrip('/') if kind == 'openai_compatible' else endpoint, model,
                    encrypted, int(encrypted is not None), None, stamp, stamp))
    return get(store, connection_id)


def update(store, connection_id, body):
    if not body or set(body)-{'name', 'api_key', 'archived'}:
        raise ValueError('Update name, api_key or archived; create a new connection for endpoint/model changes')
    encrypted = encrypt(body['api_key']) if 'api_key' in body and body['api_key'] is not None else None
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = dict(store.required(db, 'model_connections', connection_id))
        if 'name' in body:
            if not isinstance(body['name'], str) or not body['name'].strip() or len(body['name']) > 200:raise ValueError('Invalid name')
            row['name'] = body['name'].strip()
        if 'archived' in body:
            if type(body['archived']) is not bool:raise ValueError('archived must be boolean')
            row['archived_at'] = now() if body['archived'] else None
        if 'api_key' in body:
            row['key_ciphertext'] = encrypted;row['key_version'] += 1
        db.execute('UPDATE model_connections SET name=?,key_ciphertext=?,key_version=?,archived_at=?,updated_at=? WHERE id=?',
                   (row['name'], row['key_ciphertext'], row['key_version'], row['archived_at'], now(), connection_id))
    return get(store, connection_id)


def list_connections(store, project_id, limit=50, offset=0, archived='exclude'):
    limit, offset = pagination(limit, offset)
    if archived not in {'exclude', 'include', 'only'}:raise ValueError('Invalid archived filter')
    where = 'project_id=?' + ('' if archived == 'include' else ' AND archived_at IS '+('NOT NULL' if archived == 'only' else 'NULL'))
    with store.connect() as db:
        store.required(db, 'projects', project_id)
        total = db.execute('SELECT COUNT(*) FROM model_connections WHERE '+where, (project_id,)).fetchone()[0]
        rows = db.execute('SELECT * FROM model_connections WHERE '+where+' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (project_id, limit, offset))
        return {'items': [public(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


def bind(db, store, project_id, config, judge=False):
    """Freeze the endpoint/model into a revision; keep only a credential reference."""
    config = dict(config)
    if 'connection_id' not in config:return config
    row = store.required(db, 'model_connections', config['connection_id'])
    if row['project_id'] != project_id:raise ValueError('Connection belongs to a different Project')
    store.active(row)
    if config.get('auth') or config.get('headers_env') or config.get('api_key_env'):
        raise ValueError('Use connection_id or environment credentials, not both')
    if judge:
        if row['kind'] != 'openai_compatible':raise ValueError('Judge needs an openai_compatible connection')
        values = {'base_url': row['endpoint'], 'model': row['model']}
    else:
        if config.get('type') != 'http':raise ValueError('Saved connections currently support HTTP Agents and LLM Judges')
        values = {'endpoint': row['endpoint']}
        if row['kind'] == 'openai_compatible':
            values = {'endpoint': row['endpoint']+'/chat/completions', 'model': row['model']}
            config.setdefault('request_template', {'model': row['model'], 'messages': [{'role': 'user', 'content': '$.input.prompt'}]})
            config.setdefault('output_path', '$.choices[0].message.content')
    for key, value in values.items():
        if key in config and config[key] != value:raise ValueError(f'{key} must match the saved connection')
        config[key] = value
    return config


def api_key(store, connection_id):
    if store is None:raise ValueError('Saved connections require a Store in the executing Worker')
    with store.connect() as db:row = store.required(db, 'model_connections', connection_id)
    store.active(row)
    if row['key_ciphertext'] is None:return None
    try:key = cipher().decrypt(row['key_ciphertext'].encode()).decode()
    except InvalidToken:raise ValueError('Unable to decrypt connection; check Worker LITEEVAL_SECRET_KEY') from None
    return register_secret(key)


def check_ready(db, store, project_id, config):
    if not config.get('connection_id'):return
    row = store.required(db, 'model_connections', config['connection_id'])
    if row['project_id'] != project_id:raise ValueError('Connection belongs to a different Project')
    store.active(row)
