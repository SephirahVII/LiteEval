"""Additive v1 -> v2 migration. Existing snapshots and completed results are retained."""
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

def migrate(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > 2:
        raise ValueError('Database was created by a newer LiteEval version')
    db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),number INTEGER NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,error TEXT,trace_id TEXT,artifact_ids_json TEXT NOT NULL DEFAULT '[]',UNIQUE(run_id,number))")
    for table, columns in COLUMNS.items():
        current = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        for name, definition in columns.items():
            if name not in current:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    db.executescript(DDL)
    db.execute('PRAGMA user_version=2')
