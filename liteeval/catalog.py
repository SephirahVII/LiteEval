"""Immutable dataset releases and evaluator revision history."""
import json
from .config import validate_evaluator
from .storage import Conflict, digest, encoded, now, uid


def pagination(limit=50, offset=0):
    limit, offset = int(limit), int(offset)
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError('Pagination limit must be 1..200; offset >= 0')
    return limit, offset


def source(db, store, dataset_id=None, dataset_version_id=None):
    if bool(dataset_id) == bool(dataset_version_id):
        raise ValueError('Provide exactly one of dataset_id or dataset_version_id')
    version = store.required(db, 'dataset_versions', dataset_version_id) if dataset_version_id else None
    dataset = store.required(db, 'datasets', version['dataset_id'] if version else dataset_id)
    store.active(dataset)
    if version:
        rows = db.execute('SELECT s.case_id id,s.revision,s.content_hash,r.content_json FROM snapshot_cases s '
                          'JOIN case_revisions r ON r.case_id=s.case_id AND r.revision=s.revision '
                          'WHERE s.snapshot_id=? ORDER BY s.ordinal', (version['snapshot_id'],)).fetchall()
    else:
        rows = db.execute('SELECT c.id,c.revision,r.content_hash,r.content_json FROM cases c '
                          'JOIN case_revisions r ON r.case_id=c.id AND r.revision=c.revision '
                          'WHERE c.dataset_id=? AND c.archived_at IS NULL ORDER BY c.id', (dataset['id'],)).fetchall()
    return dataset, version, rows


def snapshot_hash(rows):
    return digest([(r['id'], r['revision'], r['content_hash']) for r in rows])


def freeze(db, dataset_id, rows):
    snapshot_id = uid()
    db.execute('INSERT INTO dataset_snapshots VALUES (?,?,?,?)', (snapshot_id, dataset_id, snapshot_hash(rows), now()))
    db.executemany('INSERT INTO snapshot_cases VALUES (?,?,?,?,?)',
                   [(snapshot_id, r['id'], r['revision'], r['content_hash'], i) for i, r in enumerate(rows)])
    return snapshot_id


def publish_dataset(store, dataset_id, name, description=''):
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError('Version name required (max 200)')
    if not isinstance(description, str) or len(description) > 2000:
        raise ValueError('Version description must contain at most 2000 characters')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        dataset, _, rows = source(db, store, dataset_id=dataset_id)
        if not rows:
            raise ValueError('Cannot publish an empty Dataset')
        if db.execute('SELECT 1 FROM dataset_versions WHERE dataset_id=? AND name=?', (dataset_id, name.strip())).fetchone():
            raise Conflict('Dataset version name already exists')
        snapshot_id = freeze(db, dataset_id, rows)
        version_id = uid()
        db.execute('INSERT INTO dataset_versions VALUES (?,?,?,?,?,?,?)',
                   (version_id, dataset['project_id'], dataset_id, name.strip(), description, snapshot_id, now()))
    return get_version(store, version_id)


VERSION_SELECT = '''SELECT v.*,s.snapshot_hash,
    (SELECT COUNT(*) FROM snapshot_cases c WHERE c.snapshot_id=v.snapshot_id) case_count
    FROM dataset_versions v JOIN dataset_snapshots s ON s.id=v.snapshot_id'''


def get_version(store, version_id):
    with store.connect() as db:
        store.required(db, 'dataset_versions', version_id)
        return dict(db.execute(VERSION_SELECT+' WHERE v.id=?', (version_id,)).fetchone())


def list_versions(store, dataset_id, limit=50, offset=0):
    limit, offset = pagination(limit, offset)
    with store.connect() as db:
        db.execute('BEGIN')
        store.required(db, 'datasets', dataset_id)
        total = db.execute('SELECT COUNT(*) FROM dataset_versions WHERE dataset_id=?', (dataset_id,)).fetchone()[0]
        rows = db.execute(VERSION_SELECT+' WHERE v.dataset_id=? ORDER BY v.created_at DESC,v.id DESC LIMIT ? OFFSET ?', (dataset_id, limit, offset))
        return {'items': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset}


def version_cases(store, version_id, limit=50, offset=0):
    limit, offset = pagination(limit, offset)
    with store.connect() as db:
        db.execute('BEGIN')
        version = store.required(db, 'dataset_versions', version_id)
        total = db.execute('SELECT COUNT(*) FROM snapshot_cases WHERE snapshot_id=?', (version['snapshot_id'],)).fetchone()[0]
        rows = db.execute('SELECT r.content_json FROM snapshot_cases s JOIN case_revisions r '
                          'ON r.case_id=s.case_id AND r.revision=s.revision WHERE s.snapshot_id=? '
                          'ORDER BY s.ordinal LIMIT ? OFFSET ?', (version['snapshot_id'], limit, offset))
        return {'items': [json.loads(r[0]) for r in rows], 'total': total, 'limit': limit, 'offset': offset}


def revise_evaluator(store, revision_id, config, base_revision, name=None):
    if type(base_revision) is not int or base_revision < 1:
        raise ValueError('base_revision must be a positive integer')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        original = store.required(db, 'evaluator_revisions', revision_id)
        store.active(original)
        latest = db.execute('SELECT * FROM evaluator_revisions WHERE evaluator_id=? ORDER BY revision DESC LIMIT 1', (original['evaluator_id'],)).fetchone()
        if base_revision != latest['revision']:
            raise Conflict(f'Evaluator changed; current revision is {latest["revision"]}')
        store.active(latest)
        label = latest['name'] if name is None else name
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ValueError('Evaluator name required (max 200)')
        if not isinstance(config, dict):
            raise ValueError('Evaluator config must be an object')
        from .connections import bind
        if 'connection_id' in config and latest['kind'] != 'llm_judge':raise ValueError('Only LLM Judges use model connections')
        config = bind(db, store, latest['project_id'], config, judge=True)
        validate_evaluator(latest['kind'], config)
        new_id = uid()
        db.execute('INSERT INTO evaluator_revisions (id,project_id,name,kind,config_json,config_hash,evaluator_id,revision) VALUES (?,?,?,?,?,?,?,?)',
                   (new_id, latest['project_id'], label, latest['kind'], encoded(config), digest([latest['kind'], config]),
                    latest['evaluator_id'], latest['revision']+1))
        return dict(store.required(db, 'evaluator_revisions', new_id))


def list_revisions(store, revision_id, limit=50, offset=0):
    limit, offset = pagination(limit, offset)
    with store.connect() as db:
        db.execute('BEGIN')
        revision = store.required(db, 'evaluator_revisions', revision_id)
        total = db.execute('SELECT COUNT(*) FROM evaluator_revisions WHERE evaluator_id=?', (revision['evaluator_id'],)).fetchone()[0]
        rows = db.execute('SELECT * FROM evaluator_revisions WHERE evaluator_id=? ORDER BY revision DESC LIMIT ? OFFSET ?', (revision['evaluator_id'], limit, offset))
        return {'items': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset}
