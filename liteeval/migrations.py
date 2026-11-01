"""Transactional additive migrations; historical snapshots and results are retained."""
SCHEMA_VERSION = 4
DDL = '''
CREATE TABLE IF NOT EXISTS api_keys (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), key_hash TEXT NOT NULL UNIQUE,
 label TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), number INTEGER NOT NULL,
 status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, error TEXT, trace_id TEXT,
 artifact_ids_json TEXT NOT NULL DEFAULT '[]', UNIQUE(run_id,number));
CREATE TABLE IF NOT EXISTS artifacts (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), run_id TEXT,
 kind TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_experiment_idempotency ON experiments(project_id,idempotency_key)
 WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_experiment_lease ON experiments(status,lease_expires);
'''
COLUMNS = {
 'attempts': {'usage_json': "TEXT NOT NULL DEFAULT '{}'", 'cost': 'REAL', 'currency': 'TEXT'},
 'traces': {'metadata_json': "TEXT NOT NULL DEFAULT '{}'"},
 'experiments': {'config_json': "TEXT NOT NULL DEFAULT '{}'", 'lease_token': 'TEXT',
                 'lease_expires': 'REAL', 'cancel_requested': 'INTEGER NOT NULL DEFAULT 0',
                 'idempotency_key': 'TEXT', 'request_hash': 'TEXT', 'error': 'TEXT'},
 'runs': {'attempt_id': 'TEXT', 'usage_json': "TEXT NOT NULL DEFAULT '{}'", 'cost': 'REAL',
          'currency': 'TEXT', 'final_state_json': "TEXT NOT NULL DEFAULT '{}'"},
 'scores': {'evidence_json': "TEXT NOT NULL DEFAULT '[]'", 'artifact_id': 'TEXT'},
}

V3_COLUMNS = {
    'datasets': {'description': "TEXT NOT NULL DEFAULT ''", 'archived_at': 'TEXT'},
    'cases': {'archived_at': 'TEXT'},
    'evaluator_revisions': {'archived_at': 'TEXT'},
}

V3_DDL = '''
CREATE TABLE IF NOT EXISTS owner_account (
 id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS browser_sessions (
 token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, created_at REAL NOT NULL,
 last_seen REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_limits (
 client TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start REAL NOT NULL);
CREATE TABLE IF NOT EXISTS evaluator_previews (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 created_at TEXT NOT NULL, finished_at TEXT, lease_token TEXT, lease_expires REAL,
 result_json TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS idx_preview_queue ON evaluator_previews(status,lease_expires,created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON browser_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_runs_page ON runs(experiment_id,case_id,case_revision);
CREATE INDEX IF NOT EXISTS idx_cases_active ON cases(dataset_id,archived_at);
'''

V4_DDL = '''
CREATE TABLE IF NOT EXISTS evaluators (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dataset_versions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 dataset_id TEXT NOT NULL REFERENCES datasets(id), name TEXT NOT NULL, description TEXT NOT NULL,
 snapshot_id TEXT NOT NULL REFERENCES dataset_snapshots(id), created_at TEXT NOT NULL,
 UNIQUE(dataset_id,name));
CREATE TABLE IF NOT EXISTS workers (
 id TEXT PRIMARY KEY, heartbeat_at REAL NOT NULL, stopped_at REAL,
 capabilities_json TEXT NOT NULL, credential_status_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_dataset_versions ON dataset_versions(dataset_id,created_at,id);
'''

V4_COLUMNS = {
    'evaluator_revisions': {'evaluator_id': 'TEXT REFERENCES evaluators(id)',
                            'revision': 'INTEGER NOT NULL DEFAULT 1'},
    'experiments': {'dataset_version_id': 'TEXT REFERENCES dataset_versions(id)',
                    'name': "TEXT NOT NULL DEFAULT ''", 'description': "TEXT NOT NULL DEFAULT ''",
                    'diagnostic': 'INTEGER NOT NULL DEFAULT 0'},
}


def migrate(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError('Database was created by a newer LiteEval version')
    if version == SCHEMA_VERSION:
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),number INTEGER NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,error TEXT,trace_id TEXT,artifact_ids_json TEXT NOT NULL DEFAULT '[]',UNIQUE(run_id,number))")
        for group in (COLUMNS, V3_COLUMNS):
            for table, columns in group.items():
                current = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                for name, definition in columns.items():
                    if name not in current:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        # executescript commits implicitly, so execute these simple DDL statements separately.
        for statement in (DDL + V3_DDL).split(';'):
            if statement.strip():
                db.execute(statement)
        for statement in V4_DDL.split(';'):
            if statement.strip():
                db.execute(statement)
        for table, columns in V4_COLUMNS.items():
            current = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            for name, definition in columns.items():
                if name not in current:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        # Legacy revisions each become their own family; equal names are not merged.
        db.execute("INSERT INTO evaluators SELECT id,project_id,strftime('%Y-%m-%dT%H:%M:%SZ','now') FROM evaluator_revisions WHERE evaluator_id IS NULL")
        db.execute('UPDATE evaluator_revisions SET evaluator_id=id WHERE evaluator_id IS NULL')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_evaluator_revision ON evaluator_revisions(evaluator_id,revision)')
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        db.commit()
    except BaseException:
        db.rollback()
        raise
