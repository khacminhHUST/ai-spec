-- Schema theo docs/ingestion.md (Schema đầu ra) + docs/query-pipeline.md (conversations).
-- Chạy trong search_path = namespace (shared/migrate.py).

CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,
    doc_code      TEXT,
    project_id    TEXT,
    building      TEXT,
    stream        TEXT CHECK (stream IN ('LEGAL_STANDARD','OPERATOR_STANDARD','DESIGN_CONSULTANT','CONTRACT')),
    disciplines   TEXT[] NOT NULL DEFAULT '{}',
    title         TEXT,
    issuer        TEXT,
    revision      TEXT,
    issue_date    DATE,
    stage         TEXT,
    languages     TEXT[] NOT NULL DEFAULT '{}',
    is_bilingual  BOOLEAN NOT NULL DEFAULT FALSE,
    is_latest     BOOLEAN NOT NULL DEFAULT TRUE,
    source_path   TEXT NOT NULL,
    file_hash     TEXT NOT NULL UNIQUE,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    warnings      TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS documents_doc_code_idx ON documents (doc_code);
CREATE INDEX IF NOT EXISTS documents_project_idx ON documents (project_id, stream, is_latest);

CREATE TABLE IF NOT EXISTS parent_chunks (
    parent_id           TEXT PRIMARY KEY,
    doc_id              TEXT NOT NULL REFERENCES documents (doc_id) ON DELETE CASCADE,
    section_key         TEXT NOT NULL,
    parent_section_key  TEXT,
    lang                TEXT NOT NULL CHECK (lang IN ('vi','en')),
    type                TEXT NOT NULL CHECK (type IN ('SECTION','TABLE')),
    number              TEXT,
    title               TEXT,
    breadcrumb          TEXT NOT NULL DEFAULT '',
    content_md          TEXT NOT NULL,
    page_start          INT,
    page_end            INT,
    referenced_ids      TEXT[] NOT NULL DEFAULT '{}',
    token_count         INT NOT NULL DEFAULT 0,
    UNIQUE (doc_id, lang, section_key)
);
CREATE INDEX IF NOT EXISTS parent_chunks_section_idx ON parent_chunks (doc_id, section_key);
CREATE INDEX IF NOT EXISTS parent_chunks_refs_idx ON parent_chunks USING GIN (referenced_ids);

-- alias_norm = shared.keys.normalize_alias(alias); building = '' khi alias của cả dự án
CREATE TABLE IF NOT EXISTS project_aliases (
    alias_norm  TEXT NOT NULL,
    alias       TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    building    TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL CHECK (kind IN ('project','building','owner')),
    PRIMARY KEY (alias_norm, project_id, building)
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id  TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    title            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_user_idx ON conversations (user_id, updated_at DESC);
