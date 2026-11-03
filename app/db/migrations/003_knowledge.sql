-- Quản lý kho tri thức (docs/knowledge-admin.md mục 4). Không đổi bảng cũ.
-- Seed projects / project_buildings từ config/projects.yaml: app/db/migrate.py (lần migrate đầu).

CREATE TABLE IF NOT EXISTS projects (
    project_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    owner_aliases       TEXT[] NOT NULL DEFAULT '{}',
    source_priority     TEXT[] NOT NULL DEFAULT '{}',
    governing_language  TEXT NOT NULL DEFAULT 'vi' CHECK (governing_language IN ('vi','en')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_buildings (
    project_id  TEXT NOT NULL REFERENCES projects (project_id) ON DELETE CASCADE,
    building    TEXT NOT NULL,
    aliases     TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (project_id, building)
);

CREATE TABLE IF NOT EXISTS ingest_jobs (
    job_id           TEXT PRIMARY KEY,
    file_name        TEXT NOT NULL,
    file_hash        TEXT NOT NULL,
    storage_key      TEXT NOT NULL,
    size             BIGINT NOT NULL DEFAULT 0,
    project_id       TEXT NOT NULL,
    building         TEXT NOT NULL,
    stream           TEXT NOT NULL CHECK (stream IN ('LEGAL_STANDARD','OPERATOR_STANDARD','DESIGN_CONSULTANT','CONTRACT')),
    disciplines      TEXT[] NOT NULL DEFAULT '{}',
    doc_type         TEXT NOT NULL,
    revision         TEXT,
    issue_date       DATE,
    stage            TEXT,
    title            TEXT,
    issuer           TEXT,
    series_doc_code  TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'QUEUED'
                     CHECK (status IN ('QUEUED','PROCESSING','DRAFT','PUBLISHING','PUBLISHED','FAILED','CANCELLED')),
    step             TEXT,
    report           JSONB,
    warnings         TEXT[] NOT NULL DEFAULT '{}',
    error            TEXT,
    artifact_dir     TEXT,
    doc_id           TEXT,
    uploaded_by      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by     TEXT,
    published_at     TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ingest_jobs_status_idx ON ingest_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS ingest_jobs_doc_idx ON ingest_jobs (doc_id);
-- 1 file chỉ có 1 job còn hiệu lực (chưa PUBLISHED/CANCELLED)
CREATE UNIQUE INDEX IF NOT EXISTS ingest_jobs_active_hash_idx ON ingest_jobs (file_hash)
    WHERE status IN ('QUEUED','PROCESSING','DRAFT','PUBLISHING','FAILED');
