"""Native benchmark schema, installed transactionally by the database migration."""
BENCHMARK_DDL = """
CREATE TABLE IF NOT EXISTS benchmark_releases (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,
 version TEXT NOT NULL,digest TEXT NOT NULL,manifest TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(project_id,name,version));
CREATE TABLE IF NOT EXISTS harness_revisions (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,
 role TEXT NOT NULL,digest TEXT NOT NULL,spec TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(project_id,name,digest));
CREATE TABLE IF NOT EXISTS benchmark_executions (
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),plan TEXT NOT NULL,
 status TEXT NOT NULL,result TEXT,created_at TEXT NOT NULL,finished_at TEXT);
CREATE TABLE IF NOT EXISTS benchmark_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,execution_id TEXT NOT NULL REFERENCES benchmark_executions(id),
 kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
"""
